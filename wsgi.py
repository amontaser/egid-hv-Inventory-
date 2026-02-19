"""WSGI entry point for production deployment with Gunicorn"""

from hyperv_inventory.app import create_app

app = create_app()
