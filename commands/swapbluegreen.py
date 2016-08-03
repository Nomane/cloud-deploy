from fabric.colors import green as _green, yellow as _yellow, red as _red

import time
from ghost_log import log
from ghost_tools import get_aws_connection_data, get_app_friendly_name, GCallException
from settings import cloud_connections, DEFAULT_PROVIDER

from ghost_aws import check_autoscale_exists, get_autoscaling_group_and_processes_to_suspend, suspend_autoscaling_group_processes, resume_autoscaling_group_processes
from libs.elb import deregister_all_instances_from_elb, register_all_instances_to_elb, register_elb_into_autoscale
from libs.elb import get_elb_instance_status_autoscaling_group, get_elb_instance_status, get_connection_draining_value, get_elb_dns_name
from libs.elb import get_elb_by_name, elb_configure_health_check, deregister_instance_from_elb
from libs.blue_green import get_blue_green_apps, check_app_manifest, get_blue_green_config

COMMAND_DESCRIPTION = "Swap the Blue/Green env"

class Swapbluegreen():
    _app = None
    _job = None
    _log_file = -1

    def __init__(self, worker):
        self._app = worker.app
        self._job = worker.job
        self._db = worker._db
        self._config = worker._config
        self._worker = worker
        self._log_file = worker.log_file
        self._connection_data = get_aws_connection_data(
                self._app.get('assumed_account_id', ''),
                self._app.get('assumed_role_name', ''),
                self._app.get('assumed_region_name', '')
                )
        self._cloud_connection = cloud_connections.get(self._app.get('provider', DEFAULT_PROVIDER))(
                self._log_file,
                **self._connection_data
                )

    def _get_notification_message_failed(self, online_app, to_deploy_app, msg):
        app_name = get_app_friendly_name(online_app)
        notif = "Blue/green swap failed for [{0}] between [{1}] and [{2}]: {3}".format(app_name, online_app['_id'], to_deploy_app['_id'], msg)
        return _red(notif)

    def _get_notification_message_aborted(self, app, msg):
        notif = "Blue/green swap aborted for [{0}] : {1}".format(get_app_friendly_name(app), msg)
        return _yellow(notif)

    def _get_notification_message_done(self, online_app, as_old, as_new, elb_name, elb_dns):
        app_name = get_app_friendly_name(online_app)
        notif = "Blue/green swap done for [{0}] between [{1}] and [{2}] on ELB '{3}' ({4})".format(app_name, as_old, as_new, elb_name, elb_dns)
        return _green(notif)

    def _update_app_is_online(self, app, is_online):
        """ Updates the App DB object to set the 'is_online' attribute. This attribute should be at True when the ASG is mapped with the online ELB.
        """
        self._worker._db.apps.update({ '_id': app['_id']}, {'$set': {'blue_green.is_online': is_online }})
        log("'{0}' has been set '{1}' for blue/green".format(app['_id'], 'online' if is_online else 'offline'), self._log_file)

    def _wait_draining_connection(self, elb_conn, elb_names):
        """ Wait until the connection draining is reached.

            elb_conn    boto2 obj   Connection object to the ELB endpoint.
            elb_names    List        A list of ELB names.
            Return      None
        """
        wait_before_swap = int(get_connection_draining_value(elb_conn, elb_names)) + 1
        log(_green('Waiting {0}s: The ELB connection draining time' .format(wait_before_swap)), self._log_file)
        time.sleep(wait_before_swap)

    def _wait_until_instances_registered(self, elb_conn, elb_names, timeout):
        """ Wait until each instances become online in the Load Balancer.
            If timeout value is reached, raise an exception

            elb_conn    boto2 obj   Connection object to the ELB endpoint.
            elb_names   list        A list of ELB names.
            timeout     int         Maximum time to wait before the instances become healthy.
            return      None
        """
        t = 0
        while len([i for i in get_elb_instance_status(elb_conn, elb_names).values() if 'outofservice' in i.values()]):
            if t > timeout: raise Exception
            log(_yellow('Waiting 10s because the instance is not in service in the ELB'), self._log_file)
            time.sleep(10)
            t += 10

    def _update_health_check(self, elb_conn3, elb_name, health_check_config, hc_params, action):
        """ Update the ELB Health Check parameter

            elb_conn3             boto3 obj  Connection object to the ELB endpoint.
            elb_name              string     The name of the ELB to modify
            health_check_config   dict       The informations about the current health check configuration of the ELB.
            hc_params             dict       The parameters of the Health check config(Ghost side).
            action                string     Two possibilities, reduce(to set the Health Check interval to the minimum) or
                                                                    restore(to set the "default(predeploy)" check interval)
            return Bool True if the operation succeed, False otherwise
        """
        if action == 'reduce':
            log(_green('Changing HealthCheck to be "minimal" on online ELB "{0}"'.format(elb_name)), self._log_file)
            elb_configure_health_check(elb_conn3, elb_name,
                health_check_config['Target'], hc_params['hc_interval'], hc_params['hc_timeout'], health_check_config['UnhealthyThreshold'], hc_params['hc_healthy_threshold'])
        elif action == 'restore':
            log(_green('Restoring original HealthCheck config on online ELB "{0}"'.format(elb_name)), self._log_file)
            elb_configure_health_check(elb_conn3, elb_name, health_check_config['Target'], health_check_config['Interval'],
                         health_check_config['Timeout'], health_check_config['UnhealthyThreshold'], health_check_config['HealthyThreshold'])
        else:
            log(_red('Unsupported Update Health Check operation. Currently, only reduce or restore are supported'), self._log_file)
            return False
        return True


    def _swap_asg(self, swap_execution_strategy, online_app, to_deploy_app, config, log_file):
        """ Swap group of instances from A to B atatched to the main ELB

        :param  swap_execution_strategy  string: The swap strategy which can be 'isolated' or 'bothversion'
        :param  online_app Ghost app object: ASG instances to de-register
        :param  to_deploy_app Ghost app object: ASG instances to register
        :param  config: Ghost config
        :param  log_file
        :return tuple (Main ELB name, Main ELB dns)
        """
        app_region = self._app['region']
        as_conn = self._cloud_connection.get_connection(app_region, ["ec2", "autoscale"])
        as_conn3 = self._cloud_connection.get_connection(app_region, ['autoscaling'], boto_version='boto3')
        elb_conn = self._cloud_connection.get_connection(app_region, ["ec2", "elb"])
        elb_conn3 = self._cloud_connection.get_connection(app_region, ['elb'], boto_version='boto3')

        # Retrieve autoscaling infos, if any
        as_group_old, as_group_old_processes_to_suspend = get_autoscaling_group_and_processes_to_suspend(as_conn, online_app, log_file)
        as_group_new, as_group_new_processes_to_suspend = get_autoscaling_group_and_processes_to_suspend(as_conn, to_deploy_app, log_file)
        # Retrieve ELB instances
        elb_online_instances = get_elb_instance_status_autoscaling_group(elb_conn, online_app['autoscale']['name'], as_conn)
        elb_tempwarm_instances = get_elb_instance_status_autoscaling_group(elb_conn, to_deploy_app['autoscale']['name'], as_conn)

        try:
            # Suspend autoscaling groups
            suspend_autoscaling_group_processes(as_conn, as_group_old, as_group_old_processes_to_suspend, log_file)
            suspend_autoscaling_group_processes(as_conn, as_group_new, as_group_new_processes_to_suspend, log_file)

            # Retrieve online ELB object
            elb_online = get_elb_by_name(elb_conn3, elb_online_instances.keys()[0])
            health_check_config = elb_online['HealthCheck']

            hc_params = {'hc_interval': get_blue_green_config(self._config, 'swapbluegreen', 'healthcheck_interval', 5),
                         'hc_timeout': get_blue_green_config(self._config, 'swapbluegreen', 'healthcheck_timeout', 2),
                         'registration_timeout': get_blue_green_config(self._config, 'swapbluegreen', 'registreation_timeout', 45),
                         'hc_healthy_threshold': get_blue_green_config(self._config, 'swapbluegreen', 'healthcheck_healthy_threshold', 2)}

            log("Swapping using strategy '{0}'".format(swap_execution_strategy), self._log_file)
            self._update_health_check(elb_conn3, elb_online['LoadBalancerName'], health_check_config, hc_params, 'reduce')
            if swap_execution_strategy == 'isolated':
                log(_green('De-register all online instances from ELB {0}'.format(', '.join(elb_online_instances.keys()))), self._log_file)
                deregister_all_instances_from_elb(elb_conn, elb_online_instances, self._log_file)
                self._wait_draining_connection(elb_conn, elb_online_instances.keys())
                log(_green('Register and put online new instances to online ELB {0}'.format(', '.join(elb_online_instances.keys()))), self._log_file)
                register_all_instances_to_elb(elb_conn, elb_online_instances.keys(), elb_tempwarm_instances, self._log_file)
            elif swap_execution_strategy == 'overlap':
                log(_green('De-register old instances from ELB {0}'.format(', '.join(elb_online_instances.keys()))), self._log_file)
                deregister_all_instances_from_elb(elb_conn, elb_online_instances, self._log_file)
                log(_green('Register new instances in the ELB: {0}' .format(elb_online['LoadBalancerName'])), self._log_file)
                register_all_instances_to_elb(elb_conn, elb_online_instances.keys(), elb_tempwarm_instances, self._log_file)
            else:
                log("Invalid swap execution strategy selected : '{0}'. Please choose between 'isolated' and 'overlap'".format(swap_execution_strategy), self._log_file)
                return None, None
            try:
                self._wait_until_instances_registered(elb_conn, elb_online_instances.keys(), hc_params['registration_timeout'])
            except:
                log(_red("Timeout reached while waiting the instances registration. Rollback process launch"), self._log_file)
                deregister_instance_from_elb(elb_conn, elb_online_instances.keys(), elb_tempwarm_instances[elb_tempwarm_instances.keys()[0]].keys(), self._log_file)
                register_all_instances_to_elb(elb_conn, elb_online_instances.keys(), elb_online_instances, self._log_file)
                register_all_instances_to_elb(elb_conn, elb_tempwarm_instances.keys(), elb_tempwarm_instances, self._log_file)
                log(_yellow("Rollback completed."), self._log_file)
                return None, None

            log(_green('De-register all instances from temp (warm) ELB {0}'.format(', '.join(elb_tempwarm_instances.keys()))), self._log_file)
            deregister_all_instances_from_elb(elb_conn, elb_tempwarm_instances, self._log_file)

            log(_green('Register old instances to Temp ELB {0} (usefull for another Rollback Swap)'.format(', '.join(elb_tempwarm_instances.keys()))), self._log_file)
            register_all_instances_to_elb(elb_conn, elb_tempwarm_instances.keys(), elb_online_instances, self._log_file)

            log(_green('Update autoscale groups with their new ELB'), self._log_file)
            register_elb_into_autoscale(to_deploy_app['autoscale']['name'], as_conn3, elb_tempwarm_instances.keys(), elb_online_instances.keys(), self._log_file)
            register_elb_into_autoscale(online_app['autoscale']['name'], as_conn3, elb_online_instances.keys(), elb_tempwarm_instances.keys(), self._log_file)
            self._update_health_check(elb_conn3, elb_online['LoadBalancerName'], health_check_config, hc_params, 'restore')

            # Update _is_online field in DB on both app
            self._update_app_is_online(online_app, False) # no more online anymore
            self._update_app_is_online(to_deploy_app, True) # promotion !

            online_elb_name = elb_online_instances.keys()[0]
            return str(online_elb_name), get_elb_dns_name(elb_conn, online_elb_name)
        finally:
            resume_autoscaling_group_processes(as_conn, as_group_old, as_group_old_processes_to_suspend, log_file)
            resume_autoscaling_group_processes(as_conn, as_group_new, as_group_new_processes_to_suspend, log_file)

    def execute(self):
        log(_green("STATE: Started"), self._log_file)
        swap_execution_strategy = self._job['options'][0] if 'options' in self._job and len(self._job['options']) > 0 else "isolated"
        online_app, to_deploy_app = get_blue_green_apps(self._app,
                                                        self._worker._db.apps,
                                                        self._log_file)
        if not online_app:
            self._worker.update_status("aborted", message=self._get_notification_message_aborted(self._app, "Blue/green is not enabled on this app or not well configured"))
            return
        try:
            # Check AMI
            if 'ami' not in to_deploy_app:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(to_deploy_app, "Please run `Buildimage` first"))
                return
            # Check if modules have been deployed
            if not check_app_manifest(to_deploy_app, self._config, self._log_file):
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(to_deploy_app, "Please deploy your app's modules"))
                return
            # Check ASG
            if to_deploy_app['autoscale']['name'] and online_app['autoscale']['name']:
                if not (check_autoscale_exists(self._cloud_connection, to_deploy_app['autoscale']['name'], to_deploy_app['region'])
                    and check_autoscale_exists(self._cloud_connection, online_app['autoscale']['name'], online_app['region'])):
                    self._worker.update_status("aborted", message=self._get_notification_message_aborted(to_deploy_app, "Please set an AutoScale on both green and blue app"))
                    return
            else:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(to_deploy_app, "Please set an AutoScale on both green and blue app."))
                return

            # Check if we have two different AS !
            if to_deploy_app['autoscale']['name'] == online_app['autoscale']['name']:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(to_deploy_app, "Please set a different AutoScale on green and blue app."))
                return

            # Check if we're ready to swap. If an instance is out of service
            # into the ELB pool raise an exception
            app_region = self._app['region']
            as_conn = self._cloud_connection.get_connection(app_region, ["ec2", "autoscale"])
            elb_conn = self._cloud_connection.get_connection(app_region, ["ec2", "elb"])

            elb_instances = get_elb_instance_status_autoscaling_group(elb_conn, to_deploy_app['autoscale']['name'], as_conn)
            if len([i for i in elb_instances.values() if 'outofservice' in i.values()]):
                raise GCallException('Cannot continue because one or more instances are in the out of service state in the temp ELB')
            else:
                log(_green("AutoScale blue [{0}] and green [{1}] ready for swap".format(online_app['autoscale']['name'], to_deploy_app['autoscale']['name'])), self._log_file)

            # Swap !
            elb_name, elb_dns = self._swap_asg(swap_execution_strategy, online_app, to_deploy_app, self._config, self._log_file)
            if not elb_name:
                self._worker.update_status("failed", message=self._get_notification_message_failed(online_app, to_deploy_app, 'Unable to make blue-green swap'))
                return

            # All good
            done_notif = self._get_notification_message_done(online_app, online_app['autoscale']['name'], to_deploy_app['autoscale']['name'], elb_name, elb_dns)
            self._worker.update_status("done", message=done_notif)
        except GCallException as e:
            self._worker.update_status("failed", message=self._get_notification_message_failed(online_app, to_deploy_app, str(e)))