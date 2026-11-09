"""Small Linear GraphQL client for the portfolio's one-way issue sync."""
from __future__ import annotations

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = "https://api.linear.app/graphql"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PAGE_INFO = "pageInfo { hasNextPage endCursor }"
ISSUE_FIELDS = "id title description state { id } team { id } project { id } url archivedAt"

DESTINATION = f"""
query PortfolioDestination($teamId: String!, $projectId: String!, $teamFilter: TeamFilter!, $after: String) {{
  team(id: $teamId) {{
    id name
    states(first: 50, after: $after) {{ nodes {{ id name }} {PAGE_INFO} }}
  }}
  project(id: $projectId) {{
    id name
    teams(first: 1, filter: $teamFilter) {{ nodes {{ id }} }}
  }}
}}
"""
ISSUES = f"""
query PortfolioIssues($filter: IssueFilter!, $after: String) {{
  issues(first: 50, after: $after, filter: $filter, includeArchived: true) {{
    nodes {{ {ISSUE_FIELDS} }}
    {PAGE_INFO}
  }}
}}
"""
ISSUE = f"""
query PortfolioIssue($id: String!) {{
  issue(id: $id) {{ {ISSUE_FIELDS} }}
}}
"""
CREATE = f"""
mutation PortfolioIssueCreate($input: IssueCreateInput!) {{
  issueCreate(input: $input) {{ success issue {{ {ISSUE_FIELDS} }} }}
}}
"""
UPDATE = f"""
mutation PortfolioIssueUpdate($id: String!, $input: IssueUpdateInput!) {{
  issueUpdate(id: $id, input: $input) {{ success issue {{ {ISSUE_FIELDS} }} }}
}}
"""


class LinearError(RuntimeError):
    """A safe error message for Linear request and response failures."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Redirecting an authenticated request could disclose its credentials.
        return None


def _object(value):
    if not isinstance(value, dict):
        raise LinearError("Linear API returned an unexpected response shape")
    return value


def _nodes(connection):
    nodes = _object(connection).get("nodes")
    if not isinstance(nodes, list):
        raise LinearError("Linear API returned an unexpected response shape")
    return [_object(node) for node in nodes]


def _required_text(value):
    if not isinstance(value, str) or not value.strip():
        raise LinearError("Linear API returned a missing or invalid required field")
    return value


def _issue(node):
    node = _object(node)
    description = node.get("description")
    archived_at = node.get("archivedAt")
    if any(value is not None and not isinstance(value, str) for value in (description, archived_at)):
        raise LinearError("Linear API returned an unexpected issue field")
    return {
        "id": _required_text(node.get("id")),
        "title": _required_text(node.get("title")),
        "description": description,
        "stateId": _required_text(_object(node.get("state")).get("id")),
        "url": _required_text(node.get("url")),
        "archivedAt": archived_at,
    }


class LinearClient:
    """Use a raw personal API key or an explicit Bearer OAuth authorization."""

    def __init__(self, authorization: str, *, timeout: float = 20.0):
        if not isinstance(authorization, str) or not re.fullmatch(r"(?:Bearer )?[!-~]+", authorization):
            raise LinearError("Supply a valid Linear API key or Bearer authorization")
        self._authorization = authorization
        self._timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def _request(self, query, variables):
        request = Request(ENDPOINT, method="POST", data=json.dumps({
            "query": query, "variables": variables,
        }).encode("utf-8"), headers={
            "Authorization": self._authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error.close()
            raise LinearError(f"Linear API returned HTTP {error.code}") from None
        except (OSError, HTTPException):
            raise LinearError("Could not reach the Linear API or read its response") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LinearError("Linear API response exceeded the size limit")
        try:
            payload = _object(json.loads(raw))
        except (UnicodeError, ValueError):
            raise LinearError("Linear API returned invalid JSON") from None
        if payload.get("errors"):
            raise LinearError("Linear API returned GraphQL errors")
        return _object(payload.get("data"))

    def _pages(self, query, variables, *path):
        after = None
        seen = set()
        while True:
            data = self._request(query, {**variables, "after": after})
            connection = data
            for name in path:
                connection = _object(_object(connection).get(name))
            info = _object(connection.get("pageInfo"))
            has_next = info.get("hasNextPage")
            if not isinstance(has_next, bool):
                raise LinearError("Linear API returned invalid pagination metadata")
            cursor = info.get("endCursor")
            if has_next and (not isinstance(cursor, str) or not cursor or cursor in seen):
                raise LinearError("Linear API did not advance pagination")
            yield data, _nodes(connection)
            if not has_next:
                return
            seen.add(cursor)
            after = cursor

    def destination(self, team_id, project_id):
        variables = {
            "teamId": team_id,
            "projectId": project_id,
            "teamFilter": {"id": {"eq": team_id}},
        }
        state_ids = {}
        for data, states in self._pages(DESTINATION, variables, "team", "states"):
            team = _object(data.get("team"))
            project = _object(data.get("project"))
            if team.get("id") != team_id or project.get("id") != project_id:
                raise LinearError("Linear API returned a different team or project")
            if not any(item.get("id") == team_id for item in _nodes(project.get("teams"))):
                raise LinearError("Selected Linear team is not associated with the project")
            for state in states:
                name = _required_text(state.get("name"))
                if name in state_ids:
                    raise LinearError("Selected Linear team has ambiguous workflow state names")
                state_ids[name] = _required_text(state.get("id"))
        return {
            "team_name": _required_text(team.get("name")),
            "project_name": _required_text(project.get("name")),
            "state_ids": state_ids,
        }

    def issues(self, team_id, project_id):
        variables = {"filter": {
            "team": {"id": {"eq": team_id}},
            "project": {"id": {"eq": project_id}},
        }}
        for _, nodes in self._pages(ISSUES, variables, "issues"):
            for node in nodes:
                if (not isinstance(node.get("team"), dict) or node["team"].get("id") != team_id
                        or not isinstance(node.get("project"), dict) or node["project"].get("id") != project_id):
                    raise LinearError("Linear API returned an issue outside the selected team and project")
                yield _issue(node)

    def get_issue(self, issue_id, team_id, project_id):
        node = _object(self._request(ISSUE, {"id": issue_id}).get("issue"))
        if (node.get("id") != issue_id or _object(node.get("team")).get("id") != team_id
                or _object(node.get("project")).get("id") != project_id):
            raise LinearError("Linear issue is no longer in the selected team and project")
        return _issue(node)

    def _mutation(self, query, key, variables, expected_id):
        result = _object(self._request(query, variables).get(key))
        if result.get("success") is not True:
            raise LinearError("Linear API did not confirm the issue mutation")
        issue = _issue(result.get("issue"))
        if expected_id and issue["id"] != expected_id:
            raise LinearError("Linear API returned an unexpected issue ID")
        return issue

    def create_issue(self, fields):
        return self._mutation(CREATE, "issueCreate", {"input": fields}, fields.get("id"))

    def update_issue(self, issue_id, fields):
        return self._mutation(UPDATE, "issueUpdate", {"id": issue_id, "input": fields}, issue_id)
