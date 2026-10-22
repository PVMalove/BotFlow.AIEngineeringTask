"""RS256 session tokens: signature, issuer, audience and lifetime are enforced."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from domain.entities.auth import AuthSession, InvalidSession
from infrastructure.security.jwt import AUDIENCE, ISSUER, Rs256Tokens


def new_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def pem() -> str:
    return new_pem()


def session(lifetime: timedelta = timedelta(hours=8)) -> AuthSession:
    now = datetime.now(UTC)
    return AuthSession(uuid4(), uuid4(), created_at=now, expires_at=now + lifetime)


def test_token_carries_session_claims_for_8_hours(pem: str) -> None:
    issued = session()

    token = Rs256Tokens(pem).issue(issued)

    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "RS256"
    assert (claims["iss"], claims["aud"]) == (ISSUER, AUDIENCE)
    assert claims["sub"] == str(issued.member_id)
    assert claims["jti"] == str(issued.id)
    assert claims["exp"] - claims["iat"] == 8 * 3600
    verified = Rs256Tokens(pem).verify(token)
    assert (verified.session_id, verified.member_id) == (issued.id, issued.member_id)


def test_token_signed_by_another_key_is_rejected(pem: str) -> None:
    forged = Rs256Tokens(new_pem()).issue(session())

    with pytest.raises(InvalidSession):
        Rs256Tokens(pem).verify(forged)


def test_expired_token_is_rejected(pem: str) -> None:
    token = Rs256Tokens(pem).issue(session(lifetime=timedelta(seconds=-5)))

    with pytest.raises(InvalidSession):
        Rs256Tokens(pem).verify(token)


@pytest.mark.parametrize(
    "claims",
    [{"iss": "other"}, {"aud": "other"}, {"sub": "not-a-uuid"}, {"jti": None}],
)
def test_token_with_wrong_claims_is_rejected(pem: str, claims: dict[str, str]) -> None:
    now = datetime.now(UTC)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(uuid4()),
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    payload = {k: v for k, v in {**payload, **claims}.items() if v is not None}
    token = jwt.encode(payload, pem, algorithm="RS256")

    with pytest.raises(InvalidSession):
        Rs256Tokens(pem).verify(token)


def test_unsigned_token_is_rejected(pem: str) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        "",
        algorithm="none",
    )

    with pytest.raises(InvalidSession):
        Rs256Tokens(pem).verify(token)


def test_garbage_is_rejected(pem: str) -> None:
    with pytest.raises(InvalidSession):
        Rs256Tokens(pem).verify("a.b.c")
