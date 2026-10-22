"""Telegram file source and S3 audio store against fake clients."""

from typing import Any
from uuid import uuid4

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.methods import GetFile
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from application.ports import (
    AudioMissing,
    AudioTooLarge,
    TelegramFileUnavailable,
    TransientIoError,
)
from infrastructure.s3_audio import S3AudioStore, ensure_bucket
from infrastructure.telegram_files import TelegramFileSource

MIB = 1024 * 1024
TOKEN = "123456:dummy-token-for-local-tests"
SECRET_URL = f"https://api.telegram.org/file/bot{TOKEN}/voice/file_1.oga"


class FakeFile:
    def __init__(self, file_path: str | None = "voice/file_1.oga", size: Any = 100):
        self.file_path = file_path
        self.file_size = size


class FakeBot:
    def __init__(self) -> None:
        self.file = FakeFile()
        self.chunks: list[bytes] = [b"OggS", b"voice"]
        self.get_file_error: Exception | None = None
        self.download_error: Exception | None = None
        self.calls: list[str] = []

    async def get_file(self, file_id: str) -> FakeFile:
        self.calls.append(f"get_file:{file_id}")
        if self.get_file_error:
            raise self.get_file_error
        return self.file

    async def download_file(self, file_path: str, destination: Any) -> None:
        self.calls.append(f"download:{file_path}")
        for chunk in self.chunks:
            destination.write(chunk)
        if self.download_error:
            raise self.download_error


def source(bot: FakeBot) -> TelegramFileSource:
    return TelegramFileSource(bot)  # type: ignore[arg-type]


async def test_the_file_is_looked_up_by_id_and_downloaded_into_memory() -> None:
    bot = FakeBot()

    data = await source(bot).fetch("AwACfile")

    assert data == b"OggSvoice"
    assert bot.calls == ["get_file:AwACfile", "download:voice/file_1.oga"]


async def test_a_file_declared_too_big_is_refused_before_the_download() -> None:
    bot = FakeBot()
    bot.file = FakeFile(size=5 * MIB + 1)

    with pytest.raises(AudioTooLarge):
        await source(bot).fetch("f")

    assert bot.calls == ["get_file:f"]


async def test_a_file_of_exactly_five_mebibytes_is_fetched() -> None:
    bot = FakeBot()
    bot.file = FakeFile(size=5 * MIB)

    await source(bot).fetch("f")

    assert "download:voice/file_1.oga" in bot.calls


async def test_the_download_stops_at_the_hard_cap_whatever_telegram_declared() -> None:
    bot = FakeBot()
    bot.file = FakeFile(size=None)
    bot.chunks = [b"x" * MIB] * 6

    with pytest.raises(AudioTooLarge):
        await source(bot).fetch("f")


async def test_a_missing_file_path_means_the_file_is_unavailable() -> None:
    bot = FakeBot()
    bot.file = FakeFile(file_path=None)

    with pytest.raises(TelegramFileUnavailable):
        await source(bot).fetch("f")


async def test_telegram_rejecting_the_file_id_is_final() -> None:
    bot = FakeBot()
    bot.get_file_error = TelegramBadRequest(
        method=GetFile(file_id="f"), message="Bad Request: wrong file_id"
    )

    with pytest.raises(TelegramFileUnavailable):
        await source(bot).fetch("f")


class Http404(Exception):
    status = 404


async def test_a_vanished_download_is_final() -> None:
    bot = FakeBot()
    bot.download_error = Http404(SECRET_URL)

    with pytest.raises(TelegramFileUnavailable):
        await source(bot).fetch("f")


@pytest.mark.parametrize(
    "error",
    [
        TelegramNetworkError(method=GetFile(file_id="f"), message="timeout"),
        TelegramServerError(method=GetFile(file_id="f"), message="502"),
        OSError(SECRET_URL),
    ],
)
async def test_network_and_server_trouble_is_transient_and_leaks_no_url(
    error: Exception,
) -> None:
    bot = FakeBot()
    bot.get_file_error = error

    with pytest.raises(TransientIoError) as raised:
        await source(bot).fetch("f")

    assert TOKEN not in repr(raised.value) and "api.telegram.org" not in str(
        raised.value
    )
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


async def test_a_download_failure_is_transient_and_leaks_no_url() -> None:
    bot = FakeBot()
    bot.download_error = RuntimeError(SECRET_URL)

    with pytest.raises(TransientIoError) as raised:
        await source(bot).fetch("f")

    assert TOKEN not in str(raised.value) and raised.value.__cause__ is None


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aenter__(self) -> "FakeBody":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.data


def client_error(code: str, operation: str = "GetObject") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "m"}}, operation)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None
        self.buckets: set[str] = set()

    async def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        if self.error:
            raise self.error
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {}

    async def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        if self.error:
            raise self.error
        try:
            data = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        except KeyError:
            raise client_error("NoSuchKey") from None
        return {"Body": FakeBody(data)}

    async def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_bucket", kwargs))
        if kwargs["Bucket"] not in self.buckets:
            raise client_error("404", "HeadBucket")
        return {}

    async def create_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_bucket", kwargs))
        if self.error:
            raise self.error
        self.buckets.add(kwargs["Bucket"])
        return {}


async def test_the_audio_is_stored_under_a_key_derived_from_the_job_only() -> None:
    s3, job_id = FakeS3(), uuid4()
    store = S3AudioStore(s3, "botflow-audio")

    key = await store.put(job_id, b"audio", "audio/ogg")

    assert key == f"voice/{job_id}"
    ((operation, kwargs),) = s3.calls
    assert operation == "put_object"
    assert (kwargs["Bucket"], kwargs["Key"], kwargs["Body"]) == (
        "botflow-audio",
        key,
        b"audio",
    )
    assert kwargs["ContentType"] == "audio/ogg"


async def test_no_acl_or_public_access_is_ever_requested() -> None:
    s3 = FakeS3()
    store = S3AudioStore(s3, "botflow-audio")

    await store.put(uuid4(), b"a", None)

    ((_, kwargs),) = s3.calls
    assert not {k for k in kwargs if "ACL" in k or "Grant" in k}
    assert kwargs["ContentType"] == "application/octet-stream"


async def test_the_stored_audio_is_read_back_by_the_job_id() -> None:
    s3, job_id = FakeS3(), uuid4()
    store = S3AudioStore(s3, "botflow-audio")
    await store.put(job_id, b"audio", "audio/ogg")

    assert await store.get(job_id) == b"audio"


async def test_putting_the_same_job_again_overwrites_the_one_object() -> None:
    s3, job_id = FakeS3(), uuid4()
    store = S3AudioStore(s3, "botflow-audio")

    await store.put(job_id, b"first", None)
    await store.put(job_id, b"second", None)

    assert list(s3.objects.values()) == [b"second"]


async def test_a_missing_object_is_reported_as_missing() -> None:
    store = S3AudioStore(FakeS3(), "botflow-audio")

    with pytest.raises(AudioMissing):
        await store.get(uuid4())


async def test_other_storage_failures_are_transient_and_hide_the_sdk_message() -> None:
    s3 = FakeS3()
    s3.error = OSError("http://botflow:secret@minio:9000 refused")
    store = S3AudioStore(s3, "botflow-audio")

    with pytest.raises(TransientIoError) as put_error:
        await store.put(uuid4(), b"a", None)
    with pytest.raises(TransientIoError) as get_error:
        await store.get(uuid4())

    for raised in (put_error, get_error):
        assert "secret" not in str(raised.value) and raised.value.__cause__ is None


async def test_a_missing_bucket_is_created() -> None:
    s3 = FakeS3()

    await ensure_bucket(s3, "botflow-audio")

    assert s3.buckets == {"botflow-audio"}


async def test_an_existing_bucket_is_left_alone() -> None:
    s3 = FakeS3()
    s3.buckets.add("botflow-audio")

    await ensure_bucket(s3, "botflow-audio")

    assert [name for name, _ in s3.calls] == ["head_bucket"]


async def test_a_bucket_created_by_a_racing_bootstrap_is_fine() -> None:
    s3 = FakeS3()
    s3.error = client_error("BucketAlreadyOwnedByYou", "CreateBucket")

    await ensure_bucket(s3, "botflow-audio")


async def test_an_unexpected_bucket_error_is_not_hidden() -> None:
    s3 = FakeS3()
    s3.error = client_error("AccessDenied", "CreateBucket")

    with pytest.raises(ClientError):
        await ensure_bucket(s3, "botflow-audio")
