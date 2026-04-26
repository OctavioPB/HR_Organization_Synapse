"""Unit tests for Sprint 9 — connector framework and API endpoints.

No real Kafka, Slack, Teams, Jira, or GitHub connections are made.
All network calls are mocked via unittest.mock or httpx transport patching.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from ingestion.producers.registry import ConnectorHealth, ConnectorRegistry
from ingestion.schemas.collaboration_event import CollaborationEvent


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset the ConnectorRegistry and router-level producer singletons between tests."""
    import api.routers.connectors as conn_mod
    ConnectorRegistry.reset()
    conn_mod._slack_producer = None
    conn_mod._github_producer = None
    yield
    ConnectorRegistry.reset()
    conn_mod._slack_producer = None
    conn_mod._github_producer = None


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def registry():
    return ConnectorRegistry.get()


# ─── ConnectorHealth ──────────────────────────────────────────────────────────


def test_connector_health_as_dict_contains_all_keys():
    h = ConnectorHealth(
        channel="slack",
        enabled=True,
        healthy=True,
        events_published=42,
    )
    d = h.as_dict()
    for key in ("channel", "enabled", "healthy", "last_event_at", "events_published", "error"):
        assert key in d, f"Missing key: {key}"


def test_connector_health_last_event_at_none_when_no_events():
    h = ConnectorHealth(channel="jira", enabled=True, healthy=False)
    assert h.as_dict()["last_event_at"] is None


# ─── ConnectorRegistry ────────────────────────────────────────────────────────


def test_registry_bootstrap_from_env_disabled_by_default(registry):
    """All connectors default to enabled=False when ENABLE_* env vars are absent."""
    for health in registry.all():
        assert health.enabled is False


def test_registry_bootstrap_from_env_respects_enable_flag():
    with patch.dict(os.environ, {"ENABLE_SLACK": "true"}):
        ConnectorRegistry.reset()
        reg = ConnectorRegistry.get()
    slack = reg.get_channel("slack")
    assert slack is not None
    assert slack.enabled is True


def test_registry_register_upserts_existing_channel(registry):
    health = ConnectorHealth(channel="slack", enabled=True, healthy=True)
    registry.register(health)
    updated = ConnectorHealth(channel="slack", enabled=True, healthy=False, error="test")
    registry.register(updated)
    assert registry.get_channel("slack").healthy is False
    assert registry.get_channel("slack").error == "test"


def test_registry_set_healthy_updates_flag(registry):
    registry.register(ConnectorHealth(channel="jira", enabled=True, healthy=True))
    registry.set_healthy("jira", healthy=False, error="timeout")
    assert registry.get_channel("jira").healthy is False
    assert registry.get_channel("jira").error == "timeout"


def test_registry_record_event_increments_count(registry):
    registry.register(ConnectorHealth(channel="github", enabled=True, healthy=True))
    registry.record_event("github")
    registry.record_event("github")
    entry = registry.get_channel("github")
    assert entry.events_published == 2
    assert entry.last_event_at is not None


def test_registry_record_event_noop_for_unknown_channel(registry):
    # Must not raise for unregistered channels
    registry.record_event("nonexistent")


def test_registry_all_returns_all_channels(registry):
    channels = {h.channel for h in registry.all()}
    assert channels == {"slack", "teams", "jira", "github"}


# ─── SlackRealProducer ────────────────────────────────────────────────────────


def test_slack_producer_verify_signature_valid():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    secret = "test_signing_secret"
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))
    sig_base = f"v0:{ts}:{body.decode()}"
    expected = "v0=" + hmac.new(
        secret.encode("utf-8"), sig_base.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": secret}):
        producer = SlackRealProducer()
        assert producer.verify_signature(body, ts, expected) is True


def test_slack_producer_verify_signature_stale_timestamp():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "secret"}):
        producer = SlackRealProducer()
    stale_ts = str(int(time.time()) - 400)  # 400s ago > 300s threshold
    result = producer.verify_signature(b"body", stale_ts, "v0=anysig")
    assert result is False


def test_slack_producer_verify_signature_wrong_sig():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    ts = str(int(time.time()))
    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "real_secret"}):
        producer = SlackRealProducer()
    result = producer.verify_signature(b"body", ts, "v0=wrongsignature")
    assert result is False


def test_slack_producer_parse_webhook_ignores_bot_message():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    producer = SlackRealProducer()
    payload = {
        "event": {
            "type": "message",
            "subtype": "bot_message",
            "user": "U123",
            "channel": "C456",
            "bot_id": "B789",
        }
    }
    assert producer.parse_webhook_payload(payload) is None


def test_slack_producer_parse_webhook_reaction_added_returns_event():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    producer = SlackRealProducer()
    payload = {
        "event": {
            "type": "reaction_added",
            "user": "U_source",
            "item_user": "U_target",
            "channel": "C_channel",
        }
    }
    event = producer.parse_webhook_payload(payload)
    assert event is not None
    assert event.channel == "slack"
    assert event.direction == "mentioned"
    assert event.source_employee_id == "U_source"
    assert event.target_employee_id == "U_target"


def test_slack_producer_parse_webhook_reaction_same_user_returns_none():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    producer = SlackRealProducer()
    payload = {
        "event": {
            "type": "reaction_added",
            "user": "U_same",
            "item_user": "U_same",
            "channel": "C_channel",
        }
    }
    assert producer.parse_webhook_payload(payload) is None


def test_slack_producer_employee_map_resolves_ids():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    emp_map = {"U_source": "emp-uuid-a", "U_target": "emp-uuid-b"}
    with patch.dict(os.environ, {"SLACK_EMPLOYEE_MAP": json.dumps(emp_map)}):
        producer = SlackRealProducer()

    payload = {
        "event": {
            "type": "reaction_added",
            "user": "U_source",
            "item_user": "U_target",
            "channel": "C1",
        }
    }
    event = producer.parse_webhook_payload(payload)
    assert event.source_employee_id == "emp-uuid-a"
    assert event.target_employee_id == "emp-uuid-b"


def test_slack_producer_enqueue_and_stream():
    from ingestion.producers.slack_real_producer import SlackRealProducer

    producer = SlackRealProducer()
    fake_event = CollaborationEvent(
        source_employee_id="a",
        target_employee_id="b",
        channel="slack",
        direction="sent",
        department_source="Eng",
        department_target="Sales",
        timestamp=datetime.now(tz=timezone.utc),
    )
    producer.enqueue_webhook_event(fake_event)

    # Read one event from the generator
    producer._running = True
    gen = producer.stream_events()

    # Patch time.sleep to avoid blocking
    with patch("ingestion.producers.slack_real_producer.time.sleep", side_effect=StopIteration):
        received = next(gen)
    assert received.event_id == fake_event.event_id


# ─── GitHubProducer ───────────────────────────────────────────────────────────


def test_github_producer_verify_signature_valid():
    from ingestion.producers.github_producer import GitHubProducer

    secret = "webhook_secret"
    body = b'{"action":"submitted"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sig = f"sha256={digest}"

    with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
        producer = GitHubProducer()
    assert producer.verify_signature(body, sig) is True


def test_github_producer_verify_signature_invalid():
    from ingestion.producers.github_producer import GitHubProducer

    with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "real_secret"}):
        producer = GitHubProducer()
    assert producer.verify_signature(b"body", "sha256=wrong") is False


def test_github_producer_parse_review_submitted():
    from ingestion.producers.github_producer import GitHubProducer

    producer = GitHubProducer()
    payload = {
        "action": "submitted",
        "review": {
            "user": {"login": "reviewer_login"},
            "submitted_at": "2025-04-25T10:00:00Z",
        },
        "pull_request": {"user": {"login": "author_login"}},
        "sender": {"login": "reviewer_login"},
    }
    event = producer.parse_webhook_payload(payload)
    assert event is not None
    assert event.channel == "github"
    assert event.direction == "reviewed"
    assert event.source_employee_id == "reviewer_login"
    assert event.target_employee_id == "author_login"


def test_github_producer_parse_review_requested():
    from ingestion.producers.github_producer import GitHubProducer

    producer = GitHubProducer()
    payload = {
        "action": "review_requested",
        "pull_request": {"user": {"login": "author"}},
        "requested_reviewer": {"login": "reviewer"},
        "sender": {"login": "requester"},
        "review": {},
    }
    event = producer.parse_webhook_payload(payload)
    assert event is not None
    assert event.direction == "assigned"
    assert event.source_employee_id == "requester"
    assert event.target_employee_id == "reviewer"


def test_github_producer_parse_same_user_returns_none():
    from ingestion.producers.github_producer import GitHubProducer

    producer = GitHubProducer()
    payload = {
        "action": "submitted",
        "review": {
            "user": {"login": "same_user"},
            "submitted_at": "2025-04-25T10:00:00Z",
        },
        "pull_request": {"user": {"login": "same_user"}},
        "sender": {"login": "same_user"},
    }
    assert producer.parse_webhook_payload(payload) is None


def test_github_producer_employee_map_resolves_logins():
    from ingestion.producers.github_producer import GitHubProducer

    emp_map = {"reviewer_login": "uuid-r", "author_login": "uuid-a"}
    with patch.dict(os.environ, {"GITHUB_EMPLOYEE_MAP": json.dumps(emp_map)}):
        producer = GitHubProducer()
    payload = {
        "action": "submitted",
        "review": {"user": {"login": "reviewer_login"}, "submitted_at": "2025-04-25T10:00:00Z"},
        "pull_request": {"user": {"login": "author_login"}},
        "sender": {"login": "reviewer_login"},
    }
    event = producer.parse_webhook_payload(payload)
    assert event.source_employee_id == "uuid-r"
    assert event.target_employee_id == "uuid-a"


# ─── JiraRealProducer ─────────────────────────────────────────────────────────


def test_jira_producer_extract_edges_assignment():
    from ingestion.producers.jira_real_producer import JiraRealProducer

    with patch.dict(os.environ, {
        "JIRA_BASE_URL": "https://test.atlassian.net",
        "JIRA_EMAIL": "test@test.com",
        "JIRA_API_TOKEN": "token",
    }):
        producer = JiraRealProducer()

    issue = {
        "fields": {
            "assignee": {"accountId": "acc-assignee"},
            "reporter": {"accountId": "acc-reporter"},
            "comment": {"comments": []},
            "updated": "2025-04-25T10:00:00.000+0000",
        }
    }
    events = list(producer._extract_edges(issue))
    assert len(events) == 1
    assert events[0].direction == "assigned"
    assert events[0].source_employee_id == "acc-reporter"
    assert events[0].target_employee_id == "acc-assignee"


def test_jira_producer_extract_edges_comment_authors():
    from ingestion.producers.jira_real_producer import JiraRealProducer

    with patch.dict(os.environ, {
        "JIRA_BASE_URL": "https://test.atlassian.net",
        "JIRA_EMAIL": "test@test.com",
        "JIRA_API_TOKEN": "token",
    }):
        producer = JiraRealProducer()

    issue = {
        "fields": {
            "assignee": {"accountId": "acc-assignee"},
            "reporter": None,
            "comment": {
                "comments": [
                    {"author": {"accountId": "acc-commenter"}, "created": "2025-04-25T10:00:00.000+0000"},
                ]
            },
            "updated": "2025-04-25T10:00:00.000+0000",
        }
    }
    events = list(producer._extract_edges(issue))
    assert any(e.direction == "mentioned" for e in events)
    mention = next(e for e in events if e.direction == "mentioned")
    assert mention.source_employee_id == "acc-commenter"
    assert mention.target_employee_id == "acc-assignee"


def test_jira_producer_extract_edges_no_self_loops():
    from ingestion.producers.jira_real_producer import JiraRealProducer

    with patch.dict(os.environ, {
        "JIRA_BASE_URL": "https://test.atlassian.net",
        "JIRA_EMAIL": "test@test.com",
        "JIRA_API_TOKEN": "token",
    }):
        producer = JiraRealProducer()

    issue = {
        "fields": {
            "assignee": {"accountId": "same-user"},
            "reporter": {"accountId": "same-user"},
            "comment": {"comments": []},
            "updated": "2025-04-25T10:00:00.000+0000",
        }
    }
    events = list(producer._extract_edges(issue))
    assert events == []


# ─── GET /connectors/status ───────────────────────────────────────────────────


def test_connectors_status_returns_200(client):
    resp = client.get("/connectors/status")
    assert resp.status_code == 200


def test_connectors_status_schema(client):
    resp = client.get("/connectors/status")
    body = resp.json()
    assert "overall_healthy" in body
    assert "connector_count" in body
    assert "connectors" in body
    assert isinstance(body["connectors"], list)


def test_connectors_status_all_disabled_by_default(client):
    """When no ENABLE_* vars are set, connector_count must be 0."""
    resp = client.get("/connectors/status")
    body = resp.json()
    assert body["connector_count"] == 0
    assert body["overall_healthy"] is True  # vacuously healthy when none enabled


def test_connectors_status_shows_enabled_connector(client):
    registry = ConnectorRegistry.get()
    registry.register(ConnectorHealth(channel="slack", enabled=True, healthy=True))
    resp = client.get("/connectors/status")
    body = resp.json()
    assert body["connector_count"] == 1
    assert body["connectors"][0]["channel"] == "slack"
    assert body["connectors"][0]["healthy"] is True


# ─── POST /connectors/slack/events ────────────────────────────────────────────


def test_slack_webhook_url_verification(client):
    """Slack sends a url_verification challenge during app setup — must echo it."""
    payload = {"type": "url_verification", "challenge": "test_challenge_token"}
    resp = client.post("/connectors/slack/events", json=payload)
    assert resp.status_code == 200
    assert resp.text == "test_challenge_token"


def test_slack_webhook_invalid_signature_returns_403(client):
    """Requests with an invalid Slack signature must be rejected with 403."""
    payload = {"type": "event_callback", "event": {"type": "message"}}
    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "real_secret"}):
        resp = client.post(
            "/connectors/slack/events",
            json=payload,
            headers={
                "x-slack-request-timestamp": str(int(time.time())),
                "x-slack-signature": "v0=invalidsignature",
            },
        )
    assert resp.status_code == 403


def test_slack_webhook_valid_signature_returns_200(client):
    """A valid Slack signature must result in 200 even for ignored event types."""
    secret = "test_webhook_secret"
    body_dict = {"type": "event_callback", "event": {"type": "message", "subtype": "bot_message", "bot_id": "B1"}}
    body = json.dumps(body_dict).encode()
    ts = str(int(time.time()))
    sig_base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": secret}):
        resp = client.post(
            "/connectors/slack/events",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-slack-request-timestamp": ts,
                "x-slack-signature": sig,
            },
        )
    assert resp.status_code == 200


# ─── POST /connectors/github/webhook ─────────────────────────────────────────


def test_github_webhook_invalid_signature_returns_403(client):
    payload = {"action": "submitted"}
    with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "real_secret"}):
        resp = client.post(
            "/connectors/github/webhook",
            json=payload,
            headers={
                "x-hub-signature-256": "sha256=invalidsig",
                "x-github-event": "pull_request_review",
            },
        )
    assert resp.status_code == 403


def test_github_webhook_ignored_event_type_returns_200(client):
    """Events other than pull_request/pull_request_review must be silently ignored."""
    with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": ""}):
        resp = client.post(
            "/connectors/github/webhook",
            json={"action": "labeled"},
            headers={
                "x-hub-signature-256": "",
                "x-github-event": "issues",
            },
        )
    assert resp.status_code == 200


def test_github_webhook_pr_review_published(client):
    """A valid pull_request_review event must be parsed and published."""
    secret = "gh_secret"
    payload = {
        "action": "submitted",
        "review": {"user": {"login": "reviewer"}, "submitted_at": "2025-04-25T10:00:00Z"},
        "pull_request": {"user": {"login": "author"}},
        "sender": {"login": "reviewer"},
    }
    body = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
        with patch("api.routers.connectors._publish_to_kafka"):
            resp = client.post(
                "/connectors/github/webhook",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "x-hub-signature-256": f"sha256={digest}",
                    "x-github-event": "pull_request_review",
                },
            )
    assert resp.status_code == 200
