from pathlib import Path

import pytest

from kernel_platform.settings import Settings


def test_secret_value_is_not_in_representations(tmp_path: Path) -> None:
    (tmp_path / "minio_password").write_text("private-test-password")
    sut = Settings(secrets_dir=tmp_path)

    value = sut.secret("minio_password")

    assert value.get_secret_value() == "private-test-password"
    assert "private-test-password" not in repr(value)
    assert "private-test-password" not in repr(sut)


def test_empty_credentials_are_rejected_before_connecting(tmp_path: Path) -> None:
    (tmp_path / "minio_password").touch()
    sut = Settings(secrets_dir=tmp_path)

    with pytest.raises(ValueError, match="Empty secret file: minio_password"):
        sut.secret("minio_password")
