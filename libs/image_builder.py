# -*- coding: utf-8 -*-

import time
import os
import io

from ghost_log import log
from ghost_tools import b64decode_utf8

from .blue_green import get_blue_green_from_app

AMI_BASE_FMT = "ami.{env}.{region}.{role}.{name}.{color}"
AMI_FMT = AMI_BASE_FMT + "{date}"


class ImageBuilder:
    """
    This class is the generic interface used by Buildimage command
    in order to create the desired Image
    """
    def __init__(self, app, job, db, log_file, config):

        self._app = app
        self._job = job
        self._db = db
        self._log_file = log_file
        self._config = config

        blue_green, self._color = get_blue_green_from_app(self._app)
        self._ami_name = AMI_FMT.format(env=self._app['env'], region=self._app['region'],
                                                              role=self._app['role'],
                                                              name=self._app['name'],
                                                              date=time.strftime("%Y%m%d-%H%M%S"),
                                                              color='.%s' % self._color if self._color else '')

    def _format_ghost_env_vars(self):
        ghost_vars = []
        ghost_vars.append('GHOST_APP=%s' % self._app['name'])
        ghost_vars.append('GHOST_ENV=%s' % self._app['env'])
        ghost_vars.append('GHOST_ENV_COLOR=%s' % (self._color if self._color else ''))
        ghost_vars.append('GHOST_ROLE=%s' % self._app['role'])
        return ghost_vars    

    def _generate_buildimage_hook(self, hook_name):
        """ Generates a buildimage hook script

        >>> from StringIO import StringIO
        >>> from ghost_tools import b64encode_utf8
        >>> app = { \
                'name': 'AppName', 'env': 'prod', 'role': 'webfront', 'region': 'eu-west-1',\
                'lifecycle_hooks': {'pre_buildimage': u'', 'post_buildimage': b64encode_utf8(u'echo Custom post-buildimage script')}\
            }
        >>> job = None
        >>> log_file = StringIO()
        >>> _config = None
        >>> _db = None

        >>> ImageBuilder(app, job, _db, log_file, _config)._generate_buildimage_hook('pre_buildimage')
        '/ghost/AppName/prod/webfront/hook-pre_buildimage'
        >>> with io.open('/ghost/AppName/prod/webfront/hook-pre_buildimage', encoding='utf-8') as f:
        ...   f.read()
        u'echo No pre_buildimage script'

        >>> ImageBuilder(app, job, _db, log_file, _config)._generate_buildimage_hook('post_buildimage')
        '/ghost/AppName/prod/webfront/hook-post_buildimage'
        >>> with io.open('/ghost/AppName/prod/webfront/hook-post_buildimage', encoding='utf-8') as f:
        ...   f.read()
        u'echo Custom post-buildimage script'

        """
        log("Create '%s' script for Packer" % hook_name, self._log_file)
        lfc_hooks = self._app.get('lifecycle_hooks', None)
        if not lfc_hooks or not lfc_hooks.get(hook_name, None):
            hook_source = u"echo No {hook_name} script".format(hook_name=hook_name)
        else:
            hook_source = b64decode_utf8(self._app['lifecycle_hooks'][hook_name])
        app_path = "/ghost/{name}/{env}/{role}".format(name=self._app['name'], env=self._app['env'], role=self._app['role'])
        if not os.path.exists(app_path):
            os.makedirs(app_path)
        hook_file_path = "{app_path}/hook-{hook_name}".format(app_path=app_path, hook_name=hook_name)
        with io.open(hook_file_path, mode='w', encoding='utf-8') as f:
            f.write(hook_source)
        return hook_file_path

    def _get_buildimage_hooks(self):
        """
        Create and return a dictionary will all hooks available for Build Image process
        """
        hooks = {}
        hooks['pre_buildimage'] = self._generate_buildimage_hook('pre_buildimage')
        hooks['post_buildimage'] = self._generate_buildimage_hook('post_buildimage')
        return hooks

    def start_builder(self):
        raise NotImplementedError

    def purge_old_images(self):
        raise NotImplementedError
