"""Secret initialization is isolated and preserves an operator's values."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


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
