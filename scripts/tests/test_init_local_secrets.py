"""Secret initialization is isolated and preserves an operator's values."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def test_custom_secret_directory_does_not_touch_default_location(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "init-local-secrets.py"
    shutil.copyfile(Path(__file__).parents[1] / script.name, script)
    secrets = tmp_path / "isolated-secrets"
    env = {**os.environ, "BOTFLOW_SECRETS_DIR": str(secrets)}

    result = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, check=True
    )

    assert (secrets / "identity_db_password").is_file()
    assert not (root / ".secrets").exists()
    assert b"Local secret files prepared" in result.stdout


def test_second_initialization_preserves_credentials_and_acl(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "init-local-secrets.py"
    secrets = tmp_path / "secrets"
    env = {**os.environ, "BOTFLOW_SECRETS_DIR": str(secrets)}
    subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, check=True
    )
    before = {p.name: p.read_bytes() for p in secrets.iterdir()}

    result = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, check=True
    )

    assert {p.name: p.read_bytes() for p in secrets.iterdir()} == before
    assert secrets.stat().st_mode & 0o777 == 0o700
    assert all(value not in result.stdout for value in before.values() if value)


def test_jwt_private_key_is_generated_and_never_printed(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "init-local-secrets.py"
    secrets = tmp_path / "secrets"
    env = {**os.environ, "BOTFLOW_SECRETS_DIR": str(secrets)}

    result = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, check=True
    )

    key = serialization.load_pem_private_key(
        (secrets / "identity_jwt_private_key").read_bytes(), password=None
    )
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size >= 3072
    assert b"PRIVATE KEY" not in result.stdout + result.stderr


def test_existing_jwt_key_is_not_replaced(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "init-local-secrets.py"
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    operator_key = secrets / "identity_jwt_private_key"
    operator_key.write_bytes(b"operator-owned")
    env = {**os.environ, "BOTFLOW_SECRETS_DIR": str(secrets)}

    subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, check=True
    )

    assert operator_key.read_bytes() == b"operator-owned"
