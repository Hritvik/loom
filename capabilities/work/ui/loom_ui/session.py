"""Sessions, the pairing secret, and one-shot form nonces.

Even on loopback the browser is a shared, hostile surface: any page the person visits can POST to
``127.0.0.1``. Three cheap controls make that harmless, and all three are enforced here rather than
hoped for in a template:

* **A pairing secret.** The server prints one token per configured identity when it starts. A
  session exists only because the person presented that token in a browser; the token names the
  identity server-side, so the browser never chooses who it is.
* **A signed session cookie.** The cookie carries a random id and an HMAC over it using a secret
  generated at process start. A cookie this process did not mint does not resolve to a session, and
  restarting the process invalidates every session without touching the disk.
* **A CSRF token and a one-shot form nonce.** The CSRF token is per session and compared in
  constant time. The nonce is per rendered confirmation form and is consumed by the first POST, so a
  reloaded form is refused instead of executing the action twice.

Session and nonce state is in memory only. The UI owns no durable state, and there is nothing to
migrate or clean up.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field

from .identity import Policy

COOKIE_NAME = "loom_session"
DEFAULT_TTL_SECONDS = 8 * 3600
DEFAULT_IDLE_SECONDS = 60 * 60
MAX_FORMS_PER_SESSION = 64
TOKEN_BYTES = 32


@dataclass
class Session:
    """One paired browser. The identity is fixed at pairing time and never read from a request."""

    sid: str
    identity: str
    csrf: str
    created: float
    seen: float
    forms: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.forms = dict(self.forms)


def cookie_value(sid: str, secret: bytes) -> str:
    signature = hmac.new(secret, sid.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{sid}.{signature}"


def _signature_ok(value: str, secret: bytes) -> str | None:
    sid, separator, signature = value.partition(".")
    if not separator or not sid or not signature:
        return None
    expected = hmac.new(secret, sid.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return sid


def parse_cookie(header: str | None, name: str = COOKIE_NAME) -> str | None:
    """The value of one cookie from a ``Cookie`` header, or ``None``."""
    if not header:
        return None
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value.strip() or None
    return None


class SessionStore:
    """Pairing tokens, live sessions and form nonces, all in memory."""

    def __init__(self, policy: Policy, secret: bytes | None = None,
                 ttl: float = DEFAULT_TTL_SECONDS, idle: float = DEFAULT_IDLE_SECONDS,
                 clock=time.monotonic):
        self.policy = policy
        self.secret = secret or secrets.token_bytes(TOKEN_BYTES)
        self.ttl = max(60.0, float(ttl))
        self.idle = max(60.0, min(float(idle), self.ttl))
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._tokens: dict[str, str] = {
            secrets.token_urlsafe(TOKEN_BYTES): human.identity for human in policy.humans()}

    # -- pairing -------------------------------------------------------------

    def pairing(self) -> tuple[tuple[str, str], ...]:
        """``(identity, token)`` pairs for the startup notice. Never sent to a client."""
        return tuple(sorted((identity, token) for token, identity in self._tokens.items()))

    def pair(self, token: str | None) -> Session | None:
        """Open a session for the identity a pairing token names, in constant time."""
        supplied = (token or "").strip()
        if not supplied:
            return None
        identity = None
        for candidate, name in self._tokens.items():
            if hmac.compare_digest(candidate, supplied):
                identity = name
        if identity is None or self.policy.get(identity) is None:
            return None
        now = self._clock()
        session = Session(sid=secrets.token_urlsafe(TOKEN_BYTES), identity=identity,
                          csrf=secrets.token_urlsafe(TOKEN_BYTES), created=now, seen=now)
        with self._lock:
            self._sweep(now)
            self._sessions[session.sid] = session
        return session

    # -- use -----------------------------------------------------------------

    def cookie(self, session: Session) -> str:
        return cookie_value(session.sid, self.secret)

    def open(self, cookie: str | None) -> Session | None:
        """Resolve a cookie to a live session, or ``None``. Never guesses an identity."""
        sid = _signature_ok(cookie or "", self.secret)
        if sid is None:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if (now - session.created > self.ttl or now - session.seen > self.idle
                    or self.policy.get(session.identity) is None):
                self._sessions.pop(sid, None)
                return None
            session.seen = now
            return session

    def close(self, session: Session) -> None:
        with self._lock:
            self._sessions.pop(session.sid, None)

    def csrf_ok(self, session: Session, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(session.csrf, str(supplied))

    # -- form nonces ---------------------------------------------------------

    def issue_form(self, session: Session) -> str:
        """A fresh one-shot token for one rendered confirmation form."""
        token = secrets.token_urlsafe(18)
        now = self._clock()
        with self._lock:
            if len(session.forms) >= MAX_FORMS_PER_SESSION:
                oldest = min(session.forms, key=session.forms.get)
                session.forms.pop(oldest, None)
            session.forms[token] = now
        return token

    def consume_form(self, session: Session, token: str | None) -> bool:
        """Consume a form nonce exactly once. A replayed form returns ``False``."""
        if not token:
            return False
        supplied = str(token)
        with self._lock:
            for known in list(session.forms):
                if hmac.compare_digest(known, supplied):
                    session.forms.pop(known, None)
                    return True
            return False

    def _sweep(self, now: float) -> None:
        for sid, session in list(self._sessions.items()):
            if (now - session.created > self.ttl or now - session.seen > self.idle
                    or self.policy.get(session.identity) is None):
                self._sessions.pop(sid, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


__all__ = ["COOKIE_NAME", "Session", "SessionStore", "parse_cookie", "cookie_value"]
