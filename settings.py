from models import jobs
from models import apps
from models import deployments
from models import job_enqueueings
from aws_connection import AWSConnection

from ghost_tools import config

API_NAME = 'GHOST API'

# Let's just use the local mongod instance. Edit as needed.

# Please note that MONGO_HOST and MONGO_PORT could very well be left
# out as they already default to a bare bones local 'mongod' instance.
MONGO_HOST = config.get('mongo_host', 'localhost')
MONGO_PORT = 27017
#MONGO_USERNAME = 'user'
#MONGO_PASSWORD = 'user'
MONGO_DBNAME = 'apitest'
MONGO_QUERY_BLACKLIST = ['$where']

REDIS_HOST = config.get('redis_host', 'localhost')

# Enable reads (GET) and inserts (POST) for resources/collections
# (if you omit this line, the API will default to ['GET'] and provide
# read-only access to the endpoint).
RESOURCE_METHODS = ['GET', 'POST']

# Enable reads (GET), edits (PATCH), replacements (PUT) and deletes of
# individual items  (defaults to read-only item access).
ITEM_METHODS = ['GET', 'PATCH', 'PUT', 'DELETE']

# EVE Pagination
PAGINATION_DEFAULT = config.get('eve_pagination_default', 23)

# API BASE URL
API_BASE_URL = config.get('api_base_url', 'http://localhost:5000')

DOMAIN = {
    'job_enqueueings': job_enqueueings.job_enqueueings,
    'jobs': jobs.jobs,
    'apps': apps.apps,
    'deployments': deployments.deployments
}

cloud_connections = {
    'aws': AWSConnection,
    'azure': 'NotYetSupported',
    'google': 'NotYetSupported'
}
# Default cloud provider
DEFAULT_PROVIDER = 'aws'

DEBUG = True

VERSIONING = True
