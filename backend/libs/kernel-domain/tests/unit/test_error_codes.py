"""Every error code in a registry is a stable snake_case identifier."""

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).parents[4]
REGISTRIES = sorted(
    [
        *BACKEND.glob("services/*/src/domain/errors.py"),
        BACKEND / "libs/kernel-platform/src/kernel_platform/security/errors.py",
    ]
)
SNAKE_CASE = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
FACTORIES = {
    "validation",
    "not_found",
    "conflict",
    "forbidden",
    "unauthorized",
    "rate_limited",
    "problem",
}


def codes(registry: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(registry.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in FACTORIES
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Error"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found.append(str(node.args[0].value))
    return found


def test_the_registries_are_found() -> None:
    assert len(REGISTRIES) == 4
    assert all(codes(registry) for registry in REGISTRIES)


def test_every_error_code_is_snake_case() -> None:
    bad = [
        (registry.name, code)
        for registry in REGISTRIES
        for code in codes(registry)
        if not SNAKE_CASE.fullmatch(code)
    ]
    assert bad == []
