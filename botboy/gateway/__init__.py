"""Gateway package security hooks."""

# Importing the gateway package installs the queue/lease and raw task-store
# authorization patches before route modules obtain the task-store proxy.
from botboy.gateway import queue_security as _queue_security  # noqa: F401,E402
from botboy.gateway import task_boundary as _task_boundary  # noqa: F401,E402
from botboy.gateway import recovery_security as _recovery_security  # noqa: F401,E402
from botboy.gateway import task_summary_security as _task_summary_security  # noqa: F401,E402
from botboy.gateway import merge_security as _merge_security  # noqa: F401,E402
from botboy.gateway import store_read_security as _store_read_security  # noqa: F401,E402
from botboy.gateway import task_store_surface_security as _task_store_surface_security  # noqa: F401,E402
from botboy.gateway import worker_read_security as _worker_read_security  # noqa: F401,E402
from botboy.gateway import status_read_security as _status_read_security  # noqa: F401,E402
