import os
import sys
import tempfile
from bson.objectid import ObjectId

from ghost_tools import GCallException, gcall, get_app_module_name_list, clean_local_module_workspace
from ghost_tools import get_aws_connection_data
from settings import cloud_connections, DEFAULT_PROVIDER
from ghost_log import log
from ghost_aws import deploy_module_on_hosts
from libs.deploy import execute_module_script_on_ghost

COMMAND_DESCRIPTION = "Re-deploy an old module package"

class Redeploy():
    _app = None
    _job = None
    _log_file = -1
    _config = None

    def __init__(self, worker):
        self._app = worker.app
        self._job = worker.job
        self._db = worker._db
        self._worker = worker
        self._config = worker._config
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

    def _get_clone_path_from_module(self, module):
        """
        >>> class worker:
        ...     app = {'name': 'AppName', 'env': 'prod', 'role': 'webfront'}
        ...     job = None
        ...     log_file = None
        ...     _config = None
        >>> module = {'name': 'mod1', 'git_repo': 'git@bitbucket.org:morea/ghost.git'}
        >>> Redeploy(worker=worker())._get_clone_path_from_module(module)
        '/ghost/AppName/prod/webfront/mod1'
        """
        return "{app_path}/{module}/.redeploy".format(app_path=self._get_path_from_app(), module=module['name'])

    def _update_manifest(self, module, package):
        key_path = self._get_path_from_app() + '/MANIFEST'
        cloud_connection = cloud_connections.get(self._app.get('provider', DEFAULT_PROVIDER))(self._log_file)
        conn = cloud_connection.get_connection(self._config.get('bucket_region', self._app['region']), ["s3"])
        bucket = conn.get_bucket(self._config['bucket_s3'])
        key = bucket.get_key(key_path)
        modules = []
        module_exist = False
        all_app_modules_list = get_app_module_name_list(self._app['modules'])
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
                        mod['index'] = all_app_modules_list.index(mod['name'])
                        modules.append(mod)
        if not key:
            key = bucket.new_key(key_path)
        if not module_exist:
            modules.append({
                'name': module['name'],
                'package': package,
                'path': module['path'],
                'index': all_app_modules_list.index(module['name'])
            })
        for mod in sorted(modules, key=lambda mod: mod['index']):
            data = data + mod['name'] + ':' + mod['package'] + ':' + mod['path'] + '\n'
        manifest, manifest_path = tempfile.mkstemp()
        if sys.version > '3':
            os.write(manifest, bytes(data, 'UTF-8'))
        else:
            os.write(manifest, data)
        os.close(manifest)
        key.set_contents_from_filename(manifest_path)

    def _get_deploy_infos(self, deploy_id):
        deploy_infos = self._db.deploy_histories.find_one({'_id': ObjectId(deploy_id)})
        if deploy_infos:
            module = {}
            module['path'] = deploy_infos['module_path']
            module['name'] = deploy_infos['module']
            return module, deploy_infos['package']
        return None, None

    def _deploy_module(self, module, fabric_execution_strategy, safe_deployment_strategy):
        deploy_module_on_hosts(self._cloud_connection, module, fabric_execution_strategy, self._app, self._config, self._log_file, safe_deployment_strategy)

    def _local_extract_package(self, module, package):
        clone_path = self._get_clone_path_from_module(module)
        gcall('rm -rf "%s"' % clone_path, 'Cleaning old temporary redeploy module working directory "%s"' % clone_path, self._log_file)
        gcall('mkdir -p "%s"' % clone_path, 'Recreating redeploy module working directory "%s"' % clone_path, self._log_file)

        key_path = '{path}/{module}/{pkg_name}'.format(path=self._get_path_from_app(), module=module['name'], pkg_name=package)
        log("Downloading package: {0} from '{1}'".format(package, key_path), self._log_file)
        dest_package_path = "{0}/{1}".format(clone_path, package)
        cloud_connection = cloud_connections.get(self._app.get('provider', DEFAULT_PROVIDER))(self._log_file)
        conn = cloud_connection.get_connection(self._config.get('bucket_region', self._app['region']), ["s3"])
        bucket = conn.get_bucket(self._config['bucket_s3'])
        key = bucket.get_key(key_path)
        if not key:
            raise GCallException("Package '{0}' doesn't exist on bucket '{1}'".format(key_path, self._config['bucket_s3']))
        key.get_contents_to_filename(dest_package_path)

        gcall('tar -xf "{0}" -C "{1}"'.format(dest_package_path, clone_path), "Extracting package: %s" % package, self._log_file)
        return clone_path

    def _execute_redeploy(self, deploy_id, fabric_execution_strategy, safe_deployment_strategy):
        module, package = self._get_deploy_infos(deploy_id)
        if module and package:
            self._update_manifest(module, package)
            all_app_modules_list = get_app_module_name_list(self._app['modules'])
            clean_local_module_workspace(self._get_path_from_app(), all_app_modules_list, self._log_file)
            # Download and extract package before launching deploy
            clone_path = self._local_extract_package(module, package)

            # Re-deploy
            self._deploy_module(module, fabric_execution_strategy, safe_deployment_strategy)

            # After all deploy exec
            execute_module_script_on_ghost(self._app, module, 'after_all_deploy', 'After all deploy', clone_path, self._log_file)
        else:
            raise GCallException("Redeploy on deployment ID: {0} failed".format(deploy_id))

    def execute(self):
        log("Redeploying module", self._log_file)
        if 'options' in self._job and len(self._job['options']) > 0:
            deploy_id = self._job['options'][0]
            fabric_execution_strategy = self._job['options'][1] if len(self._job['options']) > 0 else None
            safe_deployment_strategy = self._job['options'][2] if len(self._job['options']) > 2 else None
            try:
                self._execute_redeploy(deploy_id, fabric_execution_strategy, safe_deployment_strategy)
                self._worker.update_status("done", message="Redeploy OK: [{0}]".format(deploy_id))
            except GCallException as e:
                self._worker.update_status("failed", message="Redeploy Failed: [{0}]\n{1}".format(deploy_id, str(e)))
        else:
            self._worker.update_status("failed", message="Incorrect job request: missing options field deploy_id")
