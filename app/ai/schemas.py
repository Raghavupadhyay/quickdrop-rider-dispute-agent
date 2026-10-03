import re
from datetime import date as _date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ClaimType = Literal[
    "missing_surge",
    "missing_trip_payment",
    "missing_incentive",
    "duplicate_penalty",
    "wrong_distance",
    "cancellation_dispute",
    "general_payout",
    "unknown",
]

CLAIM_TYPES = set(ClaimType.__args__)

Intent = Literal["claim", "follow_up", "pushback", "other"]

INTENTS = set(Intent.__args__)

TRIP_ID_RE = re.compile(r"^T\d{4,8}$")


class Claim(BaseModel):
    """One thing the rider says is wrong. Only what the rider said: the type is
    a hint for the investigation, trip_ids/date say where to look."""

    model_config = ConfigDict(extra="ignore")

    type: ClaimType = "general_payout"
    trip_ids: list[str] = Field(default_factory=list)
    date: str | None = None
    claimed_amount: int | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _known_type(cls, value):
        value = str(value or "").strip().lower()
        return value if value in CLAIM_TYPES else "unknown"

    @field_validator("trip_ids", mode="before")
    @classmethod
    def _clean_trip_ids(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        cleaned: list[str] = []
        for item in value:
            candidate = str(item).strip().upper().replace(" ", "")
            if TRIP_ID_RE.match(candidate) and candidate not in cleaned:
                cleaned.append(candidate)
        return cleaned

    @field_validator("date", mode="before")
    @classmethod
    def _clean_date(cls, value):
        if not value:
            return None
        try:
            return _date.fromisoformat(str(value).strip()[:10]).isoformat()
        except ValueError:
            return None

    @field_validator("claimed_amount", mode="before")
    @classmethod
    def _clean_amount(cls, value):
        if value is None or value == "":
            return None
        try:
            amount = int(float(str(value).replace("₹", "").replace(",", "")))
        except (TypeError, ValueError):
            return None
        return amount if amount > 0 else None

    @property
    def day(self) -> _date | None:
        return _date.fromisoformat(self.date) if self.date else None

    @property
    def has_detail(self) -> bool:
        return bool(self.trip_ids or self.date)


class ParsedMessage(BaseModel):
    """What the extractor understood from one rider message, in context."""

    model_config = ConfigDict(extra="ignore")

    intent: Intent = "claim"
    claims: list[Claim] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None
    suspicious: bool = False
    suspicious_reason: str | None = None

    @field_validator("intent", mode="before")
    @classmethod
    def _known_intent(cls, value):
        value = str(value or "").strip().lower()
        return value if value in INTENTS else "claim"

    @field_validator("claims", mode="before")
    @classmethod
    def _claims_list(cls, value):
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return value

    @property
    def investigable_claims(self) -> list[Claim]:
        return [claim for claim in self.claims if claim.has_detail]
