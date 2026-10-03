import json
from dataclasses import dataclass, field
from datetime import datetime

from app.ai.client import LLMClient, LLMError
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.rules import (
    RuleBasedExtractor,
    find_other_rider_ids,
    find_trip_ids,
    looks_like_injection,
)
from app.ai.schemas import Claim, ParsedMessage


@dataclass
class ExtractionResult:
    parsed: ParsedMessage
    source: str  # "llm" | "rules"
    error: str | None = None
    raw: str | None = None
    overrides: list[str] = field(default_factory=list)


class ClaimExtractor:
    """Turns a rider message (plus the conversation so far) into claims.

    The LLM is the primary reader. Code then applies safety nets that do not
    depend on the model: trip ids written in the text are always kept,
    impersonation / prompt injection is always flagged, and a message with a
    date or trip id never asks for clarification. If the LLM is unavailable or
    returns something unusable, the rule-based reader takes over.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        rules: RuleBasedExtractor | None = None,
    ):
        self.llm_client = llm_client or LLMClient()
        self.rules = rules or RuleBasedExtractor()

    def extract(
        self,
        rider_id: str,
        text: str,
        received_at: datetime,
        conversation_history: list[dict] | None = None,
    ) -> ExtractionResult:
        history = conversation_history or []
        error: str | None = None
        raw: str | None = None

        if self.llm_client.available:
            try:
                raw = self.llm_client.complete_json(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=self._user_prompt(rider_id, text, received_at, history),
                )
                parsed = ParsedMessage.model_validate(json.loads(raw))
                result = ExtractionResult(parsed=parsed, source="llm", raw=raw)
            except (LLMError, ValueError, TypeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                result = None
        else:
            error = "LLM not configured (GROQ_API_KEY missing)"
            result = None

        if result is None:
            parsed = self.rules.extract(rider_id, text, received_at, history)
            result = ExtractionResult(parsed=parsed, source="rules", error=error)

        self._apply_safety_nets(result, rider_id, text)
        return result

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _user_prompt(rider_id: str, text: str, received_at: datetime, history: list[dict]) -> str:
        return (
            f"Rider ID (from the phone number, trusted): {rider_id}\n"
            f"Message received at: {received_at.isoformat()}\n\n"
            f"Earlier messages in this conversation (oldest first), as JSON:\n"
            f"{json.dumps(history[-10:], ensure_ascii=False)}\n\n"
            f"Current rider message:\n{text}\n\n"
            f"Return the JSON object described in the instructions."
        )

    @staticmethod
    def _apply_safety_nets(result: ExtractionResult, rider_id: str, text: str) -> None:
        parsed = result.parsed

        other_riders = find_other_rider_ids(text, rider_id)
        if other_riders and not parsed.suspicious:
            parsed.suspicious = True
            parsed.suspicious_reason = (
                f"Message speaks for another rider: {', '.join(other_riders)}"
            )
            result.overrides.append("flagged_other_rider_id")

        if looks_like_injection(text) and not parsed.suspicious:
            parsed.suspicious = True
            parsed.suspicious_reason = "Message contains instructions aimed at the system"
            result.overrides.append("flagged_injection")

        if parsed.suspicious:
            return

        # Trip ids in the text are facts; make sure none were dropped.
        text_ids = find_trip_ids(text)
        known_ids = {trip_id for claim in parsed.claims for trip_id in claim.trip_ids}
        missing_ids = [trip_id for trip_id in text_ids if trip_id not in known_ids]
        if missing_ids:
            if parsed.claims:
                parsed.claims[0].trip_ids = list(parsed.claims[0].trip_ids) + missing_ids
            else:
                parsed.claims.append(Claim(type="missing_trip_payment", trip_ids=missing_ids))
            result.overrides.append("added_trip_ids_from_text")

        if parsed.needs_clarification and parsed.investigable_claims:
            parsed.needs_clarification = False
            parsed.clarification_question = None
            result.overrides.append("cleared_clarification")
