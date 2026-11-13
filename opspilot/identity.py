"""Minimal identity boundary for approval decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    issuer: str
    authenticated: bool


class ActorResolver(Protocol):
    def resolve(self, actor_id: str) -> Principal | None: ...


class LocalTestActorResolver:
    """Fixed synthetic identities for local dry-run only; not an IdP."""

    _principals = {
        "local-operator": Principal("local-operator", frozenset({"operator"}), "local-test", True),
        "local-release-manager": Principal("local-release-manager", frozenset({"release_manager"}), "local-test", True),
    }

    def resolve(self, actor_id: str) -> Principal | None:
        return self._principals.get(actor_id)


def resolve_principal(resolver: ActorResolver | None, actor_id: str | None) -> Principal:
    if resolver is None:
        raise ValueError("actor_resolver_unconfigured")
    if not actor_id:
        raise ValueError("actor_identity_required")
    principal = resolver.resolve(actor_id)
    if (principal is None or not principal.authenticated or not principal.subject
            or not principal.issuer or not principal.roles):
        raise ValueError("actor_identity_unverified")
    return principal
