import json
from datetime import datetime

from app.ai.client import LLMClient
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import ParsedMessage


class ClaimExtractor:
    def __init__(
        self,
        llm_client: LLMClient,
    ):
        self.llm_client = llm_client

    def extract(
        self,
        rider_id: str,
        text: str,
        received_at: datetime,
        conversation_history: list[dict] | None = None,
    ) -> ParsedMessage:
        history = conversation_history or []

        user_prompt = f"""
Rider ID:
{rider_id}

Message received at:
{received_at.isoformat()}

Previous conversation:
{json.dumps(history, ensure_ascii=False)}

Current rider message:
{text}

Return JSON exactly in this structure:

{{
  "claims": [
    {{
      "type": "missing_surge",
      "trip_ids": ["T123456"],
      "date": "2026-09-20",
      "claimed_amount": null
    }}
  ],
  "needs_clarification": false,
  "clarification_question": null
}}
"""

        raw = self.llm_client.complete_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        parsed = json.loads(raw)

        return ParsedMessage.model_validate(
            parsed
        )