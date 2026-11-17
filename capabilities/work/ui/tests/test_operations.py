"""Operations: values match the front door, and nothing on the page can change state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: E402

import engine  # noqa: E402
from loom_ui import operations, provenance, server, snapshot, templates, version  # noqa: E402

SOURCES = ("database", "process", "launches", "tick", "scheduler", "release", "logs")


def iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).isoformat(
        timespec="seconds").replace("+00:00", "Z")


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)

    # -- helpers -------------------------------------------------------------

    def collect(self, now: str | None = None):
        snap = snapshot.read(self.home.db, now=now and engine.parse_time(now))
        return snap, operations.collect(snap, version.inspect())

    def spawn(self, task="demo/W-1", role="implementer", attempt="demo/W-1#a1",
              started_at=None):
        child = subprocess.Popen(["sleep", "30"])

        def reap():
            child.kill()
            child.wait(timeout=5)

        self.addCleanup(reap)
        self.home.append(event="attempt", task=task, agent="opencode/worker", role=role,
                         attempt=attempt, state="running", now=iso())
        self.home.append(event="launch", task=task, agent="opencode/worker", role=role,
                         attempt=attempt, pid=child.pid,
                         started_at=started_at or iso(), now=iso())
        return child

    def tick_json(self, now: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(self.home.work_home / "loom"), "tick", "--json", "--now", now],
            capture_output=True, text=True, env=os.environ.copy(), timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # -- parity with the supported front door ---------------------------------

    def test_capacity_leases_and_live_attempts_match_tick_json(self):
        child = self.spawn()
        snap, ops = self.collect()
        payload = self.tick_json(snap.as_of)

        self.assertEqual(ops.capacity.active, payload["active"])
        self.assertEqual(ops.capacity.limits, payload["limits"])
        self.assertEqual([(item["task"], item["agent"], item["expired"])
                          for item in sorted(payload["expired"], key=lambda item: item["task"])],
                         [(item.task, item.agent, item.expired_at)
                          for item in sorted(ops.expired_leases, key=lambda item: item.task)])
        self.assertEqual({(item["task"], item["pid"]) for item in payload["liveAttempts"]},
                         {(item.task, item.pid) for item in ops.attempts if item.live})
        self.assertIn((("demo/W-1", child.pid)), {(item.task, item.pid)
                                                  for item in ops.attempts if item.live})

    def test_a_claim_without_an_expiry_is_expired_like_tick_says(self):
        self.home.registry["tasks"].append({
            "id": "demo/W-8", "board": "demo.board", "program": "demo",
            "title": "No expiry", "goal": "x", "status": "claimed", "revision": 1,
            "claim": {"agent": "opencode/none", "epoch": 1, "base_revision": 0},
            "owner_repo": str(self.home.repo),
        })
        self.home.save()
        snap, ops = self.collect()
        payload = self.tick_json(snap.as_of)
        self.assertIn("demo/W-8", [item["task"] for item in payload["expired"]])
        self.assertIn("demo/W-8", [item.task for item in ops.expired_leases])
        self.assertIsNone(next(item for item in ops.expired_leases
                               if item.task == "demo/W-8").expired_at)

    def test_stuck_uses_the_configured_threshold_and_the_engine_fold(self):
        old = datetime.now(timezone.utc) - timedelta(hours=6)
        child = self.spawn(started_at=iso(old))
        with unittest.mock.patch.object(engine, "process_start", return_value=old):
            _, ops = self.collect()
        self.assertEqual([item.task for item in ops.stuck], ["demo/W-1"])
        self.assertTrue(any(item.pid == child.pid for item in ops.attempts if item.live))
        threshold = engine.parse_duration(operations._config()["worker"]["stuck_after"])
        self.assertGreater(ops.stuck[0].reason.count("minute"), 0)
        self.assertIn(str(int(threshold.total_seconds() // 3600)), ops.stuck[0].reason)

    # -- failures are facts, not inferred from a quiet page -------------------

    def test_failed_launch_is_reported_with_its_reason(self):
        self.home.append(event="attempt", task="demo/W-2", agent="opencode/worker",
                         role="implementer", attempt="demo/W-2#a1", state="failed",
                         reason="launch failed: worker exited 2", now=iso())
        _, ops = self.collect()
        self.assertEqual([(item.kind, item.task) for item in ops.failures],
                         [("failed", "demo/W-2")])
        self.assertIn("exited 2", ops.failures[0].reason)
        self.assertEqual(ops.source("launches").status, "degraded")

    def test_a_failed_launch_does_not_make_capacity_unhealthy(self):
        self.home.append(event="attempt", task="demo/W-2", agent="opencode/worker",
                         role="implementer", attempt="demo/W-2#a1", state="failed",
                         reason="launch failed: worker exited 2", now=iso())
        _, ops = self.collect()
        self.assertEqual(ops.source("process").status, "ok")
        self.assertEqual(ops.capacity.active["implementer"], 0)

    def test_a_start_that_never_attached_a_process_is_stale(self):
        self.home.append(event="attempt", task="demo/W-2", agent="opencode/worker",
                         role="implementer", attempt="demo/W-2#a1", state="starting",
                         now="2026-11-15T12:00:00Z")
        _, ops = self.collect()
        self.assertEqual([item.kind for item in ops.failures], ["stale-start"])
        self.assertIn("never attached", ops.failures[0].reason)

    def test_a_claim_still_inside_the_start_grace_is_not_unhealthy(self):
        # The claim opens the attempt; the spawn attaches the process. Inside the grace the missing
        # pid is a hand-off in progress, so neither the process capacity nor the launch record may
        # call the same attempt degraded.
        self.home.append(event="attempt", task="demo/W-2", agent="opencode/worker",
                         role="implementer", attempt="demo/W-2#a1", state="starting",
                         now=iso())
        _, ops = self.collect()
        self.assertEqual(ops.source("process").status, "ok")
        self.assertEqual(ops.source("launches").status, "ok")
        self.assertEqual(ops.failures, ())
        self.assertEqual(ops.capacity.open_attempts, 1)
        self.assertEqual(ops.capacity.live_attempts, 0)

    def test_a_stale_start_degrades_both_sources_about_the_same_attempt(self):
        self.home.append(event="attempt", task="demo/W-2", agent="opencode/worker",
                         role="implementer", attempt="demo/W-2#a1", state="starting",
                         now="2026-11-15T12:00:00Z")
        _, ops = self.collect()
        self.assertEqual([item.kind for item in ops.failures], ["stale-start"])
        self.assertEqual(ops.source("process").status, "degraded")
        self.assertIn(engine.ATTEMPT_START_GRACE, ops.source("process").detail)
        self.assertEqual(ops.source("launches").status, "degraded")

    def test_the_failure_headline_counts_every_record_not_only_the_listed_ones(self):
        for index in range(25):
            self.home.append(event="attempt", task=f"demo/W-{index}", agent="opencode/worker",
                             role="implementer", attempt=f"demo/W-{index}#a1", state="failed",
                             reason="launch failed: worker exited 2", now=iso())
        _, ops = self.collect()
        self.assertEqual(len(ops.failures), operations.MAX_FAILURES)
        self.assertEqual(ops.failures_total, 25)
        self.assertEqual(ops.source("launches").data["failures"], 25)
        self.assertEqual(ops.source("launches").data["shown"], operations.MAX_FAILURES)
        self.assertEqual(ops.source("launches").data["not_shown"], 5)
        self.assertIn("25 failed", ops.source("launches").detail)
        self.assertIn("the most recent 20 are listed", ops.source("launches").detail)
        page = templates.operations_page(ops, version.inspect())
        self.assertIn("25 recorded; the most recent 20 are listed.", page)

    def test_the_attempts_headline_counts_every_record_not_only_the_listed_ones(self):
        for index in range(30):
            self.home.append(event="attempt", task=f"demo/W-{index}", agent="opencode/worker",
                             role="implementer", attempt=f"demo/W-{index}#a1", state="failed",
                             reason="launch failed: worker exited 2", now=iso())
        _, ops = self.collect()
        self.assertEqual(len(ops.attempts), operations.MAX_ATTEMPTS)
        self.assertEqual(ops.attempts_total, 30)
        self.assertEqual(ops.source("process").data["attempts_total"], 30)
        self.assertEqual(ops.source("process").data["attempts_shown"], operations.MAX_ATTEMPTS)
        self.assertEqual(ops.source("process").data["attempts_not_shown"], 5)
        self.assertIn("the most recent 25 of 30 attempt(s) are listed",
                      ops.source("process").detail)
        page = templates.operations_page(ops, version.inspect())
        self.assertIn("30 recorded; the most recent 25 are listed.", page)

    def test_a_run_whose_process_vanished_is_lost(self):
        child = subprocess.Popen(["sleep", "30"])
        self.home.append(event="attempt", task="demo/W-3", agent="opencode/worker",
                         role="implementer", attempt="demo/W-3#a1", state="running", now=iso())
        self.home.append(event="launch", task="demo/W-3", agent="opencode/worker",
                         role="implementer", attempt="demo/W-3#a1", pid=child.pid,
                         started_at=iso(), now=iso())
        child.kill()
        child.wait(timeout=5)
        _, ops = self.collect()
        self.assertEqual([item.kind for item in ops.failures], ["lost-process"])
        self.assertIn("demo/W-3", [item.task for item in ops.failures])

    # -- tick: a record with its own time, never a live pass ------------------

    def decisions(self, record: dict) -> Path:
        path = self.home.home_dir / ".local/state/loom/tick.decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n")
        return path

    def test_last_tick_pass_is_read_with_its_recorded_time(self):
        at = iso(datetime.now(timezone.utc) - timedelta(seconds=30))
        self.decisions({"ts": at, "active": {"implementer": 1},
                        "limits": {"implementer": 2}, "launched": ["demo/W-1"],
                        "reviews": [], "held": {}, "problems": []})
        snap, ops = self.collect()
        self.assertEqual(ops.tick.status, "ok")
        self.assertEqual(ops.tick.as_of, at)
        self.assertEqual(ops.tick.record["launched"], ["demo/W-1"])
        self.assertFalse(ops.tick.stale)
        self.assertEqual(ops.source("tick").as_of, at)

    def test_an_old_tick_pass_is_degraded_not_presented_as_health(self):
        self.decisions({"ts": "2026-11-15T12:00:00Z", "active": {}, "limits": {},
                        "launched": [], "reviews": [], "held": {}, "problems": []})
        _, ops = self.collect()
        self.assertEqual(ops.tick.status, "degraded")
        self.assertTrue(ops.tick.stale)
        self.assertIn("older than", ops.tick.detail)

    def test_no_tick_record_is_unavailable_and_does_not_hide_the_page(self):
        _, ops = self.collect()
        self.assertEqual(ops.tick.status, "unavailable")
        self.assertEqual(ops.source("database").status, "ok")
        self.assertIsNone(ops.tick.record)

    def test_a_malformed_tick_record_is_unavailable(self):
        path = self.home.home_dir / ".local/state/loom/tick.decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json\n")
        _, ops = self.collect()
        self.assertEqual(ops.tick.status, "unavailable")
        self.assertIn("cannot read", ops.tick.detail)

    # -- schedule, release, log ----------------------------------------------

    def test_schedule_reports_the_loaded_launchd_job(self):
        self.home.launch_agent(interval=60)
        listing = ("PID\tStatus\tLabel\n-\t0\tlocal.loom-tick\n")
        with unittest.mock.patch.object(operations, "_launchctl_listing",
                                        return_value=(listing, None)):
            _, ops = self.collect()
        self.assertEqual(ops.schedule.job, "loaded")
        self.assertEqual(ops.schedule.interval_seconds, 60)
        self.assertEqual(ops.schedule.status, "ok")
        self.assertIn("local.loom-tick", ops.schedule.launchctl)

    def test_schedule_is_degraded_when_launchd_does_not_list_the_job(self):
        self.home.launch_agent(interval=60)
        with unittest.mock.patch.object(operations, "_launchctl_listing",
                                        return_value=("PID\tStatus\tLabel\n", None)):
            _, ops = self.collect()
        self.assertEqual(ops.schedule.job, "not loaded")
        self.assertEqual(ops.schedule.status, "degraded")

    def test_the_schedule_interval_records_where_it_was_read_from(self):
        self.home.launch_agent(interval=60)
        with unittest.mock.patch.object(operations, "_launchctl_listing",
                                        return_value=("PID\tStatus\tLabel\n-\t0\tlocal.loom-tick\n",
                                                      None)):
            _, ops = self.collect()
        self.assertEqual((ops.schedule.interval_seconds, ops.schedule.interval_source),
                         (60, "plist"))

    def test_an_interval_no_plist_supplies_is_reported_with_its_source(self):
        # The front door's DEFAULTS carry schedule.tick_interval, so without a plist the value is
        # the engine's configured interval, not the plist's StartInterval. Name the source rather
        # than presenting it as observed.
        _, ops = self.collect()
        self.assertEqual((ops.schedule.interval_seconds, ops.schedule.interval_source),
                         (operations.DEFAULT_TICK_INTERVAL, "config"))
        self.assertIn(f"assumed {operations.DEFAULT_TICK_INTERVAL}s", ops.schedule.detail)
        self.assertIn("from the config", ops.schedule.detail)
        self.assertEqual(ops.tick.stale_after_seconds,
                         operations.DEFAULT_TICK_INTERVAL * operations.TICK_STALE_INTERVALS)

    def test_the_tick_interval_prefers_the_plist_then_the_config_then_the_default(self):
        plist = provenance.Signal(source="scheduler", status="ok", as_of=iso(), detail="",
                                  data={"interval_seconds": 300})
        bare = provenance.Signal(source="scheduler", status="degraded", as_of=iso(), detail="",
                                 data={})
        self.assertEqual(operations._tick_interval(plist, {}), (300, "plist"))
        self.assertEqual(
            operations._tick_interval(bare, {"schedule": {"tick_interval": 90}}), (90, "config"))
        self.assertEqual(operations._tick_interval(bare, {}),
                         (operations.DEFAULT_TICK_INTERVAL, "default"))

    def test_a_stale_tick_says_where_its_interval_came_from(self):
        self.decisions({"ts": "2026-11-15T12:00:00Z", "active": {}, "limits": {},
                        "launched": [], "reviews": [], "held": {}, "problems": []})
        _, ops = self.collect()
        self.assertTrue(ops.tick.stale)
        self.assertIn("config interval, not the installed plist", ops.tick.detail)
        self.assertIn("older than", ops.tick.detail)

    def test_the_tick_log_is_tailed_and_escaped(self):
        path = self.home.home_dir / ".local/state/loom/tick.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("first\nsecond\n<script>alert(1)</script>\n")
        _, ops = self.collect()
        self.assertEqual(ops.log.status, "ok")
        self.assertEqual(ops.log.lines[-1], "<script>alert(1)</script>")
        page = templates.operations_page(ops, version.inspect())
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script", page)

    def test_every_source_carries_its_own_time_and_state(self):
        _, ops = self.collect()
        self.assertEqual(tuple(signal.source for signal in ops.sources), SOURCES)
        for signal in ops.sources:
            self.assertTrue(signal.as_of, signal.source)
            self.assertTrue(signal.detail, signal.source)
            self.assertIn(signal.status, {"ok", "degraded", "unavailable"}, signal.source)

    def test_release_view_reuses_the_provenance_verdict(self):
        self.home.publish_release()
        _, ops = self.collect()
        self.assertEqual(ops.release.status, "ok")
        self.assertEqual(ops.release.gate_verdict, "PINNED")
        self.assertIsNotNone(ops.release.commit)


class OperationsServerTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)
        self.server = server.make_server("127.0.0.1", 0, self.home.db, version_ttl=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method="GET", path="/operations", headers=None):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_operations_api_returns_every_source_and_the_read_only_flag(self):
        status, _, raw = self.request(path="/api/operations")
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertTrue(payload["read_only"])
        self.assertEqual([item["source"] for item in payload["operations"]["sources"]],
                         list(SOURCES))
        self.assertIn("capacity", payload["operations"])
        self.assertIn("expired_leases", payload["operations"])
        self.assertIn("attempts_total", payload["operations"])

    def test_operations_page_has_no_control_surface(self):
        status, headers, raw = self.request()
        self.assertEqual(status, 200)
        body = raw.decode("utf-8")
        for fragment in ("<form", "<button", "<input", "<script", "method="):
            self.assertNotIn(fragment, body)
        self.assertIn("Capacity", body)
        self.assertIn("Expired leases", body)
        self.assertIn("read-only", body.lower())
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        # Every table carries a caption, including the key/value tick, schedule and release blocks.
        self.assertGreater(body.count("<table"), 0)
        self.assertEqual(body.count("<table"), body.count("<caption>"))

    def test_the_operations_page_is_get_only(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                status, headers, payload = self.request(method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")
                self.assertEqual(json.loads(payload)["error"], "method_not_allowed")

    def test_a_version_mismatch_closes_the_operations_view(self):
        self.stop()
        self.home.drift_engine("store.py")
        self.server = server.make_server("127.0.0.1", 0, self.home.db, version_ttl=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]
        status, _, raw = self.request()
        self.assertEqual(status, 503)
        self.assertIn("not serving", raw.decode("utf-8"))
        status, _, raw = self.request(path="/api/operations")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw)["error"], "version_gate_closed")


if __name__ == "__main__":
    unittest.main()
