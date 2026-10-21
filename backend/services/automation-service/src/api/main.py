from botflow_platform.http import create_app

from api.containers import Container

app = create_app("automation", Container())
