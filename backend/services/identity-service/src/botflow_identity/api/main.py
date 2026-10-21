from botflow_platform.http import create_app

from botflow_identity.containers import Container

app = create_app("identity", Container())
