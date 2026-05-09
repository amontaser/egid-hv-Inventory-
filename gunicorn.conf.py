import multiprocessing
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

bind = "0.0.0.0:5000"
workers = 2
worker_class = "sync"
timeout = 120
