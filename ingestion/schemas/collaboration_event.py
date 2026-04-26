import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CollaborationEvent(BaseModel):
    """Metadata-only collaboration signal between two employees.

    Args:
        event_id: Unique event identifier (auto-generated UUID).
        source_employee_id: UUID of the employee initiating the interaction.
        target_employee_id: UUID of the employee receiving the interaction.
        channel: Platform where the interaction occurred.
        direction: Nature of the interaction.
        department_source: Department of the source employee.
        department_target: Department of the target employee.
        timestamp: UTC timestamp of the event.
        weight: Interaction strength multiplier (default 1.0).

    Note: No message content, file contents, or email bodies are stored.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_employee_id: str
    target_employee_id: str
    channel: Literal["slack", "email", "jira", "calendar", "github", "teams"]
    direction: Literal["sent", "mentioned", "invited", "assigned", "reviewed"]
    department_source: str
    department_target: str
    timestamp: datetime
    weight: float = Field(default=1.0, ge=0.0)
