from kernel_platform.http.app import create_app

from api.containers import Container

app = create_app("automation", Container())
