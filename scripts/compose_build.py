"""Build the same Compose images with an explicit optional host-network grant."""

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


def build_images(compose: list[str], env: Mapping[str, str], root: Path) -> None:
    if env.get("BOTFLOW_BUILD_NETWORK") != "host":
        subprocess.run([*compose, "build"], env=env, check=True, capture_output=True)
        return
    plan = subprocess.run(
        [*compose, "build", "--print"], env=env, check=True, capture_output=True
    )
    with tempfile.TemporaryDirectory(prefix="botflow-build-") as temporary:
        bake_file = Path(temporary) / "build.json"
        bake_file.write_bytes(plan.stdout)
        subprocess.run(
            ["docker", "buildx", "bake", "--allow=network.host", "-f", str(bake_file)],
            env=env,
            cwd=root,
            check=True,
            capture_output=True,
        )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    try:
        build_images(["docker", "compose", *sys.argv[1:]], os.environ, root)
    except subprocess.CalledProcessError as error:
        # Build stages receive no credentials or secret mounts.
        print(error.stderr.decode(errors="replace")[-4000:], file=sys.stderr)
        raise SystemExit(error.returncode) from None
