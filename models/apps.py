import env
import instance_role
import resources
import volumes

apps_schema = {
    'name': {
        'type': 'string',
        'regex': '^[a-zA-Z0-9_.+-]*$',
        'required': True
    },
    'assumed_account_id': {
        'type': 'string',
        'regex': '^[a-zA-Z0-9_.+-]*$',
        'required': False
    },
    'assumed_role_name': {
        'type': 'string',
        'regex': '^[a-zA-Z0-9_.+-]*$',
        'required': False
    },
    'assumed_region_name': {
        'type': 'string',
        'regex': '^[a-zA-Z0-9_.+-]*$',
        'required': False
    },
    'region': {'type': 'string'},
    'instance_type': {'type': 'string'},
    'env': {'type': 'string',
            'allowed': env.env,
            'required': True},
    'lifecycle_hooks': {
        'type': 'dict',
        'schema': {
            'pre_buildimage': {'type': 'string'},
            'post_buildimage': {'type': 'string'},
            'pre_bootstrap': {'type': 'string'},
            'post_bootstrap': {'type': 'string'},
        }
    },
    'features': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'name': {
                    'type': 'string',
                    'regex': '^[a-zA-Z0-9\.\-\_]*$',
                    'required': True
                },
                'version': {
                    'type': 'string',
                    'regex': '^[a-zA-Z0-9\.\-\_\/:~\+=]*$',
                    'required': False
                }
            }
        }
    },
    'role': {
        'type': 'string',
        'allowed': instance_role.role,
        'required': True
    },
    'ami': {'type': 'string',
            'regex': '^ami-[a-z0-9]*$',
            'readonly': True},
    'vpc_id': {
        'type': 'string',
        'regex': '^vpc-[a-z0-9]*$',
        'required': True
    },
    'modules': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'initialized': {'type': 'boolean',
                                'readonly': True},
                'name': {'type': 'string',
                         'regex': '^[a-zA-Z0-9\.\-\_]*$',
                         'required': True},
                'git_repo': {'type': 'string',
                             'required': True},
                'scope': {
                    'type': 'string',
                    'required': True,
                    'allowed': ['system', 'code']
                },
                'uid': {'type': 'integer', 'min': 0},
                'gid': {'type': 'integer', 'min': 0},
                'build_pack': {'type': 'string'},
                'pre_deploy': {'type': 'string'},
                'post_deploy': {'type': 'string'},
                'after_all_deploy': {'type': 'string'},
                'path': {'type': 'string',
                         'regex': '^(/[a-zA-Z0-9\.\-\_]+)+$',
                         'required': True},
                'last_deployment': {
                    'readonly': True,
                    'type': 'objectid',
                    'data_relation': {
                        'resource': 'deployments',
                        'field': '_id',
                        'embeddable': True
                    }
                }
            }
        }
    },
    'log_notifications': {
        'type': 'list',
        'schema': {
            'type': 'string',
            'regex': '^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        }
    },
    'autoscale': {
        'type': 'dict',
        'schema': {
            'min': {'type': 'integer',
                    'min': 0},
            'max': {'type': 'integer',
                    'min': 1},
            'current': {'type': 'integer'},
            'name': {'type': 'string'}
        }
    },
    'safe-deployment': {
        'type': 'dict',
        'schema': {
            'load_balancer_type' : {'type': 'string'},
            'wait_after_deploy' : {'type': 'integer', 'min': 0},
            'wait_before_deploy' : {'type': 'integer', 'min': 0},
            'app_tag_value': {'type': 'string', 'required': False},
            'ha_backend': {'type': 'string', 'required': False},
            'api_port': {'type': 'integer', 'required': False}
        }
    },
    'build_infos': {
        'type': 'dict',
        'schema': {
            'ssh_username': {'type': 'string',
                             'regex': '^[a-z\_][a-z0-9\_\-]{0,30}$',
                             'required': True},
            'source_ami': {'type': 'string',
                           'regex': '^ami-[a-z0-9]*$',
                           'required': True},
            'ami_name': {'type': 'string',
                         'readonly': True},
            'subnet_id': {'type': 'string',
                          'regex': '^subnet-[a-z0-9]*$',
                          'required': True}
        }
    },
    'resources': {'type': 'list',
                  'schema': resources.available},
    'environment_infos': {'type': 'dict',
                          'schema': {
                              'security_groups': {'type': 'list',
                                                  'schema':
                                                  {'type': 'string',
                                                   'regex': '^sg-[a-z0-9]*$'}},
                              'subnet_ids': {'type': 'list',
                                             'schema': {'type': 'string',
                                                        'regex':
                                                        '^subnet-[a-z0-9]*$'}},
                              'instance_profile':
                              {'type': 'string',
                               'regex': '^[a-zA-Z0-9\+\=\,\.\@\-\_]{1,64}$'},
                              'key_name': {'type': 'string',
                                           'regex': '^[\x00-\x7F]{1,255}$'},
                              'root_block_device':
                              {'type': 'dict',
                               'schema': {
                                   'size': {'type': 'integer'},
                                   'name': {'type': 'string',
                                            'regex': '^/[a-z0-9]+/[a-z0-9]+$'}
                               }},
                              'optional_volumes': {'type': 'list',
                                                   'required': False,
                                                   'schema': volumes.block}
                          }},
    'user': {'type': 'string'},
}

apps = {
    'datasource': {
        'source': 'apps'
    },
    'item_title': 'app',
    'schema': apps_schema
}
