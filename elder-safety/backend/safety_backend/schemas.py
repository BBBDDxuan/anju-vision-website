from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventType = Literal["suspected_fall", "fire_detected", "smoke_detected", "help_requested", "device_offline"]
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class DeviceIn(Strict):
    device_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)

class DeviceOut(DeviceIn):
    last_seen_at: str
    online: bool

class EventIn(Strict):
    event_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    device_id: str = Field(min_length=1, max_length=100)
    event_type: EventType
    severity: Literal["info", "warning", "critical"] = "critical"
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    occurred_at: datetime
    evidence_id: str | None = Field(default=None, max_length=100)
    clip_evidence_id: str | None = Field(default=None, max_length=100)
    track_id: str | None = Field(default=None, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone is required")
        return value.astimezone(timezone.utc)

    @field_validator("details")
    @classmethod
    def limit_details(cls, value):
        import json
        if len(json.dumps(value, allow_nan=False)) > 16000:
            raise ValueError("details exceeds 16000 characters")
        return value

class EventOut(EventIn):
    event_id: str
    review_status: Literal["pending", "completed", "failed", "unavailable"]
    review_summary: dict[str, Any] | None = None
    notification_status: Literal["pending", "sent", "failed", "skipped"]
    acknowledgement_status: Literal["unacknowledged", "acknowledged"]
    state: Literal["detected", "notified", "acknowledged", "resolved"]
    created_at: datetime
    deduplicated: bool = False

class PushIn(Strict):
    token: str = Field(min_length=1, max_length=4096)
    platform: Literal["android", "ios", "mock"]
    contact_id: Literal["primary", "backup"] = "primary"

class Contact(Strict):
    contact_id: Literal["primary", "backup"]
    name: str = Field(min_length=1, max_length=80)

class Preferences(Strict):
    escalation_seconds: int = Field(default=120, ge=5, le=86400)
    notifications_enabled: bool = True
    contacts: list[Contact] = Field(default_factory=lambda: [Contact(contact_id="primary", name="主要联系人"), Contact(contact_id="backup", name="备用联系人")], max_length=2)

    @model_validator(mode="after")
    def distinct(self):
        if len({c.contact_id for c in self.contacts}) != len(self.contacts):
            raise ValueError("Duplicate contact_id")
        return self

class Review(Strict):
    event_type: Literal["suspected_fall", "fire_detected", "smoke_detected", "help_requested", "device_offline", "uncertain"]
    observations: list[str] = Field(max_length=10)
    summary: str = Field(max_length=2000)
    limitations: list[str] = Field(max_length=10)
    uncertainty: Literal["low", "medium", "high"]

class EvidenceOut(Strict):
    evidence_id: str
    expires_at: str

class TokenOut(Strict):
    token_id: str

class Result(Strict):
    ok: bool = True

class EvidenceAttach(Strict):
    evidence_id: str | None = Field(default=None, min_length=1, max_length=100)
    clip_evidence_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def not_empty(self):
        if self.evidence_id is None and self.clip_evidence_id is None:
            raise ValueError("At least one evidence ID is required")
        return self
