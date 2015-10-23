import os
import sys
import datetime
import calendar
import shutil
import tempfile
from sh import git, grep
from commands.tools import GCallException, gcall, log, refresh_stage2, execute_task_on_hosts
from boto.ec2 import autoscale
import boto.s3
import base64

ROOT_PATH = os.path.dirname(os.path.realpath(__file__))

class Deploy():
    _app = None
    _job = None
    _log_file = -1
    _app_path = None
    _git_repo = None
    _dry_run = None
    _as_conn = None
    _as_group = None
    _worker = None
    _config = None

    def __init__(self, worker):
        self._app = worker.app
        self._job = worker.job
        self._log_file = worker.log_file
        self._config = worker._config
        self._worker = worker
        # FIXME Deal with multiple job modules.
        # Deal only with first (0) job module for now

    def _find_modules_by_name(self, modules):
        result = []
        for module in modules:
            if 'name' in module:
                for item in self._app['modules']:
                    if 'name' in item and item['name'] == module['name']:
                        result.append(item)
        return result

    def _get_path_from_app(self):
        return "/ghost/{name}/{env}/{role}".format(name=self._app['name'], env=self._app['env'], role=self._app['role'])

    def _get_full_clone_path_from_module(self, module):
        return self._get_buildpack_clone_path_from_module(module) + '-full'

    def _get_buildpack_clone_path_from_module(self, module):
        return "/ghost/{name}/{env}/{role}/{module}".format(name=self._app['name'], env=self._app['env'], role=self._app['role'], module=module['name'])

    def _initialize_module(self, module):
        full_clone_path = self._get_full_clone_path_from_module(module)

        try:
            shutil.rmtree(full_clone_path)
        except (OSError, IOError) as e:
            print(e)

        try:
            os.makedirs(full_clone_path)
        except:
            raise GCallException("Init module: {0} failed, creating directory".format(module['name']))

        self._worker.module_initialized(module['name'])

    def _set_as_conn(self):
        self._as_conn = autoscale.connect_to_region(self._app['region'])

    def _set_autoscale_group(self):
        if not self._as_conn:
            self._set_as_conn()
        if 'autoscale' in self._app.keys():
            if 'name' in self._app['autoscale'].keys():
                as_list = self._as_conn.get_all_groups(names=[self._app['autoscale']['name']])
                if len(as_list) == 1:
                    self._as_group = as_list[0].name
                    log("INFO: Application autoscale {0} found in EC2".format(self._app['autoscale']['name']), self._log_file)
                else:
                    log("WARNING: Application autoscale {0} not found in EC2".format(self._app['autoscale']['name']), self._log_file)
                    all_as = self._as_conn.get_all_groups()
                    if len(all_as) >0:
                        for ec2_as in all_as:
                            log("WARNING:    Autoscaling found: "+ec2_as.name+" ",self._log_file)
                    else:
                        log("WARNING: No autoscale created so far",self._log_file)
            else:
                log("WARNING: set_autoscale_group is called in an application without inialised autoscale", self._log_file)
        else:
            log("WARNING: set_autoscale_group is called in an application without inialised autoscale", self._log_file)

    def _start_autoscale(self):
        if not self._as_group:
            self._set_autoscale_group()
        if (self._as_group):
            log("Resuming autoscaling", self._log_file)
            self._as_conn.resume_processes(self._as_group)

    def _stop_autoscale(self):
        if not self._as_group:
            self._set_autoscale_group()
        if (self._as_group):
            log("Stopping autoscaling", self._log_file)
            self._as_conn.suspend_processes(self._as_group)


    def _deploy_module(self, module):
        task_name = "deploy:{0},{1}".format(self._config['bucket_s3'], module['name'])
        execute_task_on_hosts(task_name, self._app['name'], self._app['env'], self._app['role'], self._app['region'], self._config['key_path'], self._log_file)

    def _package_module(self, module, ts, commit):
        path = self._get_buildpack_clone_path_from_module(module)
        os.chdir(path)
        pkg_name = "{0}_{1}_{2}".format(ts, module['name'], commit)
        gcall("tar cvzf ../%s . > /dev/null" % pkg_name, "Creating package: %s" % pkg_name, self._log_file)
        gcall("aws --region {region} s3 cp ../{0} s3://{bucket_s3}{path}/".format(pkg_name, \
                bucket_s3=self._config['bucket_s3'], region=self._app['region'], path=path), "Uploading package: %s" % pkg_name, self._log_file)
        gcall("rm -f ../{0}".format(pkg_name), "Deleting local package: %s" % pkg_name, self._log_file)
        return pkg_name

    def _get_module_revision(self, module_name):
        for module in self._job['modules']:
            if 'name' in module and module['name'] == module_name:
                if 'rev' in module:
                    return module['rev']
                return 'master'

    def execute(self):
        self._apps_modules = self._find_modules_by_name(self._job['modules'])
        refresh_stage2(self._config['bucket_s3'], self._app['region'], self._config['ghost_root_path'])
        module_list = []
        for module in self._apps_modules:
            if 'name' in module:
                module_list.append(module['name'])
        split_comma = ', '
        module_list = split_comma.join(module_list)
        try:
            for module in self._apps_modules:
                if not module['initialized']:
                    self._initialize_module(module)

            for module in self._apps_modules:
                deploy_id = self._execute_deploy(module)
                self._worker._db.jobs.update({ '_id': self._job['_id'], 'modules.name': module['name']}, {'$set': {'modules.$.deploy_id': deploy_id }})

            self._worker.update_status("done", message="Deployment OK: [{0}: {1}]".format(module_list, deploy_id))
        except GCallException as e:
            self._worker.update_status("failed", message="Deployment Failed: [{0}]\n{1}".format(module_list, str(e)))

    def _update_manifest(self, module, package):
        key_path = self._get_path_from_app() + '/MANIFEST'
        conn = boto.s3.connect_to_region(self._app['region'])
        bucket = conn.get_bucket(self._config['bucket_s3'])
        key = bucket.get_key(key_path)
        modules = []
        module_exist = False
        all_app_modules_list = [app_module['name'] for app_module in self._app['modules'] if 'name' in app_module]
        data = ""
        if key:
            manifest = key.get_contents_as_string()
            if sys.version > '3':
                manifest = manifest.decode('utf-8')
            for line in manifest.split('\n'):
                if line:
                    mod = {}
                    tmp = line.split(':')
                    mod['name'] = tmp[0]
                    if mod['name'] == module['name']:
                        mod['package'] = package
                        mod['path'] = module['path']
                        module_exist = True
                    else:
                        mod['package'] = tmp[1]
                        mod['path'] = tmp[2]
                    # Only keep modules that have not been removed from the app
                    if mod['name'] in all_app_modules_list:
                        modules.append(mod)
        if not key:
            key = bucket.new_key(key_path)
        if not module_exist:
            modules.append({ 'name': module['name'], 'package': package, 'path': module['path']})
        for mod in modules:
            data = data + mod['name'] + ':' + mod['package'] + ':' + mod['path'] + '\n'
        manifest, manifest_path = tempfile.mkstemp()
        if sys.version > '3':
            os.write(manifest, bytes(data, 'UTF-8'))
        else:
            os.write(manifest, data)
        os.close(manifest)
        key.set_contents_from_filename(manifest_path)

    def _execute_deploy(self, module):
        """
        Returns the deployment id
        """

        now = datetime.datetime.utcnow()
        ts = calendar.timegm(now.timetuple())

        git_repo = module['git_repo']
        full_clone_path = self._get_full_clone_path_from_module(module)
        shallow_clone_path = self._get_buildpack_clone_path_from_module(module)
        revision = self._get_module_revision(module['name'])

        if not os.path.exists(full_clone_path + '/.git'):
            gcall("rm -rf {full_clone_path}".format(full_clone_path=full_clone_path), "Cleaning up full clone destination", self._log_file)
            gcall("git --no-pager clone {git_repo} {full_clone_path}".format(git_repo=git_repo, full_clone_path=full_clone_path), "Git full cloning from remote %s" % git_repo, self._log_file)

        # Update existing clone
        os.chdir(full_clone_path)
        gcall("git --no-pager reset --hard", "Resetting git repository", self._log_file)
        gcall("git --no-pager clean -f", "Cleaning git repository", self._log_file)
        gcall("git --no-pager checkout master", "Git checkout master before pull in case of detached head", self._log_file)
        gcall("git --no-pager fetch --tags", "Git fetch all tags", self._log_file)
        gcall("git --no-pager pull", "Git pull", self._log_file)
        gcall("git --no-pager checkout %s" % revision, "Git checkout: %s" % revision, self._log_file)
        gcall("grep '^ref: ' .git/HEAD && git --no-pager pull || echo HEAD is detached, no need to pull", "Git pull after checkout if not detached: %s" % revision, self._log_file)

        # Extract remote origin URL and commit information
        remote_url = grep(grep(git('--no-pager', 'remote', '--verbose'), '^origin'), '(fetch)$').split()[1]
        commit = git('--no-pager', 'rev-parse', '--short', 'HEAD').strip()
        commit_message = git('--no-pager', 'log', '--max-count=1', '--format=%s', 'HEAD').strip()

        # Shallow clone from the full clone to limit the size of the generated archive
        gcall("rm -rf {shallow_clone_path}".format(shallow_clone_path=shallow_clone_path), "Removing previous shallow clone", self._log_file)
        gcall("git --no-pager clone --recursive --depth=100 file://{full_clone_path} {shallow_clone_path}".format(full_clone_path=full_clone_path, shallow_clone_path=shallow_clone_path), "Git shallow cloning from previous clone", self._log_file)
        gcall("git --no-pager submodule update --recursive --depth=100", "Git update submodules", self._log_file)

        # chdir into newly created shallow clone and reset remote origin URL
        os.chdir(shallow_clone_path)
        git('--no-pager', 'remote', 'set-url', 'origin', remote_url)

        # Store predeploy script in tarball
        if 'pre_deploy' in module:
            predeploy_source = base64.b64decode(module['pre_deploy'])
            with open(shallow_clone_path + '/predeploy', 'w') as f:
                if sys.version > '3':
                    f.write(bytes(predeploy_source, 'UTF-8'))
                else:
                    f.write(predeploy_source)

        # Execute buildpack
        if 'build_pack' in module:
            print('Buildpack: Creating')
            buildpack_source = base64.b64decode(module['build_pack'])
            buildpack, buildpack_path = tempfile.mkstemp(dir=shallow_clone_path)
            if sys.version > '3':
                os.write(buildpack, bytes(buildpack_source, 'UTF-8'))
            else:
                os.write(buildpack, buildpack_source)
            os.close(buildpack)

            buildpack_env = os.environ.copy()
            buildpack_env['GHOST_APP'] = self._app['name']
            buildpack_env['GHOST_ENV'] = self._app['env']
            buildpack_env['GHOST_ROLE'] = self._app['role']
            buildpack_env['GHOST_MODULE_NAME'] = module['name']
            buildpack_env['GHOST_MODULE_PATH'] = module['path']

            gcall('bash %s' % buildpack_path, 'Buildpack: Execute', self._log_file, env=buildpack_env)

        # Store postdeploy script in tarball
        if 'post_deploy' in module:
            postdeploy_source = base64.b64decode(module['post_deploy'])
            with open(shallow_clone_path + '/postdeploy', 'w') as f:
                if sys.version > '3':
                    f.write(bytes(postdeploy_source, 'UTF-8'))
                else:
                    f.write(postdeploy_source)

        # Create tar archive
        pkg_name = self._package_module(module, ts, commit)

        self._set_as_conn()
        if self._app['autoscale']['name']:
            self._stop_autoscale()
        self._update_manifest(module, pkg_name)
        self._deploy_module(module)
        if self._app['autoscale']['name']:
            self._start_autoscale()
        deployment = {'app_id': self._app['_id'], 'job_id': self._job['_id'], 'module': module['name'], 'revision': revision, 'commit': commit, 'commit_message': commit_message, 'timestamp': ts, 'package': pkg_name, 'module_path': module['path']}
        return self._worker._db.deploy_histories.insert(deployment)
