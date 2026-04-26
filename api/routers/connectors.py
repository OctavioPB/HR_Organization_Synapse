"""Router: /connectors — connector status and inbound webhook receivers.

Endpoints
---------
GET  /connectors/status
    Returns health state for all configured connectors.

POST /connectors/slack/events
    Receives Slack Events API webhook payloads. Handles URL verification
    challenge and converts message events into CollaborationEvent objects
    pushed to Kafka.

POST /connectors/github/webhook
    Receives GitHub webhook payloads (pull_request, pull_request_review).
    Verifies HMAC-SHA256 signature and converts to CollaborationEvent objects.

Each webhook route:
  1. Verifies the platform's signature (constant-time HMAC comparison).
  2. Calls the producer's parse_webhook_payload() to extract metadata.
  3. Publishes to Kafka if parse returns a non-None event.
  4. Returns 200 immediately (Slack and GitHub require fast acknowledgement).
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from ingestion.producers.github_producer import GitHubProducer
from ingestion.producers.registry import ConnectorRegistry
from ingestion.producers.slack_real_producer import SlackRealProducer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["connectors"])

_KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# Module-level producer singletons — initialized lazily on first webhook arrival.
# Using singletons avoids re-creating Kafka producers on every request.
_slack_producer: SlackRealProducer | None = None
_github_producer: GitHubProducer | None = None


def _get_slack_producer() -> SlackRealProducer:
    global _slack_producer
    if _slack_producer is None:
        _slack_producer = SlackRealProducer()
    return _slack_producer


def _get_github_producer() -> GitHubProducer:
    global _github_producer
    if _github_producer is None:
        _github_producer = GitHubProducer()
    return _github_producer


# ─── GET /connectors/status ───────────────────────────────────────────────────


@router.get("/status")
def get_connector_status() -> dict:
    """Health status for all configured connectors.

    Returns a dict with a `connectors` list and an `overall_healthy` flag.
    A connector is included only if it is enabled via its ENABLE_* env var.
    """
    registry = ConnectorRegistry.get()
    all_health = registry.all()
    enabled = [h.as_dict() for h in all_health if h.enabled]
    overall = all(h.healthy for h in all_health if h.enabled) if enabled else True
    return {
        "overall_healthy": overall,
        "connector_count": len(enabled),
        "connectors": enabled,
    }


# ─── POST /connectors/slack/events ───────────────────────────────────────────


@router.post("/slack/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
) -> Response:
    """Receive Slack Events API webhook.

    Handles two event categories:
      - url_verification: returns the challenge value (required on first setup).
      - event_callback: extracts metadata and publishes to Kafka.

    Slack requires a response within 3 seconds. Kafka publishing is done in
    the background so the HTTP response is immediate.
    """
    body = await request.body()
    payload = await request.json()

    producer = _get_slack_producer()

    # Slack URL verification challenge (one-time handshake during app setup)
    if payload.get("type") == "url_verification":
        return Response(
            content=payload.get("challenge", ""),
            media_type="text/plain",
        )

    # Verify HMAC signature for all other event types
    if not producer.verify_signature(body, x_slack_request_timestamp, x_slack_signature):
        logger.warning("Slack webhook: signature verification failed")
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # Parse and publish in background to meet Slack's 3-second response requirement
    event = producer.parse_webhook_payload(payload)
    if event is not None:
        background_tasks.add_task(_publish_to_kafka, "slack", event)

    return Response(status_code=200)


# ─── POST /connectors/github/webhook ─────────────────────────────────────────


@router.post("/github/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> Response:
    """Receive GitHub webhook payload.

    Verifies the HMAC-SHA256 signature before processing.
    Handles pull_request and pull_request_review event types.
    All other event types are acknowledged (200) and silently ignored.
    """
    body = await request.body()

    producer = _get_github_producer()

    if not producer.verify_signature(body, x_hub_signature_256):
        logger.warning("GitHub webhook: signature verification failed event_type=%s", x_github_event)
        raise HTTPException(status_code=403, detail="Invalid GitHub signature")

    if x_github_event not in ("pull_request", "pull_request_review"):
        # Acknowledge and ignore — GitHub sends many event types we don't need
        return Response(status_code=200)

    payload = await request.json()
    event = producer.parse_webhook_payload(payload)
    if event is not None:
        background_tasks.add_task(_publish_to_kafka, "github", event)

    return Response(status_code=200)


# ─── Shared background task ───────────────────────────────────────────────────


def _publish_to_kafka(channel: str, event) -> None:
    """Publish a single CollaborationEvent to Kafka.

    Runs in a FastAPI BackgroundTask — errors are logged but not re-raised
    so webhook acknowledgement is never blocked by Kafka issues.
    """
    try:
        from ingestion.producers.base_producer import BaseProducer
        kafka_producer = BaseProducer._build_kafka_producer(_KAFKA_BOOTSTRAP)
        try:
            kafka_producer.send(
                "collaboration.events.raw",
                value=json.dumps(event.model_dump(mode="json")).encode("utf-8"),
            )
            kafka_producer.flush()
            ConnectorRegistry.get().record_event(channel)
            logger.debug("%s: published event_id=%s", channel, event.event_id)
        finally:
            kafka_producer.close()
    except Exception as exc:
        logger.error("%s: Kafka publish failed for event_id=%s: %s", channel, event.event_id, exc)
