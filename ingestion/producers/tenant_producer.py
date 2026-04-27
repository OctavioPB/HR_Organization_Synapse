"""Tenant-aware Kafka event producer (F6).

Wraps BaseProducer.publish() to route events to the tenant-specific topic:

    {tenant_slug}.collaboration.events.raw

Usage:
    producer = TenantAwareProducer(tenant_slug="acme-corp", inner=SlackProducer())
    producer.run(bootstrap_servers="localhost:9092")

The topic is created automatically if it doesn't exist and the Kafka admin
client has permission to do so.  Set KAFKA_AUTO_CREATE_TOPICS=true (default
in Kafka) or pre-create topics via your cluster's admin tooling.

Single-tenant deployments that set TENANT_SLUG env var will also use the
tenant-namespaced topic automatically when running producers via CLI.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterator

from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import KafkaError, TopicAlreadyExistsError

from ingestion.producers.base_producer import BaseProducer
from ingestion.schemas.collaboration_event import CollaborationEvent

logger = logging.getLogger(__name__)

_TOPIC_TEMPLATE = "{tenant_slug}.collaboration.events.raw"
_TOPIC_PARTITIONS = int(os.environ.get("KAFKA_TOPIC_PARTITIONS", "3"))
_TOPIC_REPLICATION = int(os.environ.get("KAFKA_TOPIC_REPLICATION", "1"))


def tenant_topic(tenant_slug: str) -> str:
    """Return the Kafka topic name for a given tenant slug."""
    return _TOPIC_TEMPLATE.format(tenant_slug=tenant_slug)


def ensure_topic(tenant_slug: str, bootstrap_servers: str) -> None:
    """Create the tenant topic if it does not already exist.

    Idempotent — silently ignores TopicAlreadyExistsError.
    """
    topic_name = tenant_topic(tenant_slug)
    admin = KafkaAdminClient(bootstrap_servers=bootstrap_servers.split(","))
    try:
        admin.create_topics([
            NewTopic(
                name=topic_name,
                num_partitions=_TOPIC_PARTITIONS,
                replication_factor=_TOPIC_REPLICATION,
            )
        ])
        logger.info("Created Kafka topic: %s", topic_name)
    except TopicAlreadyExistsError:
        logger.debug("Kafka topic already exists: %s", topic_name)
    except Exception as exc:
        logger.warning("Could not ensure Kafka topic %s: %s", topic_name, exc)
    finally:
        admin.close()


class TenantAwareProducer:
    """Adapts any BaseProducer to publish to a tenant-namespaced Kafka topic.

    This is a composition wrapper, not a subclass of BaseProducer, because the
    tenant dimension is orthogonal to the channel (Slack, Teams, Jira, etc.).

    Args:
        tenant_slug: Tenant identifier used to build the topic name.
        inner: The underlying channel producer (must implement BaseProducer).
        auto_create_topic: Call ensure_topic() before starting. Default: True.
    """

    def __init__(
        self,
        tenant_slug: str,
        inner: BaseProducer,
        auto_create_topic: bool = True,
    ) -> None:
        self.tenant_slug      = tenant_slug
        self.inner            = inner
        self.auto_create_topic = auto_create_topic
        self._topic           = tenant_topic(tenant_slug)

    @property
    def topic(self) -> str:
        return self._topic

    def run(self, bootstrap_servers: str, delay_ms: int = 0) -> int:
        """Connect, stream events from inner producer, publish to tenant topic.

        Returns total number of events published.
        """
        if self.auto_create_topic:
            ensure_topic(self.tenant_slug, bootstrap_servers)

        self.inner._running = True
        kafka_producer = self._build_kafka_producer(bootstrap_servers)
        count = 0

        try:
            self.inner.connect()
            for event in self.inner.stream_events():
                if not self.inner._running:
                    break
                self._publish(event, kafka_producer)
                count += 1
                if count % 100 == 0:
                    logger.info(
                        "[%s] %s: published %d events",
                        self.tenant_slug, self.inner.channel, count,
                    )
                if delay_ms > 0:
                    import time
                    time.sleep(delay_ms / 1000.0)
        except Exception as exc:
            logger.error(
                "[%s] %s producer aborted after %d events: %s",
                self.tenant_slug, self.inner.channel, count, exc,
            )
            raise
        finally:
            self.inner._running = False
            self.inner.disconnect()
            kafka_producer.flush()
            kafka_producer.close()

        logger.info(
            "[%s] %s: %d events published to topic %s",
            self.tenant_slug, self.inner.channel, count, self._topic,
        )
        return count

    def stop(self) -> None:
        """Delegate stop signal to the inner producer."""
        self.inner.stop()

    def _publish(self, event: CollaborationEvent, kafka_producer: KafkaProducer) -> None:
        """Publish one event to the tenant-namespaced topic."""
        try:
            kafka_producer.send(self._topic, value=event.model_dump(mode="json"))
        except KafkaError as exc:
            logger.error(
                "[%s] Kafka publish failed event_id=%s: %s",
                self.tenant_slug, event.event_id, exc,
            )
            raise

    @staticmethod
    def _build_kafka_producer(bootstrap_servers: str) -> KafkaProducer:
        return KafkaProducer(
            bootstrap_servers=bootstrap_servers.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
        )


# ─── CLI helper ───────────────────────────────────────────────────────────────


def make_tenant_producer(
    tenant_slug: str | None = None,
    channel: str = "slack",
) -> TenantAwareProducer:
    """Factory used by CLI scripts and Airflow tasks.

    tenant_slug defaults to TENANT_SLUG env var.
    channel selects which inner producer to instantiate.
    """
    from ingestion.producers.registry import get_producer

    slug = tenant_slug or os.environ.get("TENANT_SLUG", "")
    if not slug:
        raise ValueError(
            "tenant_slug is required. Set TENANT_SLUG env var or pass explicitly."
        )

    inner = get_producer(channel)
    return TenantAwareProducer(tenant_slug=slug, inner=inner)
