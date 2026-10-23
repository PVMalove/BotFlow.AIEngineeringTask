"""Query handlers only read: they never commit and never call a writing method."""

import ast
from pathlib import Path

BACKEND = Path(__file__).parents[4]
QUERIES = sorted(BACKEND.glob("services/*/src/application/queries/*.py"))
WRITES = {"commit", "add", "update", "delete", "flush", "claim", "enqueue", "execute"}


def writes_in(module: Path) -> list[str]:
    return [
        f"{module.name}: .{node.attr}"
        for node in ast.walk(ast.parse(module.read_text()))
        if isinstance(node, ast.Attribute) and node.attr in WRITES
    ]


def test_the_query_modules_are_found() -> None:
    assert len(QUERIES) >= 7


def test_queries_do_not_write() -> None:
    assert [item for module in QUERIES for item in writes_in(module)] == []
