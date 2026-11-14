import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import linear_sync
from linear_sync import SyncError, marker, mirror, ordered_cards


def card(task_id, dependencies=(), lane="claimable"):
    return {
        "id": task_id, "title": "Task " + task_id, "program": task_id.split("/")[0],
        "lane": lane, "owner": "Portfolio", "next_actor": "implementer",
        "reviewer": None, "blockers": list(dependencies), "dependencies": list(dependencies),
        "dependents": [], "revision": 1,
    }


class FakeLinear:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def issues(self, team_id, project_id):
        return list(self.rows.values())

    def create_issue(self, fields):
        self.writes.append(("create", fields["id"]))
        self.rows[fields["id"]] = {**fields, "url": "https://linear.app/example/issue/" + fields["id"]}
        return self.rows[fields["id"]]

    def get_issue(self, issue_id, team_id, project_id):
        return self.rows[issue_id]

    def update_issue(self, issue_id, fields):
        self.writes.append(("update", issue_id))
        self.rows[issue_id].update(fields)
        return self.rows[issue_id]


class LinearSyncTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeLinear()
        self.cards = [card("alpha/W-1", ["beta/W-1"], "blocked"), card("beta/W-1")]
        self.config = {
            "team_id": "team-id", "project_id": "project-id",
            "states": {"claimable": "Ready", "blocked": "Blocked"},
        }
        self.destination = {"state_ids": {"Ready": "ready-id", "Blocked": "blocked-id"}}

    def sync(self, apply=True):
        return mirror(self.client, self.config, self.destination, self.cards, apply=apply)

    def test_creates_in_dependency_order_and_reruns_without_writes(self):
        self.assertEqual([x["id"] for x in ordered_cards(self.cards)], ["beta/W-1", "alpha/W-1"])
        self.assertEqual(self.sync(), {"created": 2, "updated": 0, "unchanged": 0})
        rows = list(self.client.rows.values())
        self.assertIn(rows[0]["url"], rows[1]["description"])
        self.assertEqual(self.sync(), {"created": 0, "updated": 0, "unchanged": 2})
        self.assertEqual(len(self.client.writes), 2)

    def test_head_marker_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "linear.head"
            with patch.object(linear_sync, "HEAD_PATH", path):
                self.assertIsNone(linear_sync.read_head())
                linear_sync.write_head((3, "abc"))
                self.assertEqual(linear_sync.read_head(), (3, "abc"))

    def test_labels_priority_and_human_flag_are_projected(self):
        self.config["labels"] = {"alpha": "alpha-label", "Needs Human": "human-label"}
        self.cards[0]["priority"] = 2
        self.cards[0]["needs_human"] = True
        self.sync()
        row = next(row for row in self.client.rows.values()
                   if marker("alpha/W-1") in row["description"])
        self.assertEqual(row["priority"], 2)
        self.assertEqual(set(row["labelIds"]), {"alpha-label", "human-label"})
        self.assertEqual(self.sync()["unchanged"], 2)

    def test_linear_markdown_rewrites_are_not_treated_as_updates(self):
        """Linear saves `- ` bullets as `* ` and wraps link destinations in angle brackets."""
        self.sync()
        for row in self.client.rows.values():
            description = row["description"]
            description = re.sub(r"^- ", "* ", description, flags=re.MULTILINE)
            description = re.sub(r"\]\(([^)]+)\)", r"](<\1>)", description)
            row["description"] = description

        before = len(self.client.writes)
        self.assertEqual(self.sync(), {"created": 0, "updated": 0, "unchanged": 2})
        self.assertEqual(len(self.client.writes), before)

    def test_a_real_change_still_updates_after_normalisation(self):
        self.sync()
        for row in self.client.rows.values():
            row["description"] = re.sub(r"^- ", "* ", row["description"], flags=re.MULTILINE)
        self.cards[0]["lane"] = "claimable"
        self.assertEqual(self.sync()["updated"], 1)

    def test_updates_only_managed_fields_and_preserves_notes(self):
        self.sync()
        row = next(row for row in self.client.rows.values() if marker("alpha/W-1") in row["description"])
        row["description"] += "\n\nHuman notes"
        self.cards[0]["lane"] = "claimable"
        self.assertEqual(self.sync()["updated"], 1)
        self.assertTrue(row["description"].endswith("Human notes"))
        self.assertEqual(row["stateId"], "ready-id")

    def test_preview_has_no_writes_and_missing_state_fails_before_writes(self):
        self.assertEqual(self.sync(apply=False)["created"], 2)
        self.assertEqual(self.client.writes, [])
        del self.destination["state_ids"]["Blocked"]
        with self.assertRaises(SyncError):
            self.sync()
        self.assertEqual(self.client.writes, [])

    def test_duplicate_or_archived_managed_cards_are_refused(self):
        self.sync()
        issue = next(iter(self.client.rows.values()))
        self.client.rows["other"] = {**issue, "id": "other"}
        with self.assertRaisesRegex(SyncError, "multiple"):
            self.sync()
        del self.client.rows["other"]
        issue["archivedAt"] = "2026-11-08"
        with self.assertRaisesRegex(SyncError, "archived"):
            self.sync()

    def test_refuses_mismatched_marker_before_any_writes(self):
        self.sync()
        issue = next(row for row in self.client.rows.values() if marker("alpha/W-1") in row["description"])
        issue["description"] = issue["description"].replace(marker("alpha/W-1"), marker("other/W-1"))
        self.client.writes.clear()
        with self.assertRaisesRegex(SyncError, "mismatched"):
            self.sync()
        self.assertEqual(self.client.writes, [])

    def test_refreshes_description_before_updating(self):
        self.sync()
        original_get = self.client.get_issue

        def add_note(issue_id, team_id, project_id):
            issue = original_get(issue_id, team_id, project_id)
            issue["description"] += "\n\nLatest human note"
            return issue

        self.client.get_issue = add_note
        self.cards[0]["lane"] = "claimable"
        self.sync()
        row = next(row for row in self.client.rows.values() if marker("alpha/W-1") in row["description"])
        self.assertTrue(row["description"].endswith("Latest human note"))

    def test_refuses_cyclic_projection(self):
        with self.assertRaisesRegex(SyncError, "cycle"):
            ordered_cards([card("alpha/W-1", ["beta/W-1"]), card("beta/W-1", ["alpha/W-1"])])


if __name__ == "__main__":
    unittest.main()
