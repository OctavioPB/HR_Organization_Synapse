"""Abstract base class for all real-connector Kafka producers.

Each connector inherits BaseProducer and implements:
    - channel: platform identifier string
    - connect(): verify credentials, establish session
    - stream_events(): pull/receive events as CollaborationEvent objects
    - disconnect(): clean up sessions/connections

Push-based connectors (Slack, GitHub) override parse_webhook_payload() to
convert incoming HTTP payloads into CollaborationEvent objects; their
stream_events() reads from a thread-safe queue populated by the webhook handler.

Pull-based connectors (Teams, Jira) implement stream_events() as a polling loop.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Iterator

from kafka import KafkaProducer
from kafka.errors import KafkaError

from ingestion.schemas.collaboration_event import CollaborationEvent

logger = logging.getLogger(__name__)

KAFKA_TOPIC = "collaboration.events.raw"


class BaseProducer(ABC):
    """Abstract Kafka producer for collaboration metadata connectors."""

    # ── Subclass contract ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def channel(self) -> str:
        """Platform identifier, e.g. 'slack', 'github'. Must match CollaborationEvent.channel."""

    @abstractmethod
    def connect(self) -> None:
        """Verify credentials and establish any persistent session or HTTP client."""

    @abstractmethod
    def stream_events(self) -> Iterator[CollaborationEvent]:
        """Yield CollaborationEvent objects from the source.

        Poll-based: loop with sleep; push-based: block on an internal queue.
        Implementations must be interruptible (check self._running).
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Release sessions, close HTTP clients, and drain any queues."""

    @abstractmethod
    def health_check(self) -> dict:
        """Return a health dict: {channel, healthy: bool, error: str|None}.

        Must not raise; catch all exceptions internally.
        """

    # ── Webhook parsing (optional, push-based connectors) ───────────────────

    def parse_webhook_payload(self, payload: dict) -> CollaborationEvent | None:
        """Convert a raw webhook payload into a CollaborationEvent.

        Returns None if the event should be ignored (e.g., bot messages,
        irrelevant event types). Default: always returns None — override for
        webhook-based connectors.
        """
        return None

    # ── Kafka publishing ─────────────────────────────────────────────────────

    @staticmethod
    def _build_kafka_producer(bootstrap_servers: str) -> KafkaProducer:
        return KafkaProducer(
            bootstrap_servers=bootstrap_servers.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
        )

    def publish(self, event: CollaborationEvent, kafka_producer: KafkaProducer) -> None:
        """Serialize and publish one CollaborationEvent to Kafka.

        Logs and re-raises KafkaError so the caller can decide whether to
        continue streaming or abort.
        """
        try:
            kafka_producer.send(KAFKA_TOPIC, value=event.model_dump(mode="json"))
        except KafkaError as exc:
            logger.error(
                "%s: Kafka publish failed event_id=%s: %s",
                self.channel, event.event_id, exc,
            )
            raise

    # ── Long-running producer loop ────────────────────────────────────────────

    def run(self, bootstrap_servers: str, delay_ms: int = 0) -> int:
        """Connect, stream events, publish each to Kafka, then disconnect.

        Args:
            bootstrap_servers: Comma-separated Kafka broker addresses.
            delay_ms: Optional inter-event delay in milliseconds (useful for
                rate-limiting replay; set to 0 for real-time connectors).

        Returns:
            Total number of events successfully published.
        """
        self._running = True
        kafka_producer = self._build_kafka_producer(bootstrap_servers)
        count = 0
        try:
            self.connect()
            for event in self.stream_events():
                if not self._running:
                    break
                self.publish(event, kafka_producer)
                count += 1
                if count % 100 == 0:
                    logger.info("%s: published %d events", self.channel, count)
                if delay_ms > 0:
                    time.sleep(delay_ms / 1000.0)
        except Exception as exc:
            logger.error("%s producer aborted after %d events: %s", self.channel, count, exc)
            raise
        finally:
            self._running = False
            self.disconnect()
            kafka_producer.flush()
            kafka_producer.close()

        logger.info("%s producer finished: %d events published", self.channel, count)
        return count

    def stop(self) -> None:
        """Signal the run() loop to stop after the current event."""
        self._running = False
