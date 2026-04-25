from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ingestion.schemas.collaboration_event import CollaborationEvent

_NOW = datetime.now(timezone.utc)

_VALID_KWARGS = {
    "source_employee_id": "emp-001",
    "target_employee_id": "emp-002",
    "channel": "slack",
    "direction": "sent",
    "department_source": "Engineering",
    "department_target": "Sales",
    "timestamp": _NOW,
}


def test_collaboration_event_valid_defaults():
    event = CollaborationEvent(**_VALID_KWARGS)
    assert event.weight == 1.0
    assert event.event_id  # auto-generated UUID
    assert event.source_employee_id == "emp-001"


def test_collaboration_event_event_id_is_unique():
    e1 = CollaborationEvent(**_VALID_KWARGS)
    e2 = CollaborationEvent(**_VALID_KWARGS)
    assert e1.event_id != e2.event_id


def test_collaboration_event_custom_weight():
    event = CollaborationEvent(**{**_VALID_KWARGS, "weight": 2.5})
    assert event.weight == 2.5


def test_collaboration_event_invalid_channel():
    with pytest.raises(ValidationError):
        CollaborationEvent(**{**_VALID_KWARGS, "channel": "whatsapp"})


def test_collaboration_event_invalid_direction():
    with pytest.raises(ValidationError):
        CollaborationEvent(**{**_VALID_KWARGS, "direction": "liked"})


def test_collaboration_event_negative_weight_rejected():
    with pytest.raises(ValidationError):
        CollaborationEvent(**{**_VALID_KWARGS, "weight": -1.0})


def test_collaboration_event_missing_required_field():
    kwargs = {k: v for k, v in _VALID_KWARGS.items() if k != "target_employee_id"}
    with pytest.raises(ValidationError):
        CollaborationEvent(**kwargs)


@pytest.mark.parametrize("channel", ["slack", "email", "jira", "calendar", "github"])
def test_collaboration_event_all_valid_channels(channel: str):
    event = CollaborationEvent(**{**_VALID_KWARGS, "channel": channel})
    assert event.channel == channel


@pytest.mark.parametrize("direction", ["sent", "mentioned", "invited", "assigned", "reviewed"])
def test_collaboration_event_all_valid_directions(direction: str):
    event = CollaborationEvent(**{**_VALID_KWARGS, "direction": direction})
    assert event.direction == direction
