"""Sessions, the pairing secret, CSRF tokens and one-shot form nonces."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: F401  (puts the package on sys.path)

from loom_ui.identity import Policy
from loom_ui.session import (COOKIE_NAME, MAX_FORMS_PER_SESSION, SessionStore, cookie_value,
                             parse_cookie)


class Clock:
    def __init__(self, start: float = 1000.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy.parse(["hritvik:owner,implementer", "rev:reviewer"])
        self.clock = Clock()
        self.store = SessionStore(self.policy, b"secret" * 8, clock=self.clock)

    def token_for(self, identity: str) -> str:
        return next(token for name, token in self.store.pairing() if name == identity)

    # -- pairing -------------------------------------------------------------

    def test_pairing_issues_a_session_for_the_token_identity(self):
        session = self.store.pair(self.token_for("hritvik"))
        self.assertIsNotNone(session)
        self.assertEqual(session.identity, "hritvik")
        self.assertEqual(len(self.store), 1)

    def test_an_unknown_or_empty_token_pairs_nobody(self):
        self.assertIsNone(self.store.pair("not-a-token"))
        self.assertIsNone(self.store.pair(""))
        self.assertIsNone(self.store.pair(None))
        self.assertEqual(len(self.store), 0)

    def test_no_configured_human_means_no_pairing_token(self):
        empty = SessionStore(Policy(()), b"x" * 32, clock=self.clock)
        self.assertEqual(empty.pairing(), ())
        self.assertIsNone(empty.pair("anything"))

    # -- cookies -------------------------------------------------------------

    def test_a_signed_cookie_resolves_and_a_forged_one_does_not(self):
        session = self.store.pair(self.token_for("hritvik"))
        value = self.store.cookie(session)
        self.assertEqual(self.store.open(value).identity, "hritvik")
        self.assertIsNone(self.store.open(value + "x"))
        self.assertIsNone(self.store.open(None))
        self.assertIsNone(self.store.open(""))
        self.assertIsNone(self.store.open(session.sid))

        other = SessionStore(self.policy, b"different" * 4, clock=self.clock)
        self.assertIsNone(other.open(value))

    def test_cookie_value_carries_an_hmac(self):
        signature = cookie_value("abc", b"key")
        self.assertTrue(signature.startswith("abc."))
        self.assertNotEqual(signature, cookie_value("abc", b"other"))

    def test_parse_cookie_finds_one_value(self):
        header = "a=1; loom_session=xyz.sig; b=2"
        self.assertEqual(parse_cookie(header, COOKIE_NAME), "xyz.sig")
        self.assertIsNone(parse_cookie("a=1", COOKIE_NAME))
        self.assertIsNone(parse_cookie(None, COOKIE_NAME))

    # -- lifetime ------------------------------------------------------------

    def test_an_idle_session_expires(self):
        session = self.store.pair(self.token_for("hritvik"))
        value = self.store.cookie(session)
        self.clock.advance(self.store.idle + 1)
        self.assertIsNone(self.store.open(value))
        self.assertEqual(len(self.store), 0)

    def test_use_refreshes_idleness_but_not_the_absolute_life(self):
        session = self.store.pair(self.token_for("hritvik"))
        value = self.store.cookie(session)
        for _ in range(3):
            self.clock.advance(self.store.idle - 10)
            self.assertIsNotNone(self.store.open(value))
        self.clock.advance(self.store.ttl + 1)
        self.assertIsNone(self.store.open(value))

    def test_close_ends_a_session(self):
        session = self.store.pair(self.token_for("hritvik"))
        value = self.store.cookie(session)
        self.store.close(session)
        self.assertIsNone(self.store.open(value))

    # -- csrf ----------------------------------------------------------------

    def test_csrf_is_compared_exactly(self):
        session = self.store.pair(self.token_for("hritvik"))
        self.assertTrue(self.store.csrf_ok(session, session.csrf))
        self.assertFalse(self.store.csrf_ok(session, session.csrf + "x"))
        self.assertFalse(self.store.csrf_ok(session, ""))
        self.assertFalse(self.store.csrf_ok(session, None))

    def test_two_sessions_do_not_share_a_csrf_token(self):
        first = self.store.pair(self.token_for("hritvik"))
        second = self.store.pair(self.token_for("hritvik"))
        self.assertNotEqual(first.csrf, second.csrf)
        self.assertFalse(self.store.csrf_ok(second, first.csrf))

    # -- form nonces ---------------------------------------------------------

    def test_a_form_nonce_is_consumed_exactly_once(self):
        session = self.store.pair(self.token_for("hritvik"))
        nonce = self.store.issue_form(session)
        self.assertTrue(self.store.consume_form(session, nonce))
        self.assertFalse(self.store.consume_form(session, nonce))
        self.assertFalse(self.store.consume_form(session, "never-issued"))
        self.assertFalse(self.store.consume_form(session, None))

    def test_a_nonce_issued_for_one_session_is_not_valid_for_another(self):
        first = self.store.pair(self.token_for("hritvik"))
        second = self.store.pair(self.token_for("rev"))
        nonce = self.store.issue_form(first)
        self.assertFalse(self.store.consume_form(second, nonce))
        self.assertTrue(self.store.consume_form(first, nonce))

    def test_the_nonce_store_is_bounded(self):
        session = self.store.pair(self.token_for("hritvik"))
        for _ in range(MAX_FORMS_PER_SESSION * 2):
            self.store.issue_form(session)
        self.assertLessEqual(len(session.forms), MAX_FORMS_PER_SESSION)

    def test_each_issued_nonce_is_distinct(self):
        session = self.store.pair(self.token_for("hritvik"))
        nonces = {self.store.issue_form(session) for _ in range(20)}
        self.assertEqual(len(nonces), 20)


if __name__ == "__main__":
    unittest.main()
