"""The dispute agent: one rider message in, one reply out.

What the model decides: what the rider is talking about (claims: type, trip
ids, date), whether the message is a follow-up / pushback, whether it is
suspicious, and how to ask for missing details.

What code decides: everything about money and records. Which tool to run,
what is owed, the 7-day window, the ₹200 / once-a-day rule, PaySwift
reconciliation, approvals, escalations, and the wording of every fact.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.ai.extractor import ClaimExtractor
from app.ai.schemas import Claim
from app.clients.payswift import PaySwiftClient
from app.db.models import Message, TraceStep
from app.services import replies
from app.services.investigator import DayAudit, Finding, Investigator, TripCheck
from app.services.payment_service import PaymentService
from app.services.trace_service import Tracer


IST = ZoneInfo("Asia/Kolkata")


def ist_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment.astimezone(IST).date()


@dataclass
class ClaimOutcome:
    claim: Claim
    sentences: list[str] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    label: str = ""


class DisputeAgent:
    def __init__(
        self,
        extractor: ClaimExtractor,
        investigator: Investigator,
        payswift_factory=PaySwiftClient,
        history_limit: int = 10,
    ):
        self.extractor = extractor
        self.investigator = investigator
        self.payswift_factory = payswift_factory
        self.history_limit = history_limit

    # ------------------------------------------------------------------ memory

    def conversation_history(self, db, rider_id: str, exclude_message_id: str) -> list[dict]:
        rows = (
            db.query(Message)
            .filter(Message.rider_id == rider_id, Message.message_id != exclude_message_id)
            .order_by(Message.received_at.asc(), Message.id.asc())
            .all()
        )
        turns: list[dict] = []
        for row in rows[-self.history_limit:]:
            turns.append({"from": "rider", "text": row.text})
            if row.reply:
                turns.append({"from": "agent", "text": row.reply})
        return turns

    def previous_claims(self, db, rider_id: str, exclude_message_id: str) -> list[Claim]:
        """Claims from the last message we understood: the agent's memory of
        what the conversation is about, for 'dobara check karo' style replies."""
        steps = (
            db.query(TraceStep)
            .filter(
                TraceStep.rider_id == rider_id,
                TraceStep.type == "tool_call",
                TraceStep.name == "extract_claims",
                TraceStep.message_id != exclude_message_id,
            )
            .order_by(TraceStep.at.desc(), TraceStep.id.desc())
            .limit(5)
            .all()
        )
        for step in steps:
            claims = [Claim.model_validate(c) for c in (step.output or {}).get("claims", [])]
            claims = [c for c in claims if c.has_detail]
            if claims:
                return claims
        return []

    # ----------------------------------------------------------------- handling

    def handle(self, db, message: Message, deadline: float | None = None) -> str:
        """deadline: time.monotonic() value by which the reply must be ready
        (the vendor retries after ~10s); payouts still running at that point
        are answered as 'processing' and finished by the finalizer."""
        rider_id = message.rider_id
        tracer = Tracer(db, rider_id, message.message_id)
        received_at = message.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=IST)
        as_of = ist_day(received_at)

        tracer.message_in(
            "rider_message",
            {"message_id": message.message_id, "text": message.text, "received_at": received_at.isoformat()},
        )

        history = self.conversation_history(db, rider_id, message.message_id)
        payments = PaymentService(db, self.payswift_factory(), tracer, deadline=deadline)

        extraction = self.extractor.extract(rider_id, message.text, received_at, history)
        if extraction.error:
            tracer.error("extract_claims_llm", {"text": message.text}, {"error": extraction.error, "fallback": extraction.source})
        parsed = extraction.parsed
        tracer.tool_call(
            "extract_claims",
            {"text": message.text, "history_turns": len(history), "received_at": received_at.isoformat()},
            {"source": extraction.source, "overrides": extraction.overrides, **parsed.model_dump()},
        )

        # 1. Shady: someone speaking for another rider, or instructions to the system.
        if parsed.suspicious:
            reason = f"Suspicious message: {parsed.suspicious_reason or 'flagged by extractor'}"
            tracer.decision("suspicious_message", {"reason": parsed.suspicious_reason}, {"action": "escalate, no payment"})
            if not payments.has_pending_escalation(rider_id, "Suspicious message"):
                payments.escalate(rider_id, message.message_id, reason, details={"text": message.text})
            return self._finish(tracer, replies.SUSPICIOUS)

        claims = parsed.investigable_claims

        # 2. "kab tak aayega?" — report what is in flight.
        if parsed.intent == "follow_up" and not claims:
            summary = payments.rider_summary(rider_id)
            tracer.decision("follow_up", {}, summary)
            return self._finish(tracer, replies.follow_up(summary))

        # 3. "dobara check karo" with nothing new — re-check what we checked before.
        pushback = parsed.intent == "pushback"
        if pushback and not claims:
            claims = self.previous_claims(db, rider_id, message.message_id)
            tracer.decision("reuse_previous_claims", {}, {"claims": [c.model_dump() for c in claims]})

        if parsed.intent == "other" and not claims:
            tracer.decision("no_dispute_found", {}, {"reply": "ask for details"})
            return self._finish(tracer, replies.OTHER)

        # 4. Nothing to look up yet — ask.
        if not claims:
            question = parsed.clarification_question or replies.DEFAULT_CLARIFICATION
            tracer.decision("ask_clarification", {"claims": [c.model_dump() for c in parsed.claims]}, {"question": question})
            return self._finish(tracer, question)

        # 5. Investigate each claim, settle each one as its own dispute.
        outcomes = [
            self._handle_claim(claim, rider_id, as_of, message.message_id, tracer, payments, pushback)
            for claim in claims
        ]

        sentences: list[str] = []
        if len(outcomes) > 1:
            sentences.append("Dono check kiye." if len(outcomes) == 2 else "Sab check kiye.")
        for outcome in outcomes:
            sentences.extend(outcome.sentences)

        return self._finish(tracer, " ".join(s for s in sentences if s))

    # ---------------------------------------------------------------- per claim

    def _handle_claim(self, claim, rider_id, as_of, message_id, tracer, payments, pushback) -> ClaimOutcome:
        out = ClaimOutcome(claim=claim)
        findings: dict[str, Finding] = {}
        checks: list[TripCheck] = []
        audit: DayAudit | None = None

        for trip_id in claim.trip_ids:
            check = self.investigator.check_trip(rider_id, trip_id, as_of)
            tracer.tool_call(
                "check_trip",
                {"rider_id": rider_id, "trip_id": trip_id, "as_of": as_of.isoformat()},
                check.to_dict(),
            )
            checks.append(check)
            if check.status == "ok":
                for f in check.findings:
                    findings[f.unit] = f

        if claim.day is not None:
            audit = self.investigator.audit_day(rider_id, claim.day, as_of)
            tracer.tool_call(
                "audit_day",
                {"rider_id": rider_id, "day": claim.day.isoformat(), "as_of": as_of.isoformat()},
                audit.to_dict(),
            )
            if audit.status == "ok":
                for f in audit.findings:
                    findings.setdefault(f.unit, f)

        # Sentences about what was found.
        for check in checks:
            if check.status == "not_found":
                out.sentences.append(replies.trip_not_found(check.trip_id))
            elif check.status == "not_owned":
                out.sentences.append(replies.trip_not_owned(check.trip_id))
                out.escalations.append(f"Trip {check.trip_id} quoted by rider belongs to another rider")
            elif check.status == "out_of_window":
                out.sentences.append(replies.trip_out_of_window(check))
                out.escalations.append(
                    f"Trip {check.trip_id} is {check.days_old} days old, outside the 7-day window"
                    + (f"; records show a shortfall of ₹{check.owed}" if check.owed else "")
                )
            elif claim.type == "cancellation_dispute":
                out.sentences.append(replies.trip_checked(check, claim.type))
            else:
                out.sentences.append(replies.trip_checked(check, claim.type))

        if audit is not None:
            if audit.status == "no_trips":
                out.sentences.append(replies.day_no_trips(audit.day))
            elif audit.status == "out_of_window":
                # If a trip check already said the day is too old, don't say it twice.
                if not any(c.status == "out_of_window" for c in checks):
                    out.sentences.append(replies.day_out_of_window(audit))
                    out.escalations.append(
                        f"Dispute about {audit.day.isoformat()} is {audit.days_old} days old, "
                        f"outside the 7-day window"
                        + (f"; records show a shortfall of ₹{audit.owed}" if audit.owed else "")
                    )
            else:
                described = {f.unit for c in checks for f in c.findings}
                out.sentences.append(replies.day_checked(audit, claim.type, skip_units=described))

        if claim.type == "cancellation_dispute":
            out.sentences.append(replies.cancellation_dispute(checks[0] if checks else None))
            out.escalations.append(
                f"Rider asks to waive a cancellation penalty ({', '.join(claim.trip_ids) or claim.date or 'no trip given'})"
            )

        owed = [f for f in findings.values() if f.owed > 0]

        if claim.type == "wrong_distance" and checks and not owed:
            out.sentences.append(replies.distance_dispute(checks[0]))
            out.escalations.append(
                f"Rider disputes the recorded distance of {', '.join(claim.trip_ids)}; cannot be verified from exports"
            )

        out.findings = owed
        out.label = self._label(claim, checks, audit)

        if owed:
            outcome = payments.settle(rider_id, owed, as_of, message_id, out.label)
            tracer.decision(
                "settlement",
                {"claim": claim.model_dump(), "owed": sum(f.owed for f in owed), "units": [f.unit for f in owed]},
                outcome.to_dict(),
            )
            out.sentences.append(replies.payment(outcome))
        elif pushback and not out.escalations:
            # The rider insists, the records disagree: a human should look.
            summary = self._facts_summary(checks, audit)
            out.escalations.append(f"Rider disputes our records after being told: {summary}")
            out.sentences.append(replies.pushback_no_change())
        else:
            tracer.decision("settlement", {"claim": claim.model_dump(), "owed": 0}, {"decision": "nothing_owed"})

        for reason in out.escalations:
            if not payments.has_pending_escalation(rider_id, reason[:60]):
                payments.escalate(
                    rider_id, message_id, reason,
                    units=[f.unit for f in findings.values()],
                    details={"claim": claim.model_dump(), "label": out.label},
                )

        return out

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _label(claim: Claim, checks: list[TripCheck], audit: DayAudit | None) -> str:
        what = claim.type.replace("_", " ")
        where = ", ".join(c.trip_id for c in checks) or (audit.day.isoformat() if audit else "")
        return f"{what} {where}".strip()

    @staticmethod
    def _facts_summary(checks: list[TripCheck], audit: DayAudit | None) -> str:
        parts = []
        if audit is not None:
            parts.append(
                f"{audit.day.isoformat()}: {audit.completed_trips} completed trips, "
                f"{audit.rider_cancellations} rider cancellations, incentive paid ₹{audit.incentive_paid}, "
                f"no shortfall"
            )
        for check in checks:
            parts.append(f"{check.trip_id}: {check.status}, shortfall ₹{check.owed}")
        return "; ".join(parts) or "no details"

    @staticmethod
    def _finish(tracer: Tracer, reply: str) -> str:
        tracer.reply(reply)
        return reply
