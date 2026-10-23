"""The TypeScript models in frontend/src/api.ts carry the fields of the public views."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[5]
API_TS = ROOT / "frontend/src/api.ts"
VIEWS = {
    "Task": ("backend/services/tasks-service/src/contracts/task.py", "TaskView"),
    "Member": (
        "backend/services/identity-service/src/contracts/views.py",
        "MemberView",
    ),
    "AdminMember": (
        "backend/services/identity-service/src/contracts/views.py",
        "AdminMemberView",
    ),
}


def view_fields(path: str, name: str) -> set[str]:
    module = ast.parse((ROOT / path).read_text())
    (view,) = (
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    return {
        node.target.id
        for node in view.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def interface_fields(name: str) -> set[str]:
    source = API_TS.read_text()
    declaration = re.search(
        rf"export interface {name}(?: extends (\w+))? \{{(.*?)\n\}}", source, re.S
    )
    assert declaration is not None, name
    parent, body = declaration.groups()
    own = set(re.findall(r"^\s{2}(\w+)\??:", body, re.M))
    return own | (interface_fields(parent) if parent else set())


def test_each_typescript_model_has_exactly_the_fields_of_its_view() -> None:
    for model, (path, view) in VIEWS.items():
        assert interface_fields(model) == view_fields(path, view), model
