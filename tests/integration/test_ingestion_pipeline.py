"""Integration test: synthetic data → Kafka → consumer → Postgres.

Requires:
  - Kafka running on localhost:9092
  - Postgres running on localhost:5432 with org_synapse DB and schema applied

Run:
    pytest tests/integration/ -m integration -v

Skip in CI without Docker:
    pytest tests/ -m "not integration"
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

KAFKA_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB = os.environ.get("POSTGRES_DB", "org_synapse")
PG_USER = os.environ.get("POSTGRES_USER", "opb")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "changeme")


def _kafka_available() -> bool:
    try:
        from kafka import KafkaProducer
        p = KafkaProducer(bootstrap_servers=KAFKA_SERVERS.split(","))
        p.close()
        return True
    except Exception:
        return False


def _postgres_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
        )
        conn.close()
        return True
    except Exception:
        return False


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def require_services():
    """Skip the entire module if Kafka or Postgres is not reachable."""
    if not _kafka_available():
        pytest.skip("Kafka not available on localhost:9092 — start docker-compose first")
    if not _postgres_available():
        pytest.skip("Postgres not available on localhost:5432 — start docker-compose first")


@pytest.fixture(scope="module")
def test_employee_id(require_services) -> str:
    """Insert a throwaway employee so FK constraints pass during the test."""
    import psycopg2
    emp_id = str(uuid.uuid4())
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO employees (id, name, department, role) VALUES (%s, %s, %s, %s)",
            (emp_id, "Test Employee", "Engineering", "QA"),
        )
    conn.commit()
    conn.close()
    yield emp_id
    # Cleanup
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
    )
    with conn.cursor() as cur:
        cur.execute("DELETE FROM raw_events WHERE source_id = %s OR target_id = %s",
                    (emp_id, emp_id))
        cur.execute("DELETE FROM employees WHERE id = %s", (emp_id,))
    conn.commit()
    conn.close()


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_schema_validation_rejects_invalid_channel(require_services):
    """Consumer must dead-letter events with invalid channels."""
    from pydantic import ValidationError
    from ingestion.schemas.collaboration_event import CollaborationEvent

    with pytest.raises(ValidationError):
        CollaborationEvent(
            source_employee_id="a",
            target_employee_id="b",
            channel="carrier_pigeon",  # invalid
            direction="sent",
            department_source="Eng",
            department_target="Sales",
            timestamp=datetime.now(timezone.utc),
        )


@pytest.mark.integration
def test_produce_and_consume_100_events(test_employee_id: str):
    """Publish 100 synthetic events → consumer inserts them → verify row count in raw_events."""
    import psycopg2
    from kafka import KafkaProducer

    from ingestion.consumers.edge_consumer import consume
    from ingestion.schemas.collaboration_event import CollaborationEvent

    emp_id = test_employee_id
    topic = "collaboration.events.raw"
    n_events = 100

    # ── Publish ──────────────────────────────────────────────────────────────
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_SERVERS.split(","),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    event_ids: list[str] = []
    for _ in range(n_events):
        event = CollaborationEvent(
            source_employee_id=emp_id,
            target_employee_id=emp_id,
            channel="slack",
            direction="sent",
            department_source="Engineering",
            department_target="Engineering",
            timestamp=datetime.now(timezone.utc),
        )
        producer.send(topic, value=event.model_dump(mode="json"))
        event_ids.append(event.event_id)
    producer.flush()
    producer.close()

    # ── Consume ──────────────────────────────────────────────────────────────
    # Run consumer in a thread with max_messages limit so the test terminates
    inserted: list[int] = []

    def _run_consumer():
        n = consume(
            bootstrap_servers=KAFKA_SERVERS,
            group_id=f"test-group-{uuid.uuid4()}",
            max_messages=n_events,
        )
        inserted.append(n)

    t = threading.Thread(target=_run_consumer, daemon=True)
    t.start()
    t.join(timeout=30)

    assert inserted, "Consumer thread did not complete within 30 s"
    assert inserted[0] == n_events, (
        f"Expected {n_events} rows inserted, got {inserted[0]}"
    )

    # ── Verify in DB ─────────────────────────────────────────────────────────
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM raw_events WHERE source_id = %s AND channel = 'slack'",
            (emp_id,),
        )
        count = cur.fetchone()[0]
    conn.close()

    assert count == n_events, f"Expected {n_events} rows in raw_events, found {count}"


@pytest.mark.integration
def test_consumer_dead_letters_invalid_messages(require_services):
    """Consumer must not crash and must skip messages that fail schema validation."""
    import json
    from kafka import KafkaProducer
    from ingestion.consumers.edge_consumer import consume

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_SERVERS.split(","),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    # Publish one completely invalid message
    producer.send("collaboration.events.raw", value={"broken": True, "event_id": "bad-event"})
    producer.flush()
    producer.close()

    # Consumer should process it as a dead letter without raising
    inserted = consume(
        bootstrap_servers=KAFKA_SERVERS,
        group_id=f"test-deadletter-{uuid.uuid4()}",
        max_messages=1,
    )
    assert inserted == 0  # bad event → dead-lettered, not inserted
