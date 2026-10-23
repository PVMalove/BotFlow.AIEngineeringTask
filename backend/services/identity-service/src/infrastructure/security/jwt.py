"""RS256 session tokens; only Identity holds the private key."""

from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from kernel_domain.result import Result

from application.ports import TokenClaims
from domain.entities.auth_session import AuthSession
from domain.errors import IdentityErrors

ISSUER = "botflow-identity"
AUDIENCE = "botflow"
_REQUIRED = ["iss", "aud", "sub", "jti", "iat", "exp"]


class Rs256Tokens:
    def __init__(self, private_key_pem: str) -> None:
        key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("The JWT signing key must be an RSA private key.")
        self._private_key = key
        self._public_key = key.public_key()

    def issue(self, session: AuthSession) -> str:
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": str(session.member_id),
                "jti": str(session.id),
                "iat": int(session.created_at.timestamp()),
                "exp": int(session.expires_at.timestamp()),
            },
            self._private_key,
            algorithm="RS256",
        )

    def verify(self, token: str) -> Result[TokenClaims]:
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["RS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": _REQUIRED},
            )
            return Result[TokenClaims].ok(
                TokenClaims(
                    session_id=UUID(claims["jti"]), member_id=UUID(claims["sub"])
                )
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            return Result[TokenClaims].fail(IdentityErrors.invalid_session())
