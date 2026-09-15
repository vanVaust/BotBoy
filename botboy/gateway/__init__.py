"""Gateway package security hooks."""

# Importing the gateway package installs the queue/lease and raw task-store
authorization patches before route modules obtain the task-store proxy.
from botboy.gateway import queue_security as _queue_security  # noqa: F401,E402
from botboy.gateway import task_boundary as _task_boundary  # noqa: F401,E402
from botboy.gateway import recovery_security as _recovery_security  # noqa: F401,E402
