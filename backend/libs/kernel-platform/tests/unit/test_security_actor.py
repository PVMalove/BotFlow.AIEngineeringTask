"""ADR 0005: общий framework-independent контракт Actor/ActorRole."""

import dataclasses
import uuid

import pytest

from kernel_platform.http.errors import ApiError
from kernel_platform.security import Actor, ActorRole, require_admin


def test_actor_role_values_match_the_pre_existing_service_role_strings() -> None:
    assert ActorRole.ADMIN.value == "admin"
    assert ActorRole.USER.value == "user"


def test_actor_is_frozen() -> None:
    actor = Actor(id=uuid.uuid4(), role=ActorRole.USER)

    with pytest.raises(dataclasses.FrozenInstanceError):
        actor.role = ActorRole.ADMIN  # type: ignore[misc]


def test_actor_carries_id_and_role() -> None:
    user_id = uuid.uuid4()

    actor = Actor(id=user_id, role=ActorRole.ADMIN)

    assert actor.id == user_id
    assert actor.role is ActorRole.ADMIN


def test_require_admin_returns_the_actor_when_role_is_admin() -> None:
    actor = Actor(id=uuid.uuid4(), role=ActorRole.ADMIN)

    assert require_admin(actor) is actor


def test_require_admin_raises_forbidden_for_a_non_admin_actor() -> None:
    actor = Actor(id=uuid.uuid4(), role=ActorRole.USER)

    with pytest.raises(ApiError) as exc_info:
        require_admin(actor)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "FORBIDDEN"
