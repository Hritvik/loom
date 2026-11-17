"""Server-side human identity and role policy.

The browser never names the actor. An acting identity is configuration that the server loaded
before it accepted a connection: the pairing token bound to that identity, the session issued from
it, and the roles recorded here. A form field called ``agent`` or ``actor`` is not a lesser version
of that identity — it is a rejected request (see :mod:`loom_ui.actions`).

Roles are the UI's own authorization layer, not Loom's. Loom decides whether a claim is legal,
whether a lease is live, whether a transition is allowed and whether a revision is current; it does
not know that this browser's person is an owner. So the policy here answers a narrower question —
may this session be *offered* this action at all — and the engine remains the only authority on
whether it succeeds. Two gates, neither of them a copy of the other.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

#: The roles this UI knows. A configured identity must name at least one of them.
ROLES = ("implementer", "owner", "reviewer")

#: Which roles may be offered which action. ``transition`` narrows further per target state.
ACTION_ROLES: Mapping[str, frozenset[str]] = {
    "claim": frozenset({"implementer"}),
    "renew": frozenset({"implementer"}),
    "release": frozenset({"implementer"}),
    "takeover": frozenset({"implementer"}),
    "transition": frozenset({"implementer", "owner"}),
    "ask": frozenset({"implementer", "owner", "reviewer"}),
    "answer": frozenset({"implementer", "owner", "reviewer"}),
}

#: Accepting integrated work is the owner's decision, not an implementer's convenience.
TARGET_ROLES: Mapping[str, frozenset[str]] = {
    "accepted": frozenset({"owner"}),
}

#: An identity is a short, printable token: an engine agent identity or a person's handle.
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@/-]{0,63}$")
MAX_HUMANS = 32


class PolicyError(ValueError):
    """The identity configuration cannot be trusted, so the server must not start."""


@dataclass(frozen=True)
class Human:
    identity: str
    roles: frozenset[str]

    def allows(self, action: str, target: str | None = None) -> bool:
        permitted = ACTION_ROLES.get(action)
        if permitted is None:
            return False
        if not (self.roles & permitted):
            return False
        if target is not None and target in TARGET_ROLES:
            return bool(self.roles & TARGET_ROLES[target])
        return True

    def to_dict(self) -> dict:
        return {"identity": self.identity, "roles": sorted(self.roles)}


class Policy:
    """The configured humans, by identity. Empty means "this server cannot act"."""

    def __init__(self, humans: Iterable[Human] = ()):
        listed = list(humans)
        if len(listed) > MAX_HUMANS:
            raise PolicyError(f"at most {MAX_HUMANS} human identities may be configured")
        index: dict[str, Human] = {}
        for human in listed:
            if human.identity in index:
                raise PolicyError(f"identity {human.identity!r} is configured twice")
            index[human.identity] = human
        self._humans = index

    # -- construction --------------------------------------------------------

    @classmethod
    def parse(cls, specs: Iterable[str]) -> "Policy":
        """Parse repeated ``identity:role[,role]`` arguments."""
        humans: list[Human] = []
        for spec in specs:
            identity, separator, roles = str(spec).partition(":")
            identity = identity.strip()
            if not separator or not identity:
                raise PolicyError(
                    f"cannot read human identity {spec!r}; use identity:role[,role]")
            if not IDENTITY_PATTERN.match(identity):
                raise PolicyError(f"identity {identity!r} is not a printable short token")
            names = [name.strip() for name in roles.split(",") if name.strip()]
            if not names:
                raise PolicyError(f"identity {identity!r} names no roles")
            unknown = sorted(set(names) - set(ROLES))
            if unknown:
                raise PolicyError(
                    f"identity {identity!r} names unknown role(s) {', '.join(unknown)}; "
                    f"known roles are {', '.join(ROLES)}")
            humans.append(Human(identity, frozenset(names)))
        return cls(humans)

    @classmethod
    def load(cls, path: Path | None = None,
             specs: Iterable[str] = ()) -> "Policy":
        """Explicit ``--human`` arguments win; otherwise read the configuration file.

        The file is optional. Its absence is not an error and not a fallback: it means no human
        identity is configured, and the UI therefore offers no action at all.
        """
        specs = list(specs)
        if specs:
            return cls.parse(specs)
        location = _config_path(path)
        if location is None or not location.is_file():
            return cls(())
        try:
            document = json.loads(location.read_text())
        except (OSError, ValueError) as exc:
            raise PolicyError(f"cannot read the identity configuration {location}: {exc}") from exc
        if not isinstance(document, dict):
            raise PolicyError(f"{location} must contain a JSON object")
        humans = document.get("humans")
        if humans is None:
            return cls(())
        if not isinstance(humans, dict):
            raise PolicyError(f'{location}: "humans" must map identity to a list of roles')
        problems = []
        parsed: list[Human] = []
        for identity, roles in humans.items():
            name = str(identity).strip()
            if not IDENTITY_PATTERN.match(name):
                problems.append(f"identity {name!r} is not a printable short token")
                continue
            if isinstance(roles, str):
                roles = [item.strip() for item in roles.split(",")]
            if not isinstance(roles, list) or not roles:
                problems.append(f"identity {name!r} has no role list")
                continue
            unknown = sorted({str(role).strip() for role in roles} - set(ROLES))
            if unknown:
                problems.append(f"identity {name!r} names unknown role(s) {', '.join(unknown)}")
                continue
            parsed.append(Human(name, frozenset(str(role).strip() for role in roles)))
        if problems:
            raise PolicyError(f"{location}: " + "; ".join(problems))
        return cls(parsed)

    # -- accessors -----------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._humans)

    def humans(self) -> tuple[Human, ...]:
        return tuple(self._humans[key] for key in sorted(self._humans))

    def identities(self) -> frozenset[str]:
        return frozenset(self._humans)

    def get(self, identity: str | None) -> Human | None:
        if not identity:
            return None
        return self._humans.get(identity)

    def allows(self, identity: str | None, action: str, target: str | None = None) -> bool:
        human = self.get(identity)
        return bool(human and human.allows(action, target))

    def to_dict(self) -> dict:
        return {"configured": self.configured,
                "humans": [human.to_dict() for human in self.humans()]}


def _config_path(path: Path | None) -> Path | None:
    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get("LOOM_UI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config/loom/ui.json"


__all__ = ["ROLES", "ACTION_ROLES", "TARGET_ROLES", "IDENTITY_PATTERN", "PolicyError",
           "Human", "Policy"]
