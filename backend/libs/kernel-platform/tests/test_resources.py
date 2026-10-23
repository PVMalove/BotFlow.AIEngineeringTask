"""Redis and S3 resources open on their own; open_resources composes them."""

from pathlib import Path
from typing import Any

import pytest

from kernel_platform import resources
from kernel_platform.resources import open_redis, open_resources, open_s3
from kernel_platform.settings import Settings

PASSWORD = "p4ss-never-in-an-error"


def settings(tmp_path: Path, service: str = "automation") -> Settings:
    for name in (
        f"redis_{service}_password",
        "minio_password",
        f"{service}_db_password",
        "rabbitmq_password",
    ):
        (tmp_path / name).write_text(PASSWORD)
    return Settings(service=service, secrets_dir=tmp_path)  # type: ignore[arg-type]


class FakeRedis:
    instances: list["FakeRedis"] = []
    ping_error: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        FakeRedis.instances.append(self)

    async def ping(self) -> None:
        if FakeRedis.ping_error:
            raise FakeRedis.ping_error

    async def aclose(self) -> None:
        self.closed = True


class FakeS3Client:
    closed = False


class FakeS3Context:
    def __init__(self, log: list[tuple[str, dict[str, Any]]], kwargs: Any) -> None:
        self.log = log
        self.kwargs = kwargs
        self.client = FakeS3Client()
        self.error: Exception | None = None

    async def __aenter__(self) -> FakeS3Client:
        if self.error:
            raise self.error
        self.log.append(("client", self.kwargs))
        return self.client

    async def __aexit__(self, *exc: object) -> None:
        self.client.closed = True


class FakeAioboto3:
    log: list[tuple[str, dict[str, Any]]] = []
    contexts: list[FakeS3Context] = []
    error: Exception | None = None

    class Session:
        def client(self, name: str, **kwargs: Any) -> FakeS3Context:
            context = FakeS3Context(FakeAioboto3.log, {"name": name, **kwargs})
            context.error = FakeAioboto3.error
            FakeAioboto3.contexts.append(context)
            return context


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRedis.instances = []
    FakeRedis.ping_error = None
    FakeAioboto3.log = []
    FakeAioboto3.contexts = []
    FakeAioboto3.error = None
    monkeypatch.setattr(resources, "Redis", FakeRedis)
    monkeypatch.setattr(resources, "aioboto3", FakeAioboto3)


async def test_redis_is_opened_for_the_service_user_and_closed_on_exit(
    tmp_path: Path,
) -> None:
    async with open_redis(settings(tmp_path)) as redis:
        (instance,) = FakeRedis.instances
        assert id(redis) == id(instance) and not instance.closed
        assert instance.kwargs["username"] == "automation"
        assert instance.kwargs["password"] == PASSWORD
        assert instance.kwargs["host"] == "redis"

    assert instance.closed


async def test_a_redis_failure_is_reported_without_the_driver_message(
    tmp_path: Path,
) -> None:
    FakeRedis.ping_error = ConnectionError(f"AUTH {PASSWORD} refused")

    with pytest.raises(RuntimeError) as raised:
        async with open_redis(settings(tmp_path)):
            raise AssertionError("must not open")

    assert PASSWORD not in str(raised.value)
    assert "automation" in str(raised.value)
    assert FakeRedis.instances[0].closed


async def test_an_error_inside_the_redis_block_is_not_rewritten(
    tmp_path: Path,
) -> None:
    with pytest.raises(KeyError):
        async with open_redis(settings(tmp_path)):
            raise KeyError("business")

    assert FakeRedis.instances[0].closed


async def test_s3_is_opened_path_style_with_the_minio_secret(tmp_path: Path) -> None:
    async with open_s3(settings(tmp_path)) as client:
        (context,) = FakeAioboto3.contexts
        assert client is context.client and not client.closed
        kwargs = context.kwargs
        assert kwargs["name"] == "s3"
        assert kwargs["endpoint_url"] == "http://minio:9000"
        assert kwargs["aws_access_key_id"] == "botflow"
        assert kwargs["aws_secret_access_key"] == PASSWORD
        assert kwargs["config"].s3["addressing_style"] == "path"

    assert context.client.closed


async def test_an_s3_failure_is_reported_without_the_sdk_message(
    tmp_path: Path,
) -> None:
    FakeAioboto3.error = OSError(f"endpoint refused for {PASSWORD}")

    with pytest.raises(RuntimeError) as raised:
        async with open_s3(settings(tmp_path)):
            raise AssertionError("must not open")

    assert PASSWORD not in str(raised.value)


class FakeEngine:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def connect(self) -> "FakeEngine":
        return self

    async def __aenter__(self) -> "FakeEngine":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: object) -> None:
        return None

    async def dispose(self) -> None:
        self.log.append("engine disposed")


class FakeRabbit:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def close(self) -> None:
        self.log.append("rabbit closed")


@pytest.fixture
def infrastructure(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []

    async def connect_robust(**kwargs: Any) -> FakeRabbit:
        return FakeRabbit(log)

    monkeypatch.setattr(resources, "_create_engine", lambda settings: FakeEngine(log))
    monkeypatch.setattr(resources, "connect_robust", connect_robust)
    return log


async def test_open_resources_composes_redis_and_s3_for_automation(
    tmp_path: Path, infrastructure: list[str]
) -> None:
    async with open_resources(settings(tmp_path)) as opened:
        assert id(opened.redis) == id(FakeRedis.instances[0])
        assert opened.s3 is FakeAioboto3.contexts[0].client
        assert not opened.closed

    assert opened.closed
    assert FakeRedis.instances[0].closed and FakeAioboto3.contexts[0].client.closed
    assert infrastructure == ["rabbit closed", "engine disposed"]


async def test_open_resources_gives_tasks_redis_but_no_s3(
    tmp_path: Path, infrastructure: list[str]
) -> None:
    async with open_resources(settings(tmp_path, "tasks")) as opened:
        assert opened.redis is not None and opened.s3 is None


async def test_a_later_failure_unwinds_what_was_opened_and_hides_the_cause(
    tmp_path: Path, infrastructure: list[str]
) -> None:
    FakeAioboto3.error = OSError(f"endpoint refused for {PASSWORD}")

    with pytest.raises(RuntimeError) as raised:
        async with open_resources(settings(tmp_path)):
            raise AssertionError("must not open")

    assert PASSWORD not in str(raised.value)
    assert FakeRedis.instances[0].closed
    assert infrastructure == ["rabbit closed", "engine disposed"]


async def test_an_arbitrary_runtime_error_is_sanitised(
    tmp_path: Path, infrastructure: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_rabbit(**kwargs: Any) -> FakeRabbit:
        raise RuntimeError(f"internal broker failure with {PASSWORD}")

    monkeypatch.setattr(resources, "connect_robust", broken_rabbit)

    with pytest.raises(RuntimeError) as raised:
        async with open_resources(settings(tmp_path)):
            raise AssertionError("must not open")

    assert PASSWORD not in str(raised.value)
    assert str(raised.value) == "Infrastructure initialization failed for automation"
