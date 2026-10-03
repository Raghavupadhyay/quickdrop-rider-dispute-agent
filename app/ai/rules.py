"""Rule-based reader for rider messages.

Used in two ways: as the fallback when the LLM is unavailable or returns
garbage, and as a safety net on top of the LLM (trip ids present in the text
are facts; impersonation and prompt injection are flagged regardless of what
the model thinks). It is deliberately simple; the LLM is the primary reader.
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ai.schemas import Claim, ParsedMessage


IST = ZoneInfo("Asia/Kolkata")

TRIP_ID_RE = re.compile(r"\bT\s?(\d{4,8})\b", re.IGNORECASE)
RIDER_ID_RE = re.compile(r"\bR\d{3}\b", re.IGNORECASE)
INJECTION_RE = re.compile(
    r"ignore\s+(all\s+|the\s+)?(previous|prior|above|earlier)"
    r"|^\s*system\s*:"
    r"|\bsystem\s*:\s*"
    r"|\boverride\b"
    r"|\bapprove\s+(all|karo|kar\s+do|everything|turant)"
    r"|\bsaare\s+disputes?\s+approve"
    r"|\bas\s+an?\s+(ai|assistant|admin)\b",
    re.IGNORECASE | re.MULTILINE,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*"

DATE_PATTERNS = [
    # "19 sept", "19th September", "13 sep ko"
    re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*" + MONTH_RE + r"\b", re.IGNORECASE),
    # "Sep 19", "September 19th"
    re.compile(r"\b" + MONTH_RE + r"\s*(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE),
    # "20 tarikh", "20 ko", "20 wala", "20 wale", "20 date"
    re.compile(r"\b(\d{1,2})\s*(?:tarikh|tareekh|tarik|ko|wala|wale|wali|date)\b", re.IGNORECASE),
    # "19th"
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE),
]

AMOUNT_PATTERNS = [
    re.compile(r"(?:rs\.?|₹|inr)\s*(\d{1,5})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,5})\s*(?:rs\b|rupay|rupaye|rupees|rupee|₹)", re.IGNORECASE),
    re.compile(r"\b(\d{1,5})\s*kam\b", re.IGNORECASE),
]

FOLLOW_UP_RE = re.compile(
    r"kab\s+tak|kab\s+(aayega|milega|aega|ayega)|\bstatus\b|\bthik\s+hai\b|\btheek\s+hai\b"
    r"|^\s*(ok|okay|thanks|thank\s+you|shukriya|dhanyavad|thik|theek)\b|\bkitna\s+time\b",
    re.IGNORECASE,
)
PUSHBACK_RE = re.compile(
    r"nahi\s+nahi|dobara|phir\s+se|fir\s+se|\brecheck\b|check\s+again|\bwrong\b|galat\s+(bol|keh|bata)"
    r"|de\s+do\s+na|abhi\s+de\s+do|\bbhi\s+abhi\b|\binsist|maine\s+.*\bkiye\s+the\b",
    re.IGNORECASE,
)

SPLIT_RE = re.compile(r"\s+aur\s+|\s+and\s+|\s*;\s*|\s+or\s+", re.IGNORECASE)


def find_trip_ids(text: str) -> list[str]:
    ids: list[str] = []
    for match in TRIP_ID_RE.finditer(text):
        trip_id = f"T{match.group(1)}"
        if trip_id not in ids:
            ids.append(trip_id)
    return ids


def find_other_rider_ids(text: str, rider_id: str) -> list[str]:
    return sorted(
        {m.group(0).upper() for m in RIDER_ID_RE.finditer(text)}
        - {rider_id.upper()}
    )


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(text))


def _month(token: str) -> int:
    token = token.lower()
    return 9 if token.startswith("sep") else MONTHS[token[:3]]


def find_date(text: str, received_at: datetime) -> date | None:
    received_day = received_at.astimezone(IST).date()
    lowered = text.lower()

    if re.search(r"\b(kal|yesterday)\b", lowered):
        return received_day - timedelta(days=1)
    if re.search(r"\bparso\b", lowered):
        return received_day - timedelta(days=2)
    if re.search(r"\b(aaj|today)\b", lowered):
        return received_day

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue

        groups = match.groups()
        if len(groups) == 2 and groups[0].isdigit():
            day, month = int(groups[0]), _month(groups[1])
        elif len(groups) == 2:
            month, day = _month(groups[0]), int(groups[1])
        else:
            day, month = int(groups[0]), received_day.month

        if not 1 <= day <= 31:
            continue

        year = received_day.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue

        if candidate > received_day:
            # A day-of-month after today means the previous month.
            month = month - 1 or 12
            year = year if month != 12 else year - 1
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue

        return candidate

    return None


def find_amount(text: str) -> int | None:
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            amount = int(match.group(1))
            if amount > 0:
                return amount
    return None


def classify(text: str, trip_ids: list[str]) -> str:
    lowered = text.lower()

    if re.search(r"\bsurge\b", lowered):
        return "missing_surge"
    if re.search(r"incentive|bonus|target", lowered):
        return "missing_incentive"
    if re.search(r"\bkm\b|distance|kilomet", lowered):
        return "wrong_distance"
    if re.search(r"penalty|penalti|kata|kaata|cut\b|deduct", lowered):
        if re.search(r"do\s+baar|2\s+baar|twice|double|dobara|two\s+times|zyada\s+baar|baar\s+baar", lowered):
            return "duplicate_penalty"
        if re.search(r"accident|kyun|kyu\b|why|wajah|reason|customer|galti|fault|emergency|bimar|hospital", lowered):
            return "cancellation_dispute"
        return "duplicate_penalty"
    if re.search(r"cancel", lowered) and re.search(r"accident|kyun|kyu\b|why|wajah|reason|emergency", lowered):
        return "cancellation_dispute"
    if re.search(r"nahi\s+aaya|nahi\s+mila|missing|not\s+paid|nhi\s+aaya|nahi\s+aya|nahi\s+aaye", lowered) and (
        trip_ids or re.search(r"order|trip|ride", lowered)
    ):
        return "missing_trip_payment"
    return "general_payout"


class RuleBasedExtractor:
    def extract(
        self,
        rider_id: str,
        text: str,
        received_at: datetime,
        conversation_history: list[dict] | None = None,
    ) -> ParsedMessage:
        history = conversation_history or []
        has_history = any(turn.get("from") == "agent" for turn in history)

        other_riders = find_other_rider_ids(text, rider_id)
        injection = looks_like_injection(text)

        if other_riders or injection:
            reason = (
                f"Message claims to be or speaks for another rider: {', '.join(other_riders)}"
                if other_riders
                else "Message contains instructions aimed at the system (prompt injection)"
            )
            return ParsedMessage(
                intent="other" if injection and not other_riders else "claim",
                claims=[Claim(type="unknown", claimed_amount=find_amount(text))],
                suspicious=True,
                suspicious_reason=reason,
            )

        trip_ids = find_trip_ids(text)
        day = find_date(text, received_at)

        if not trip_ids and day is None:
            if has_history and PUSHBACK_RE.search(text):
                return ParsedMessage(intent="pushback", claims=[])
            if has_history and FOLLOW_UP_RE.search(text):
                return ParsedMessage(intent="follow_up", claims=[])
            if has_history and len(text.split()) <= 4 and not re.search(r"\d", text):
                return ParsedMessage(intent="follow_up", claims=[])

            return ParsedMessage(
                intent="claim",
                claims=[Claim(type=classify(text, []), claimed_amount=find_amount(text))],
                needs_clarification=True,
                clarification_question=(
                    "Kaunse din ya kaunse order ka payout galat laga? "
                    "Date ya order ID bata dijiye."
                ),
            )

        if has_history and PUSHBACK_RE.search(text) and not trip_ids:
            # "10 wala bhi abhi de do na": insisting on something already answered.
            return ParsedMessage(intent="pushback", claims=[])

        parts = [part for part in SPLIT_RE.split(text) if part.strip()]
        claims: list[Claim] = []

        if len(parts) > 1 and all(find_trip_ids(p) or find_date(p, received_at) for p in parts):
            for part in parts:
                part_ids = find_trip_ids(part)
                part_day = find_date(part, received_at)
                claims.append(
                    Claim(
                        type=classify(part, part_ids),
                        trip_ids=part_ids,
                        date=part_day.isoformat() if part_day else None,
                        claimed_amount=find_amount(part),
                    )
                )
        else:
            claims.append(
                Claim(
                    type=classify(text, trip_ids),
                    trip_ids=trip_ids,
                    date=day.isoformat() if day else None,
                    claimed_amount=find_amount(text),
                )
            )

        return ParsedMessage(intent="claim", claims=claims)


