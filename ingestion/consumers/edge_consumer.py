#!/usr/bin/env python3
"""Kafka consumer: collaboration.events.raw → PostgreSQL raw_events.

Consumes CollaborationEvent messages from Kafka, validates them against the
Pydantic schema, and batch-inserts them into raw_events. Invalid messages are
dead-lettered to the logger (not silently dropped).

Batch flush policy:
  - Flush when buffer reaches BATCH_SIZE (50 events), OR
  - Flush when FLUSH_INTERVAL_SEC (2 s) has elapsed since the last flush.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
from kafka import KafkaConsumer
from kafka.errors import KafkaError
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.db import close_pool, get_conn
from ingestion.schemas.collaboration_event import CollaborationEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOPIC = "collaboration.events.raw"
BATCH_SIZE = 50
FLUSH_INTERVAL_SEC = 2.0


def _flush_batch(batch: list[CollaborationEvent]) -> int:
    """Insert a batch of events into raw_events. Returns number actually inserted.

    Uses ON CONFLICT DO NOTHING so re-processing the same events is idempotent.
    FK violations (unknown employee IDs) cause the entire batch to be rolled back
    and logged; individual event errors are not isolated here for simplicity.
    """
    if not batch:
        return 0

    rows = [
        (e.event_id, e.source_employee_id, e.target_employee_id,
         e.channel, e.direction, e.timestamp, e.weight)
        for e in batch
    ]

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO raw_events (id, source_id, target_id, channel, direction, ts, weight)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    rows,
                    page_size=BATCH_SIZE,
                )
        logger.debug("Flushed %d events to raw_events", len(batch))
        return len(batch)
    except psycopg2.Error as exc:
        logger.error("DB flush failed: %s — %d events dropped from this batch", exc, len(batch))
        return 0


def consume(
    bootstrap_servers: str,
    group_id: str = "org-synapse-consumer",
    max_messages: int | None = None,
) -> int:
    """Run the consumer loop until stopped or max_messages reached.

    Args:
        bootstrap_servers: Comma-separated Kafka broker addresses.
        group_id: Kafka consumer group ID.
        max_messages: Stop after processing this many messages (None = forever).

    Returns:
        Total number of events inserted into raw_events.
    """
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=bootstrap_servers.split(","),
        group_id=group_id,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        consumer_timeout_ms=5_000,  # raises StopIteration after 5 s of silence
    )

    batch: list[CollaborationEvent] = []
    last_flush = time.monotonic()
    total_inserted = 0
    total_processed = 0
    total_dead = 0

    logger.info("Consumer started. Topic=%s group=%s", TOPIC, group_id)

    try:
        for message in consumer:
            raw = message.value

            try:
                event = CollaborationEvent.model_validate(raw)
            except ValidationError as exc:
                logger.warning(
                    "Dead-letter | event_id=%s | %s",
                    raw.get("event_id", "UNKNOWN"),
                    exc,
                )
                total_dead += 1
                continue

            batch.append(event)
            total_processed += 1

            elapsed = time.monotonic() - last_flush
            should_flush = len(batch) >= BATCH_SIZE or elapsed >= FLUSH_INTERVAL_SEC
            if should_flush:
                total_inserted += _flush_batch(batch)
                batch.clear()
                last_flush = time.monotonic()

            if max_messages is not None and total_processed >= max_messages:
                break

    except KafkaError as exc:
        logger.error("Kafka error: %s", exc)
    finally:
        if batch:
            total_inserted += _flush_batch(batch)
        consumer.close()
        close_pool()

    logger.info(
        "Consumer stopped | processed=%d inserted=%d dead=%d",
        total_processed, total_inserted, total_dead,
    )
    return total_inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Kafka → Postgres edge consumer")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--group-id", default="org-synapse-consumer")
    parser.add_argument("--max-messages", type=int, default=None,
                        help="Exit after N messages (default: run until Ctrl-C)")
    args = parser.parse_args()

    consume(
        bootstrap_servers=args.bootstrap_servers,
        group_id=args.group_id,
        max_messages=args.max_messages,
    )


if __name__ == "__main__":
    main()
