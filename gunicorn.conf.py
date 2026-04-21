"""
Gunicorn config.

workers = (2 * CPU) + 1 is the classic rule for sync/I/O-bound workloads. For
async uvicorn workers a lower count can be enough, but since bcrypt and other
occasional CPU work run in the thread pool, the classic formula gives headroom.
WEB_CONCURRENCY env var overrides when tuning per-host.

uvicorn[standard] installs uvloop + httptools; UvicornWorker auto-uses them.
"""

import multiprocessing
import os

# Worker config
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", (multiprocessing.cpu_count() * 2) + 1))
worker_connections = 1000
timeout = 30
keepalive = 5

# Binding
bind = "0.0.0.0:8000"

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "credfluence-api"

# Graceful shutdown
graceful_timeout = 30
