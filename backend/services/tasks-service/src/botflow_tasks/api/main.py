from botflow_platform.http import create_app

from botflow_tasks.containers import Container

app = create_app("tasks", Container())
