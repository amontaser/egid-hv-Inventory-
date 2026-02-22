import multiprocessing
import sys

sys.path.insert(0, "/opt/hyperv_inventory")

bind = "0.0.0.0:5000"
workers = 3
worker_class = "sync"
timeout = 120
raw_env = ["PYTHONPATH=/opt/hyperv_inventory"]
