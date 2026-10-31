"""OIDC bearer-token verification and role mapping.

Settings:
    AUTH_MODE        oidc (default) | dev | none
                     dev  = every request is DEV_USER_EMAIL with DEV_USER_GROUPS (local and the AWS test box only)
                     none = no user (internal-only endpoints)
    OIDC_ISSUER      e.g. https://login.microsoftonline.com/<tenant>/v2.0 or https://keycloak.example/realms/corp
    OIDC_AUDIENCE    client id / audience the token must carry
    OIDC_JWKS_URL    optional; discovered from the issuer when empty
    OIDC_GROUPS_CLAIM default "groups"
    ROLE_<NAME>      comma-separated groups that hold role NAME, e.g. ROLE_ADMIN=llmops-admins,
                     ROLE_REVIEWER_G2=legal-reviewers. Role names: admin, reviewer-g2, reviewer-g8, ...
"""
from __future__ import annotations

import json
import threading
import urllib.request
from dataclasses import dataclass, field

from .config import env, env_list

__all__ = ["Principal", "AuthError", "Authenticator", "role_groups"]


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    sub: str
    email: str | None = None
    name: str | None = None
    groups: tuple[str, ...] = ()
    claims: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def alias(self) -> str:
        """Short user name: the part of the email before @, else the subject."""
        if self.email and "@" in self.email:
            return self.email.split("@", 1)[0]
        return self.email or self.sub


def role_groups(role: str) -> frozenset[str]:
    return frozenset(env_list("ROLE_" + role.upper().replace("-", "_"), ()))


class Authenticator:
    def __init__(self, mode: str = "oidc", issuer: str | None = None, audience: str | None = None,
                 jwks_url: str | None = None, groups_claim: str = "groups", leeway: int = 60,
                 dev_email: str = "dev@example.local", dev_groups: tuple[str, ...] = ()):
        if mode not in ("oidc", "dev", "none"):
            raise ValueError(f"AUTH_MODE must be oidc, dev or none, got {mode!r}")
        if mode == "oidc" and not (issuer and audience):
            raise ValueError("AUTH_MODE=oidc needs OIDC_ISSUER and OIDC_AUDIENCE")
        self.mode = mode
        self.issuer = issuer.rstrip("/") if issuer else None
        self.audience = audience
        self._jwks_url = jwks_url
        self.groups_claim = groups_claim
        self.leeway = leeway
        self.dev_email = dev_email
        self.dev_groups = tuple(dev_groups)
        self._jwk_client = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "Authenticator":
        mode = env("AUTH_MODE", "oidc")
        return cls(
            mode=mode,
            issuer=env("OIDC_ISSUER", None),
            audience=env("OIDC_AUDIENCE", None),
            jwks_url=env("OIDC_JWKS_URL", None),
            groups_claim=env("OIDC_GROUPS_CLAIM", "groups"),
            dev_email=env("DEV_USER_EMAIL", "dev@example.local"),
            dev_groups=env_list("DEV_USER_GROUPS", ("llmops-admins",)),
        )

    # -- verification -------------------------------------------------------------------------------
    def _client(self):
        if self._jwk_client is None:
            with self._lock:
                if self._jwk_client is None:
                    import jwt

                    url = self._jwks_url
                    if not url:
                        with urllib.request.urlopen(f"{self.issuer}/.well-known/openid-configuration", timeout=10) as r:  # noqa: S310
                            url = json.load(r)["jwks_uri"]
                    self._jwk_client = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)
        return self._jwk_client

    def authenticate(self, authorization: str | None) -> Principal:
        if self.mode == "dev":
            return Principal(sub="dev-user", email=self.dev_email, name="Developer", groups=self.dev_groups)
        if self.mode == "none":
            return Principal(sub="anonymous")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        import jwt

        try:
            key = self._client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                audience=self.audience, issuer=self.issuer, leeway=self.leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {type(exc).__name__}") from exc
        groups = claims.get(self.groups_claim) or []
        if isinstance(groups, str):
            groups = groups.split()
        email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
        return Principal(sub=str(claims["sub"]), email=email, name=claims.get("name"),
                         groups=tuple(str(g) for g in groups), claims=claims)

    # -- roles --------------------------------------------------------------------------------------
    @staticmethod
    def has_role(principal: Principal, role: str) -> bool:
        groups = role_groups(role)
        return bool(groups and groups.intersection(principal.groups))
