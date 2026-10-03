from typing import Literal

from pydantic import BaseModel, Field


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


class Claim(BaseModel):
    type: ClaimType

    trip_ids: list[str] = Field(
        default_factory=list
    )

    date: str | None = None

    claimed_amount: int | None = None


class ParsedMessage(BaseModel):
    claims: list[Claim] = Field(
        default_factory=list
    )

    needs_clarification: bool = False

    clarification_question: str | None = None