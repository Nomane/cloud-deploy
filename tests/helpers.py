import collections
import os
import sys

import simplejson
from ghost_tools import b64encode_utf8

LOG_FILE = 'log_file'


def _dict_update_recursive(d, u):
    py2 = sys.version_info[0] < 3
    iter_func = u.items if py2 else u.iteritems
    for k, v in iter_func():
        if isinstance(v, collections.Mapping):
            r = _dict_update_recursive(d.get(k, {}), v)
            d[k] = r
        else:
            d[k] = u[k]
    return d


def get_test_application(**kwargs):
    """
    Factory that creates ghost applications. Any propertiy can be overrided with kwargs
    """
    return _dict_update_recursive({
        "ami": "ami-abcdef",
        "autoscale": {
            "enable_metrics": False,
            "max": 2,
            "min": 2,
            "name": "as.test"
        },
        "build_infos": {
            "source_ami": "ami-source",
            "ssh_username": "admin",
            "subnet_id": "subnet-test"
        },
        "env": "test",
        "env_vars": [],
        "environment_infos": {
            "instance_profile": "iam.profile.test",
            "public_ip_address": False,
            "instance_tags": [
                {
                    "tag_name": "Name",
                    "tag_value": "ec2.name.test"
                },
                {
                    "tag_name": "tag-name",
                    "tag_value": "tag-value"
                }
            ],
            "key_name": "key-test",
            "security_groups": [
                "sg-test"
            ],
            "subnet_ids": [
                "subnet-test"
            ]
        },
        "features": [
            {
                "name": "feature-name",
                "version": "feature-version"
            }
        ],
        "instance_type": "t2.medium",
        "lifecycle_hooks": {
            "post_bootstrap": "",
            "post_buildimage": "",
            "pre_bootstrap": "",
            "pre_buildimage": ""
        },
        "log_notifications": [
            "test-notif@fr.clara.net"
        ],
        "modules": [
            {
                "gid": 33,
                "git_repo": "git@bitbucket.org:morea/ghost.dummy.git",
                "name": "dummy",
                "path": "/var/www/dummy",
                "scope": "code",
                "uid": 33
            }
        ],
        "name": "test-app",
        "region": "eu-west-1",
        "role": "webfront",
        "safe-deployment": {
            "load_balancer_type": "elb",
            "wait_after_deploy": 10,
            "wait_before_deploy": 10
        },
        "user": "morea",
        "vpc_id": "vpc-test"
    }, kwargs)


def mocked_logger(msg, file):
    print(msg)


class DictObject(object):
    def __init__(self, **d):
        self.__dict__.update(d)


def get_aws_data(data_name, as_object=False):
    filename = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'aws_data', '{}.json'.format(data_name))
    if not os.path.isfile(filename):
        raise ValueError("File not found {}".format(filename))
    with open(filename, 'r') as f:
        d = simplejson.load(f)
        if as_object:
            if d.__iter__:
                return [DictObject(**e) for e in d]
            return DictObject(**d)
        return d


def get_dummy_bash_script(b64_encoding=False):
    script = """#!/bin/bash
set -x
echo "Dummy"
"""
    return b64encode_utf8(script) if b64_encoding else script
