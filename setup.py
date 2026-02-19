"""Setup configuration for Hyper-V Inventory"""

from setuptools import setup, find_packages

setup(
    name="hyperv_inventory",
    version="2.0.0",
    packages=find_packages(),
    install_requires=[
        "Flask>=3.0.0",
        "bootstrap-flask>=2.2.0",
        "python-dotenv>=1.0.0",
        "celery>=5.3.0",
        "redis>=5.0.0",
        "pywinrm>=0.4.3",
        "gunicorn>=21.0.0",
    ],
)
