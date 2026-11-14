#!/usr/bin/env python3
"""Cross-surface contract tests.

The registry is reachable through three front doors: the `task` CLI, the MCP server and the A2A
façade. Each keeps its own list of capabilities, and a capability added to one and forgotten in
another is invisible until an agent hits a refusal. These tests compare the lists.

Canonical source: the MCP tool definitions in `mcp/server.py`. The A2A action map and both README
tables must agree with it, and every CLI subcommand must be reachable from the documented surface.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK / "a2a"))


def load(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


mcp = load("surface_mcp", WORK / "mcp" / "server.py")
a2a = load("surface_a2a", WORK / "a2a" / "server.py")


def table_rows(path: Path, column: str = "first") -> set[str]:
    """Tool/action names documented in a markdown table, backticks stripped."""
    names = set()
    pattern = re.compile(r"^\|\s*`([a-zA-Z0-9_/.-]+)`\s*\|", re.MULTILINE)
    for match in pattern.finditer(path.read_text(encoding="utf-8")):
        names.add(match.group(1))
    return names


class SurfaceContractTest(unittest.TestCase):
    def setUp(self):
        self.mcp_tools = {tool["name"] for tool in mcp.TOOL_DEFS}
        self.read_tools = {
            name for name, tool in ((t["name"], t) for t in mcp.TOOL_DEFS)
            if tool["annotations"].get("readOnlyHint")
        }
        self.write_tools = self.mcp_tools - self.read_tools

    def test_every_a2a_action_maps_to_an_mcp_tool(self):
        unknown = {action: tool for action, tool in a2a.ACTION_TO_TOOL.items()
                   if tool not in self.mcp_tools}
        self.assertEqual(unknown, {}, "A2A actions point at MCP tools that do not exist")

    def test_every_mcp_write_tool_has_an_a2a_action(self):
        covered = set(a2a.ACTION_TO_TOOL.values())
        self.assertEqual(self.write_tools - covered, set(),
                         "MCP write tools with no A2A equivalent")

    def test_a2a_action_sets_are_derived_from_the_map(self):
        self.assertEqual(a2a.MUTATING_ACTIONS | a2a.MESSAGE_ACTIONS, set(a2a.ACTION_TO_TOOL))
        self.assertFalse(a2a.MUTATING_ACTIONS & a2a.MESSAGE_ACTIONS)

    def test_agent_card_names_every_action(self):
        server = a2a.make_server(WORK, port=0)
        try:
            description = server.agent_card()["skills"][0]["description"]
        finally:
            server.server_close()
        for action in a2a.ACTION_TO_TOOL:
            self.assertIn(action, description, f"{action} missing from the agent card")

    def test_mcp_readme_lists_exactly_the_mcp_tools(self):
        documented = table_rows(WORK / "mcp" / "README.md")
        documented = {name for name in documented if name.startswith("work_")}
        self.assertEqual(documented, self.mcp_tools)

    def test_a2a_readme_lists_exactly_the_actions(self):
        documented = table_rows(WORK / "a2a" / "README.md")
        documented = {name for name in documented if name in a2a.ACTION_TO_TOOL}
        self.assertEqual(documented, set(a2a.ACTION_TO_TOOL))

    def test_work_readme_documents_every_cli_command(self):
        parser = load("surface_task", WORK / "engine.py")
        commands = set(parser.build_parser()._subparsers._group_actions[0].choices)
        documented = table_rows(WORK / "README.md")
        self.assertEqual(commands - documented, set(),
                         "CLI commands missing from capabilities/work/README.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
