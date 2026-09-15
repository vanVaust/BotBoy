"""Gateway package security hooks."""

# Importing the gateway package installs the queue/lease authorization patch
# before route modules obtain the task-store proxy.
from botboy.gateway import queue_security as _queue_security  # noqa: F401,E402
