"""The dependency graph is a projection of the engine's edges, not a second graph.

Every parity test below compares the view to ``engine.task_edges`` / ``engine.dependents_index`` /
``engine.transitive_dependents`` directly, so a divergence between the picture and what ``loom
claim`` would decide fails here rather than on the page.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home, task as make_task  # noqa: E402

import engine  # noqa: E402
from loom_ui import graph, graph_page, model, server, snapshot  # noqa: E402


def gating_closure(registry: dict, start: str) -> set[str]:
    """Transitive dependencies of ``start``: the definition, written independently of graph.py."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        for dependency in engine.task_edges(registry, current):
            if dependency not in seen:
                seen.add(dependency)
                stack.append(dependency)
    return seen


class GraphFixture(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)
        self.home.registry["tasks"] += [
            make_task("demo/W-10", "accepted", ["src/ten"]),
            make_task("demo/W-11", "ready", ["src/eleven"], deps=["demo/W-10"]),
            make_task("demo/W-12", "ready", ["src/twelve"], deps=["demo/W-11", "demo/W-2"]),
            make_task("demo/W-13", "ready", ["src/thirteen"], deps=["other/W-1"]),
            make_task("other/W-1", "accepted", ["other/one"], program="other", board="other.board"),
            make_task("other/W-2", "ready", ["other/two"], program="other", board="other.board",
                      deps=["demo/W-10"]),
        ]
        # An advisory edge: recorded by Loom, but it does not gate a claim.
        self.home.registry["edges"].append(
            {"from": "demo/W-6", "to": "demo/W-5", "kind": "provider-parity"})
        self.home.save()

    def build(self, **kwargs):
        current = snapshot.read(self.home.db)
        return graph.build(current, **kwargs), current

    def node(self, view, task_id):
        found = view.node(task_id)
        self.assertIsNotNone(found, task_id)
        return found


class GraphDataTest(GraphFixture):
    def test_edges_are_the_engine_edges(self):
        for kwargs in ({"focus": "demo/W-12"}, {"program": "demo"}):
            with self.subTest(**kwargs):
                view, snap = self.build(**kwargs)
                ids = {node.id for node in view.nodes}
                registry = snap.registry
                for node in view.nodes:
                    expected = {dependency for dependency in engine.task_edges(registry, node.id)
                                if dependency in ids}
                    actual = {edge.dependency for edge in view.edges
                              if edge.dependent == node.id and edge.gating}
                    self.assertEqual(actual, expected, node.id)

    def test_upstream_is_the_transitive_dependency_closure(self):
        view, snap = self.build(focus="demo/W-12")
        expected = gating_closure(snap.registry, "demo/W-12")
        self.assertEqual({node.id for node in view.upstream()}, expected)
        self.assertEqual(expected, {"demo/W-11", "demo/W-10", "demo/W-2", "demo/W-1"})

    def test_downstream_is_the_engines_transitive_dependents(self):
        view, snap = self.build(focus="demo/W-1")
        expected = set(engine.transitive_dependents(engine.dependents_index(snap.registry),
                                                   "demo/W-1"))
        self.assertEqual({node.id for node in view.downstream()}, expected)
        self.assertEqual(expected, {"demo/W-2", "demo/W-12"})

    def test_distances_and_paths_are_shortest_and_real(self):
        view, snap = self.build(focus="demo/W-12")
        expected_distance = {"demo/W-11": 1, "demo/W-2": 1, "demo/W-10": 2, "demo/W-1": 2}
        for task_id, distance in expected_distance.items():
            with self.subTest(task=task_id):
                node = self.node(view, task_id)
                self.assertEqual(node.upstream_distance, distance)
                self.assertEqual(len(node.path) - 1, distance)
                for prerequisite, dependent in zip(node.path, node.path[1:]):
                    self.assertIn(prerequisite, engine.task_edges(snap.registry, dependent),
                                  f"{prerequisite} -> {dependent} is not a dependency")

    def test_focus_path_starts_at_the_focus_and_ends_at_the_dependency(self):
        view, _ = self.build(focus="demo/W-12")
        node = self.node(view, "demo/W-10")
        # prerequisite -> dependent, so the focus is the last element
        self.assertEqual(node.path[0], "demo/W-10")
        self.assertEqual(node.path[-1], "demo/W-12")
        self.assertIn("demo/W-11", node.path)

    def test_a_single_node_focus_has_no_relationships(self):
        view, _ = self.build(focus="demo/W-6")
        self.assertEqual(view.upstream(), [])
        self.assertEqual(view.downstream(), [])
        self.assertEqual([node.id for node in view.nodes], ["demo/W-6"])
        self.assertEqual(view.layers[view.focus_column], ("demo/W-6",))

    def test_program_scope_excludes_other_programs_and_keeps_the_boundary(self):
        view, snap = self.build(program="demo")
        self.assertEqual({node.program for node in view.nodes}, {"demo"})
        self.assertEqual(len(view.nodes), 11)
        boundary = {(edge.dependent, edge.dependency) for edge in view.boundary}
        self.assertEqual(boundary, {("demo/W-13", "other/W-1")})
        # the boundary edge is not drawn as an in-scope edge
        self.assertNotIn("other/W-1", {node.id for node in view.nodes})

    def test_focus_crosses_a_program_boundary_and_says_so(self):
        view, _ = self.build(focus="demo/W-13")
        outside = self.node(view, "other/W-1")
        self.assertTrue(outside.outside_program)
        self.assertFalse(self.node(view, "demo/W-13").outside_program)
        self.assertIn("other/W-1", {node.id for node in view.upstream()})

    def test_advisory_edges_are_shown_but_do_not_gate(self):
        view, _ = self.build(program="demo")
        advisory = [edge for edge in view.edges if not edge.gating]
        self.assertEqual([(edge.dependent, edge.dependency, edge.kind) for edge in advisory],
                         [("demo/W-6", "demo/W-5", "provider-parity")])

    def test_there_is_no_portfolio_wide_default(self):
        view, _ = self.build()
        self.assertEqual(view.mode, "chooser")
        self.assertEqual(view.nodes, ())
        self.assertFalse(view.drawn)
        self.assertEqual({summary.program for summary in view.programs}, {"demo", "other"})
        self.assertIn("never drawn", view.note)

    def test_tasks_without_a_program_are_grouped_and_reachable(self):
        self.home.registry["tasks"].append(
            make_task("solo/W-1", "ready", ["solo/one"], program="", board="solo.board"))
        self.home.save()
        chooser, _ = self.build()
        self.assertIn("(none)", {summary.label for summary in chooser.programs})
        view, _ = self.build(program="(none)")
        self.assertEqual([node.id for node in view.nodes], ["solo/W-1"])

    def test_a_program_too_large_to_draw_asks_for_a_focus_instead(self):
        view, _ = self.build(program="demo", limit=4)
        self.assertEqual(view.mode, "program")
        self.assertFalse(view.drawn)
        self.assertEqual(view.layers, ())
        self.assertEqual(view.omitted, 11)
        self.assertIn("Choose a focus", view.note)
        self.assertEqual(len(view.nodes), 11)  # the table is still complete

    def test_a_focus_drawing_is_bounded_but_the_table_is_not(self):
        view, _ = self.build(focus="demo/W-12", limit=3)
        self.assertTrue(view.drawn)
        self.assertEqual(len(view.nodes), 5)
        self.assertEqual(len(view.rendered), 3)
        self.assertEqual(view.omitted, 2)
        self.assertIn("demo/W-1", {node.id for node in view.nodes})
        self.assertNotIn("demo/W-1", view.rendered)
        self.assertIn("text equivalent below", view.note)

    def test_claimability_comes_from_the_shared_model(self):
        for kwargs in ({"focus": "demo/W-12"}, {"program": "demo"}):
            with self.subTest(**kwargs):
                view, snap = self.build(**kwargs)
                now = __import__("store").parse_time(snap.as_of)
                for node in view.nodes:
                    record = engine.find_task(snap.registry, node.id)
                    expected = model.claimability(snap.registry, record, now)
                    self.assertEqual(node.claimable, expected.claimable, node.id)
                    self.assertEqual(node.claimability_reasons, expected.reasons, node.id)

    def test_a_cycle_is_reported_and_does_not_hang(self):
        self.home.registry["tasks"] += [
            make_task("demo/W-20", "ready", ["src/twenty"], deps=["demo/W-21"]),
            make_task("demo/W-21", "ready", ["src/twentyone"], deps=["demo/W-20"]),
        ]
        self.home.save()
        view, _ = self.build(focus="demo/W-20")
        self.assertEqual(view.cycles, ("demo/W-21",))
        self.assertEqual(self.node(view, "demo/W-21").relation, "both")

    def test_an_unknown_dependency_is_reported_not_dropped(self):
        self.home.registry["tasks"].append(
            make_task("demo/W-22", "ready", ["src/twentytwo"], deps=["demo/absent"]))
        self.home.save()
        view, _ = self.build(focus="demo/W-22")
        self.assertTrue(view.unknown_dependencies)
        self.assertIn("demo/absent", view.unknown_dependencies[0])

    def test_a_missing_scope_raises_a_scope_error(self):
        current = snapshot.read(self.home.db)
        for kwargs in ({"program": "nope"}, {"focus": "demo/absent"},
                       {"program": "other", "focus": "demo/W-12"}):
            with self.subTest(**kwargs):
                with self.assertRaises(graph.GraphScopeError):
                    graph.build(current, **kwargs)

    def test_the_view_serialises(self):
        view, snap = self.build(focus="demo/W-12")
        payload = view.to_dict()
        self.assertEqual(payload["as_of"], snap.as_of)
        self.assertEqual(payload["mode"], "focus")
        self.assertIsInstance(payload["nodes"][0]["path"], list)
        self.assertIsInstance(payload["layers"], list)
        self.assertIsInstance(payload["programs"][0]["statuses"], list)
        json.dumps(payload, allow_nan=False)


class GraphPageTest(GraphFixture):
    def page(self, **kwargs):
        view, snap = self.build(**kwargs)
        version = server.ReadModel(self.home.db).version()
        return view, graph_page.page(view, version), snap

    def test_the_page_has_a_table_equivalent_and_no_script(self):
        view, page, _ = self.page(focus="demo/W-12")
        self.assertIn("<table", page)
        self.assertIn("Text equivalent", page)
        self.assertIn("Path (prerequisite &rarr; dependent)", page)
        self.assertNotIn("<script", page)
        for node in view.nodes:
            self.assertIn(node.id, page)

    def test_the_drawing_is_labelled_for_assistive_technology(self):
        _, page, _ = self.page(focus="demo/W-12")
        self.assertIn('role="img"', page)
        self.assertIn('aria-labelledby="dag-title dag-desc"', page)
        self.assertIn("<title id=\"dag-title\">", page)
        self.assertIn("<desc id=\"dag-desc\">", page)
        self.assertIn('tabindex="0"', page)  # the scroll container is keyboard reachable
        self.assertIn('marker-end="url(#dag-arrow)"', page)

    def test_task_text_is_escaped(self):
        self.home.registry["tasks"].append(make_task(
            "demo/W-30", "ready", ["src/thirty"], deps=["demo/W-1"],
            title='<script>alert(1)</script><img src=x onerror=alert(2)>'))
        self.home.save()
        _, page, _ = self.page(focus="demo/W-30")
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)

    def test_the_chooser_lists_programs_and_draws_nothing(self):
        view, page, _ = self.page()
        self.assertEqual(view.mode, "chooser")
        self.assertNotIn("<svg", page)
        self.assertIn("/graph?program=demo", page)
        self.assertIn("/graph?program=other", page)

    def test_a_program_with_too_many_tasks_is_listed_not_drawn(self):
        view, page, _ = self.page(program="demo", limit=4)
        self.assertNotIn("<svg", page)
        self.assertIn("more than the 4-node drawing limit", page)
        for node in view.nodes:
            self.assertIn(node.id, page)
        self.assertIn("Dependencies leaving the program", page)

    def test_every_edge_appears_in_the_edge_table(self):
        view, page, _ = self.page(program="demo")
        for edge in view.edges:
            self.assertIn(edge.dependent, page)
            self.assertIn(edge.dependency, page)
        self.assertIn("advisory, does not block", page)

    def test_the_program_page_links_back_to_the_control_room(self):
        _, page, _ = self.page(program="demo")
        self.assertIn('href="/"', page)
        self.assertIn("Read-only", page)


class GraphServerTest(GraphFixture):
    def setUp(self):
        super().setUp()
        self.server = server.make_server("127.0.0.1", 0, self.home.db, version_ttl=0.0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method="GET", headers=None):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_graph_page_renders_the_focus(self):
        status, headers, raw = self.request("/graph?task=demo/W-12")
        self.assertEqual(status, 200)
        body = raw.decode("utf-8")
        self.assertIn("Focus demo/W-12", body)
        self.assertIn("demo/W-1", body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

    def test_graph_page_defaults_to_the_chooser(self):
        status, _, raw = self.request("/graph")
        self.assertEqual(status, 200)
        self.assertIn("Choose a program", raw.decode("utf-8"))

    def test_graph_api_returns_the_read_model(self):
        status, _, raw = self.request("/api/graph?program=demo")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["graph"]["mode"], "program")
        self.assertEqual(payload["graph"]["program"], "demo")
        self.assertTrue(payload["graph"]["nodes"])

    def test_unknown_scope_is_a_404_on_both_surfaces(self):
        status, _, raw = self.request("/graph?program=nope")
        self.assertEqual(status, 404)
        self.assertIn("not in this snapshot", raw.decode("utf-8"))
        status, _, raw = self.request("/api/graph?task=demo%2Fabsent")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["error"], "unknown_scope")

    def test_the_graph_has_no_mutation_endpoint(self):
        status, headers, raw = self.request("/graph?program=demo", method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertEqual(json.loads(raw)["error"], "read_only")

    def test_a_foreign_origin_cannot_read_the_graph(self):
        status, _, raw = self.request("/graph?program=demo",
                                      headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(raw)["error"], "origin_rejected")

    def test_the_graph_fails_closed_on_a_version_mismatch(self):
        self.stop()
        self.home.drift_engine("store.py")
        self.server = server.make_server("127.0.0.1", 0, self.home.db, version_ttl=0.0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        status, _, raw = self.request("/graph?program=demo")
        self.assertEqual(status, 503)
        self.assertIn("not serving", raw.decode("utf-8"))
        status, _, raw = self.request("/api/graph?program=demo")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw)["error"], "version_gate_closed")


if __name__ == "__main__":
    unittest.main()
