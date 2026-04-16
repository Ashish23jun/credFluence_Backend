import multiprocessing

# Worker config
worker_class = "uvicorn.workers.UvicornWorker"
workers = 4
worker_connections = 1000
timeout = 30
keepalive = 2

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
