"""Voice audio in the private S3 bucket: one object per job, no ACLs, no URLs."""

from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from application.ports import AudioMissing, TransientIoError

_MISSING_OBJECT = {"NoSuchKey", "404"}
_MISSING_BUCKET = {"404", "NoSuchBucket", "NotFound"}


class S3AudioStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @staticmethod
    def key_for(job_id: UUID) -> str:
        """Derived from the job alone, so a repeated put replaces the same object."""
        return f"voice/{job_id}"

    async def put(self, job_id: UUID, data: bytes, content_type: str | None) -> str:
        key = self.key_for(job_id)
        try:
            await self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
            )
        except Exception:
            # SDK messages can carry the endpoint and credentials.
            raise TransientIoError() from None
        return key

    async def get(self, job_id: UUID) -> bytes:
        try:
            response = await self._client.get_object(
                Bucket=self._bucket, Key=self.key_for(job_id)
            )
            async with response["Body"] as body:
                return bytes(await body.read())
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING_OBJECT:
                raise AudioMissing() from None
            raise TransientIoError() from None
        except Exception:
            raise TransientIoError() from None


async def ensure_bucket(client: Any, bucket: str) -> None:
    """Create the private audio bucket when absent; safe to repeat and to race."""
    try:
        await client.head_bucket(Bucket=bucket)
        return
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in _MISSING_BUCKET:
            raise
    try:
        await client.create_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
            raise
