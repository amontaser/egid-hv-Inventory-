"""WSGI entry point for production deployment with Gunicorn"""

import sys
sys.path.insert(0, '/opt/hyperv_inventory')

from app import create_app

app = create_app()
