from botflow_platform.http import create_app

from botflow_automation.containers import Container

app = create_app("automation", Container())
