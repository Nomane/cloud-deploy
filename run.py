from datetime import datetime

from flask import abort, request
from flask_bootstrap import Bootstrap

from eve import Eve
from eve_swagger import swagger

from auth import BCryptAuth
from bson.objectid import ObjectId
import json

from redis import Redis
from rq import Queue, cancel_job
import rq_dashboard

from settings import __dict__ as eve_settings, REDIS_HOST, RQ_JOB_TIMEOUT
from command import Command
from models.apps import apps
from models.jobs import jobs, CANCELLABLE_JOB_STATUSES, DELETABLE_JOB_STATUSES
from models.deployments import deployments

from ghost_tools import get_rq_name_from_app, boolify
from ghost_blueprints import commands_blueprint
from ghost_api import ghost_api_bluegreen_is_enabled, ghost_api_enable_green_app
from ghost_api import ghost_api_delete_alter_ego_app, ghost_api_clean_bluegreen_app
from ghost_api import initialize_app_modules, check_and_set_app_fields_state
from ghost_api import ghost_api_app_data_input_validator, GhostAPIInputError
from ghost_api import ALL_COMMAND_FIELDS
from ghost_lxd import lxd_blueprint
from libs.blue_green import BLUE_GREEN_COMMANDS, get_blue_green_from_app, ghost_has_blue_green_enabled
from ghost_aws import normalize_application_tags


def get_apps_db():
    return ghost.data.driver.db[apps['datasource']['source']]


def get_jobs_db():
    return ghost.data.driver.db[jobs['datasource']['source']]


def get_deployments_db():
    return ghost.data.driver.db[deployments['datasource']['source']]


def pre_update_app(updates, original):
    """
    eve pre-update event hook to reset modified modules' 'initialized' field.

    Uninitialized modules stay so, modified or not:

    >>> from copy import deepcopy
    >>> base_original = {'_id': 1111, 'env': 'prod', 'name': 'app1', 'role': 'webfront', 'modules': [
    ...     {'name': 'mod1', 'git_repo': 'git@github.com/test/mod1', 'path': '/tmp/ok'},
    ...     {'name': 'mod2', 'git_repo': 'git@github.com/test/mod2', 'path': '/tmp/ok'}],
    ... 'environment_infos': {'instance_tags':[]}}
    >>> original = deepcopy(base_original)
    >>> updates = deepcopy(base_original)
    >>> pre_update_app(updates, original)
    >>> updates['modules'][0]['initialized']
    False
    >>> updates['modules'][1]['initialized']
    False

    Initialized modules stay so if not modified:

    >>> original['modules'][0]['initialized'] = True
    >>> original['modules'][1]['initialized'] = True
    >>> updates = deepcopy(base_original)
    >>> pre_update_app(updates, original)
    >>> updates['modules'][0]['initialized']
    True
    >>> updates['modules'][1]['initialized']
    True

    Modified modules get their 'initialized' field reset to False:

    >>> updates = deepcopy(base_original)
    >>> updates['modules'][1]['git_repo'] = 'git@github.com/test/mod2-modified'
    >>> pre_update_app(updates, original)
    >>> updates['modules'][0]['initialized']
    True
    >>> updates['modules'][1]['initialized']
    False

    Modified modules get their 'initialized' field reset to False also in case of new fields:

    >>> updates = deepcopy(base_original)
    >>> updates['modules'][1]['uid'] = '101'
    >>> updates['modules'][1]['gid'] = '102'
    >>> pre_update_app(updates, original)
    >>> updates['modules'][0]['initialized']
    True
    >>> updates['modules'][1]['initialized']
    False

    New modules get their 'initialized' field set to False by default:

    >>> updates = deepcopy(base_original)
    >>> updates['modules'].append({'name': 'mod3', 'git_repo': 'git@github.com/test/mod3', 'path': '/tmp/ok'})
    >>> pre_update_app(updates, original)
    >>> updates['modules'][0]['initialized']
    True
    >>> updates['modules'][1]['initialized']
    True
    >>> updates['modules'][2]['initialized']
    False
    """

    try:
        ghost_api_app_data_input_validator(updates)
    except GhostAPIInputError as error:
        abort(422, description=error.message)

    # Selectively reset each module's 'initialized' property if any of its other properties have changed
    updates, modules_edited = initialize_app_modules(updates, original)
    user = request.authorization.username if request and request.authorization else 'Nobody'
    updates = check_and_set_app_fields_state(user, updates, original, modules_edited)

    if 'environment_infos' in updates and 'instance_tags' in updates['environment_infos']:
        updates['environment_infos']['instance_tags'] = normalize_application_tags(original, updates)

    # Blue/green disabled ?
    try:
        blue_green_section, color = get_blue_green_from_app(updates)
        if (blue_green_section and
                    'enable_blue_green' in blue_green_section and
                isinstance(blue_green_section['enable_blue_green'], bool) and
                not blue_green_section['enable_blue_green']):

            if not ghost_api_clean_bluegreen_app(get_apps_db(), original):
                abort(422)

            if not ghost_api_delete_alter_ego_app(get_apps_db(), original):
                abort(422)

            del updates['blue_green']
    except Exception as e:
        print e
        abort(500)


def post_update_app(updates, original):
    try:
        # Enable green app only if not already enabled
        blue_green, color = get_blue_green_from_app(original)
        if ghost_api_bluegreen_is_enabled(updates) and not color:
            # Maybe we need to have the "merged" app after update here instead of "original" one ?
            if not ghost_api_enable_green_app(get_apps_db(), original, request.authorization.username):
                abort(422)
    except Exception as e:
        print "Exception occured"
        print e
        abort(500)


def pre_replace_app(item, original):
    # TODO: implement (or not?) application replacement
    abort(406, description="Application replacement not allowed, please use update/PATCH verb.")


def pre_delete_app(item):
    # TODO: implement purge of application (git repo clone)
    pass


def post_delete_app(item):
    if not ghost_api_delete_alter_ego_app(get_apps_db(), item):
        abort(422, description="Cannot delete the associated blue-green application")


def pre_insert_app(items):
    app = items[0]
    name = app.get('name')
    role = app.get('role')
    env = app.get('env')
    app['environment_infos']['instance_tags'] = normalize_application_tags(app, app)

    try:
        ghost_api_app_data_input_validator(app)
    except GhostAPIInputError as error:
        abort(422, description=error.message)

    blue_green = app.get('blue_green', None)
    # We can now insert a new app with a different color
    if blue_green and blue_green.get('color', None):
        if get_apps_db().find_one(
                {'$and': [{'name': name}, {'role': role}, {'env': env}, {'blue_green.color': blue_green['color']}]}):
            abort(422, description="An app already exist with same name, role, env and color. Please change one these fields.")
    else:
        if get_apps_db().find_one({'$and': [{'name': name}, {'role': role}, {'env': env}]}):
            abort(422, description="An app already exist with same name, role and env. Please change one these fields.")
    for mod in app.get('modules'):
        mod['initialized'] = False

    app['pending_changes'] = [{
        'field': object_name,
        'user': request.authorization.username,
        'updated': datetime.utcnow(),
    } for object_name in ALL_COMMAND_FIELDS]

    app['user'] = request.authorization.username


def post_insert_app(items):
    app = items[0]
    if ghost_api_bluegreen_is_enabled(app):
        if not ghost_api_enable_green_app(get_apps_db(), app, request.authorization.username):
            abort(422, "Problem occured creating/enabling the green app")


def _post_fetched_app(app, embed_last_deployment=False):
    # Retrieve each module's last deployment
    for module in app.get('modules', []):
        query = {
            '$and': [
                {'app_id': app['_id']},
                {'module': module['name']}
            ]
        }
        sort = [('timestamp', -1)]
        deployment = get_deployments_db().find_one(query, sort=sort)
        if deployment:
            module['last_deployment'] = deployment if embed_last_deployment else deployment['_id']


def post_fetched_apps(response):
    # Do we need to embed each module's last_deployment?
    embedded = json.loads(request.args.get('embedded', '{}'))
    embed_last_deployment = boolify(embedded.get('modules.last_deployment', False))

    for app in response['_items']:
        _post_fetched_app(app, embed_last_deployment)


def post_fetched_app(response):
    # Do we need to embed each module's last_deployment?
    embedded = json.loads(request.args.get('embedded', '{}'))
    embed_last_deployment = boolify(embedded.get('modules.last_deployment', False))

    _post_fetched_app(response, embed_last_deployment)


def pre_insert_job(items):
    job = items[0]
    app_id = job.get('app_id')
    app = get_apps_db().find_one({'_id': ObjectId(app_id)})
    if not app:
        abort(404)
    if not ghost_has_blue_green_enabled():
        # Blue/Green is disabled, but trying to use a blue/green command - denied
        if job.get('command') in BLUE_GREEN_COMMANDS:
            abort(422, description="Blue-Green deployment is currently disabled, command not available")
    if job.get('command') == 'deploy':
        for module in job['modules']:
            not_exist = True
            for mod in app['modules']:
                if 'name' in module and module['name'] == mod['name']:
                    not_exist = False
            if not_exist:
                abort(422, description="Module to deploy not found")
    if job['command'] == 'build_image':
        if not ('build_infos' in app.viewkeys()):
            abort(422, description="Impossible to build image, build infos fields are empty")
    job['user'] = request.authorization.username
    job['status'] = 'init'
    job['message'] = 'Initializing job'


def post_insert_job(items):
    job = items[0]
    job_id = str(job.get('_id'))

    app_id = job.get('app_id')
    app = get_apps_db().find_one({'_id': ObjectId(app_id)})

    # Place job in app's queue
    rq_job = Queue(name=get_rq_name_from_app(app), connection=ghost.ghost_redis_connection,
                   default_timeout=RQ_JOB_TIMEOUT).enqueue(Command().execute, job_id, job_id=job_id)
    assert rq_job.id == job_id


def pre_delete_job(item):
    if item['status'] not in DELETABLE_JOB_STATUSES:
        # Do not allow deleting jobs not in cancelled, done, failed or aborted status
        abort(422, description="Deleting a job not in cancelled, done, failed or aborted status is not possible")


def pre_delete_job_enqueueings():
    job_id = request.view_args['job_id']
    job = get_jobs_db().find_one({'_id': ObjectId(job_id)})

    if job and job['status'] in CANCELLABLE_JOB_STATUSES:
        # Cancel the job from RQ
        cancel_job(job_id, connection=ghost.ghost_redis_connection)
        get_jobs_db().update({'_id': ObjectId(job_id)},
                             {'$set': {'status': 'cancelled', 'message': 'Job cancelled', '_updated': datetime.now()}})
        return

    # Do not allow cancelling jobs not in init status
    abort(422, description="Cancelling a job not in init status is not allowed")


# Create ghost app, explicitly specifying the settings to avoid errors during doctest execution
ghost = Eve(auth=BCryptAuth, settings=eve_settings)
Bootstrap(ghost)
rq_settings = rq_dashboard.default_settings.__dict__
rq_settings.update({"REDIS_HOST": REDIS_HOST})
ghost.config.from_mapping(rq_settings)
ghost.register_blueprint(rq_dashboard.blueprint, url_prefix='/rq')
ghost.register_blueprint(swagger, url_prefix='/docs/api')
# Map /docs/api to eve_swagger as it is hardcoded to <url_prefix>/api-docs (cf. https://github.com/nicolaiarocci/eve-swagger/issues/33)
ghost.add_url_rule('/docs/api', 'eve_swagger.index')

# Register eve hooks
ghost.on_fetched_item_apps += post_fetched_app
ghost.on_fetched_resource_apps += post_fetched_apps
ghost.on_update_apps += pre_update_app
ghost.on_updated_apps += post_update_app
ghost.on_replace_apps += pre_replace_app
ghost.on_delete_item_apps += pre_delete_app
ghost.on_deleted_item_apps += post_delete_app
ghost.on_insert_apps += pre_insert_app
ghost.on_inserted_apps += post_insert_app
ghost.on_insert_jobs += pre_insert_job
ghost.on_inserted_jobs += post_insert_job
ghost.on_delete_item_jobs += pre_delete_job
ghost.on_delete_resource_job_enqueueings += pre_delete_job_enqueueings

ghost.ghost_redis_connection = Redis(host=REDIS_HOST)

# Register non-mongodb resources as plain Flask blueprints (they won't appear in /docs)
ghost.register_blueprint(commands_blueprint)
ghost.register_blueprint(lxd_blueprint)

if __name__ == '__main__':
    ghost.run(host='0.0.0.0')
