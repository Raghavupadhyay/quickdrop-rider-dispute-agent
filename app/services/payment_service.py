"""Money decisions and PaySwift payouts.

Rules (from Finance) live here, in code:
  * never pay more than is owed: every rupee belongs to a *unit* (a trip fare,
    a trip penalty, a day's incentive) and a unit is paid at most once, checked
    against what PaySwift has actually paid, not against our own tables;
  * auto-pay up to ₹200 per dispute, once per rider per (IST) day; anything
    bigger or a second one goes to ops for approval;
  * a failed-looking payout is never assumed failed: PaySwift's ledger decides.

Timing: the messaging vendor retries after ~10s, PaySwift's slow path takes
~8s. The service is given a deadline; it waits for PaySwift as long as it can,
then answers "processing" and leaves the payout to the background finalizer.
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.clients.payswift import PaySwiftClient, PaySwiftError
from app.db.models import OpsItem, Payment
from app.domain.policy import (
    AUTO_PAY_LIMIT,
    AUTO_PAYS_PER_RIDER_PER_DAY,
    PAYSWIFT_MAX_AMOUNT,
)
from app.services.investigator import Finding
from app.services.trace_service import Tracer


FAILED_PAYOUT_STATUSES = {"failed", "rejected", "cancelled", "reversed"}

DEFAULT_BUDGET_SECONDS = 8.0
POLL_INTERVAL_SECONDS = 0.4


@dataclass
class PaymentOutcome:
    # paid | processing | approval_pending | approval_exists | already_paid | nothing_owed | payment_failed
    decision: str
    amount: int
    reason: str
    units: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "amount": self.amount,
            "reason": self.reason,
            "units": self.units,
            **self.details,
        }


def units_hash(units: list[str]) -> str:
    return hashlib.sha1(" ".join(sorted(units)).encode()).hexdigest()[:20]


def make_reference(rider_id: str, units: list[str]) -> str:
    return f"QD adj {rider_id} " + " ".join(sorted(units))


def units_in_reference(reference: str | None) -> set[str]:
    return {token for token in (reference or "").split() if ":" in token}


class PaymentService:
    def __init__(
        self,
        db,
        payswift: PaySwiftClient | None = None,
        tracer: Tracer | None = None,
        deadline: float | None = None,
    ):
        self.db = db
        self.payswift = payswift or PaySwiftClient()
        self.tracer = tracer
        # time.monotonic() value by which the rider must have an answer
        self.deadline = deadline if deadline is not None else time.monotonic() + DEFAULT_BUDGET_SECONDS

    # ------------------------------------------------------------------ tracing

    def _tool(self, name, input, output):
        if self.tracer:
            self.tracer.tool_call(name, input, output)

    def _decision(self, name, input, output):
        if self.tracer:
            self.tracer.decision(name, input, output)

    def _error(self, name, input, output):
        if self.tracer:
            self.tracer.error(name, input, output)

    def _remaining(self) -> float:
        return self.deadline - time.monotonic()

    # ------------------------------------------------------------ reconciliation

    def paid_units(self, rider_id: str) -> set[str]:
        """Units PaySwift has already paid this rider for. Raises PaySwiftError."""
        payouts = self.payswift.list_payouts(rider_id)
        paid: set[str] = set()
        for payout in payouts:
            if str(payout.get("status", "")).lower() in FAILED_PAYOUT_STATUSES:
                continue
            paid |= units_in_reference(payout.get("reference"))
        self._tool(
            "payswift_list_payouts",
            {"rider_id": rider_id},
            {"payouts": len(payouts), "paid_units": sorted(paid)},
        )
        return paid

    def _pending_item_for(self, rider_id: str, units: list[str]) -> OpsItem | None:
        wanted = set(units)
        pending = (
            self.db.query(OpsItem)
            .filter(OpsItem.rider_id == rider_id, OpsItem.status == "pending", OpsItem.type == "approval")
            .all()
        )
        for item in pending:
            if units_in_reference(item.reference_key) & wanted:
                return item
        return None

    def _processing_payment_for(self, rider_id: str, units: list[str]) -> Payment | None:
        wanted = set(units)
        processing = (
            self.db.query(Payment)
            .filter(Payment.rider_id == rider_id, Payment.status == "processing")
            .all()
        )
        for payment in processing:
            if units_in_reference(payment.reference_key) & wanted:
                return payment
        return None

    def _auto_pays_today(self, rider_id: str, business_day: date) -> int:
        return (
            self.db.query(Payment)
            .filter(
                Payment.rider_id == rider_id,
                Payment.kind == "auto",
                Payment.status.in_(["completed", "processing"]),
                Payment.business_day == business_day,
            )
            .count()
        )

    # ----------------------------------------------------------------- settling

    def settle(
        self,
        rider_id: str,
        findings: list[Finding],
        business_day: date,
        message_id: str | None,
        label: str,
    ) -> PaymentOutcome:
        """Decide what happens to the shortfalls of one dispute and do it."""
        owed_findings = [f for f in findings if f.owed > 0]
        if not owed_findings:
            return PaymentOutcome("nothing_owed", 0, "no shortfall found")

        units = [f.unit for f in owed_findings]
        gross = sum(f.owed for f in owed_findings)

        try:
            already_paid = self.paid_units(rider_id)
        except PaySwiftError as exc:
            self._error("payswift_list_payouts", {"rider_id": rider_id}, str(exc))
            item = self._create_item(
                rider_id, message_id, "approval", gross,
                f"Could not verify earlier payouts with PaySwift ({exc}); ₹{gross} for {label} needs a human",
                units, {"findings": [f.to_dict() for f in owed_findings], "label": label},
            )
            self._decision("payment_policy", {"owed": gross, "units": units},
                           {"decision": "approval_pending", "reason": "payswift_unavailable", "ops_item_id": item.id})
            return PaymentOutcome("approval_pending", gross, "payswift_unavailable", units, {"ops_item_id": item.id})

        remaining = [f for f in owed_findings if f.unit not in already_paid]
        if not remaining:
            self._decision("payment_policy", {"owed": gross, "units": units},
                           {"decision": "already_paid", "reason": "PaySwift already has payouts for these units"})
            return PaymentOutcome("already_paid", gross, "already paid through PaySwift", units)

        total = sum(f.owed for f in remaining)
        units = [f.unit for f in remaining]

        in_flight = self._processing_payment_for(rider_id, units)
        if in_flight is not None:
            self._decision("payment_policy", {"owed": total, "units": units},
                           {"decision": "processing", "payment_id": in_flight.id, "amount": in_flight.amount})
            return PaymentOutcome("processing", in_flight.amount, "payout already in progress", units,
                                  {"payment_id": in_flight.id})

        existing = self._pending_item_for(rider_id, units)
        if existing:
            self._decision("payment_policy", {"owed": total, "units": units},
                           {"decision": "approval_exists", "ops_item_id": existing.id, "amount": existing.amount})
            return PaymentOutcome("approval_exists", existing.amount, "already waiting for ops approval", units,
                                  {"ops_item_id": existing.id})

        auto_today = self._auto_pays_today(rider_id, business_day)
        policy_input = {
            "owed": total, "units": units, "auto_pay_limit": AUTO_PAY_LIMIT,
            "auto_pays_today": auto_today, "business_day": business_day.isoformat(),
        }

        if total > AUTO_PAY_LIMIT or total > PAYSWIFT_MAX_AMOUNT:
            reason = f"₹{total} is above the ₹{AUTO_PAY_LIMIT} auto-pay limit ({label})"
            item = self._create_item(rider_id, message_id, "approval", total, reason, units,
                                     {"findings": [f.to_dict() for f in remaining], "label": label})
            self._decision("payment_policy", policy_input,
                           {"decision": "approval_pending", "reason": "above_limit", "ops_item_id": item.id})
            return PaymentOutcome("approval_pending", total, "above_limit", units, {"ops_item_id": item.id})

        if auto_today >= AUTO_PAYS_PER_RIDER_PER_DAY:
            reason = f"Rider already received an automatic payout today; ₹{total} for {label} needs approval"
            item = self._create_item(rider_id, message_id, "approval", total, reason, units,
                                     {"findings": [f.to_dict() for f in remaining], "label": label})
            self._decision("payment_policy", policy_input,
                           {"decision": "approval_pending", "reason": "second_auto_pay_today", "ops_item_id": item.id})
            return PaymentOutcome("approval_pending", total, "second_auto_pay_today", units, {"ops_item_id": item.id})

        self._decision("payment_policy", policy_input, {"decision": "auto_pay", "amount": total})
        return self._pay(rider_id, total, units, business_day, message_id, kind="auto", label=label)

    # ------------------------------------------------------------------ payouts

    def _get_or_create_payment(self, rider_id, amount, units, business_day, message_id, kind, ops_item_id):
        reference = make_reference(rider_id, units)
        idempotency_key = f"qd:{rider_id}:{units_hash(units)}"

        payment = self.db.query(Payment).filter(Payment.idempotency_key == idempotency_key).first()
        if payment is None:
            payment = Payment(
                rider_id=rider_id, message_id=message_id, ops_item_id=ops_item_id,
                amount=amount, kind=kind, status="processing", reference=reference,
                reference_key=" ".join(sorted(units)), idempotency_key=idempotency_key,
                business_day=business_day,
            )
            self.db.add(payment)
        elif payment.status != "completed":
            payment.status = "processing"
            payment.kind = kind
            payment.ops_item_id = ops_item_id or payment.ops_item_id
            payment.error = None
        self.db.commit()
        return payment

    def _complete(self, payment: Payment, result: dict | None, note: str | None = None) -> None:
        payment.status = "completed"
        payment.payswift_payout_id = (result or {}).get("payout_id") or payment.payswift_payout_id
        payment.error = note
        self.db.commit()

    def _ledger_has(self, payment: Payment) -> bool:
        units = units_in_reference(payment.reference_key)
        return bool(units) and units <= self.paid_units(payment.rider_id)

    def _pay(self, rider_id, amount, units, business_day, message_id, kind, label, ops_item_id=None,
             escalate_on_failure: bool = True) -> PaymentOutcome:
        payment = self._get_or_create_payment(rider_id, amount, units, business_day, message_id, kind, ops_item_id)
        if payment.status == "completed":
            return PaymentOutcome("already_paid", payment.amount, "payment already completed", units,
                                  {"payment_id": payment.id, "payswift_payout_id": payment.payswift_payout_id})

        request = {"rider_id": rider_id, "amount": amount, "reference": payment.reference,
                   "idempotency_key": payment.idempotency_key}
        details = {"payment_id": payment.id}
        waiting_on_ledger = False

        while True:
            remaining = self._remaining()
            if remaining < POLL_INTERVAL_SECONDS:
                break

            if waiting_on_ledger:
                # PaySwift has the request (slow path / in progress): ask the ledger.
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
                try:
                    if self._ledger_has(payment):
                        self._complete(payment, None, "verified in ledger while waiting")
                        self._decision("payout_verified", request, {"status": "completed_from_ledger"})
                        return PaymentOutcome("paid", amount, "paid (confirmed from PaySwift ledger)", units, details)
                except PaySwiftError as exc:
                    self._error("payswift_list_payouts", {"rider_id": rider_id}, str(exc))
                continue

            try:
                result = self.payswift.create_payout(
                    rider_id, amount, payment.reference, payment.idempotency_key,
                    timeout=max(1.0, min(remaining, 9.0)),
                )
            except PaySwiftError as exc:
                self._error("payswift_create_payout", request,
                            {"error": str(exc), "maybe_processed": exc.maybe_processed, "in_progress": exc.in_progress})
                if exc.definitive:
                    return self._fail(payment, exc, label, units, details, escalate_on_failure)
                # Timed out, in progress, or a 5xx: PaySwift may have it. Watch the ledger.
                waiting_on_ledger = True
                continue

            self._complete(payment, result)
            self._tool("payswift_create_payout", request, result)
            return PaymentOutcome("paid", amount, "paid through PaySwift", units,
                                  {**details, "payswift_payout_id": payment.payswift_payout_id})

        # Out of time: the payout stays `processing`; the finalizer confirms it.
        self._decision("payout_processing", request,
                       {"status": "processing", "note": "PaySwift still working; finalizer will confirm"})
        return PaymentOutcome("processing", amount, "PaySwift still processing", units, details)

    def _fail(self, payment, exc, label, units, details, escalate_on_failure) -> PaymentOutcome:
        payment.status = "failed"
        payment.error = str(exc)
        self.db.commit()
        if escalate_on_failure:
            item = self._create_item(
                payment.rider_id, payment.message_id, "escalation", payment.amount,
                f"PaySwift payout of ₹{payment.amount} failed ({label}): {exc}. Needs a retry by ops.",
                units, {"payment_id": payment.id, "label": label},
            )
            details = {**details, "ops_item_id": item.id}
        return PaymentOutcome("payment_failed", payment.amount, str(exc), units, details)

    # ------------------------------------------------ finalizer entry points

    def verify_processing(self, payment: Payment) -> bool:
        """True when the ledger shows the payout; marks it completed."""
        if self._ledger_has(payment):
            self._complete(payment, None, "verified in ledger by finalizer")
            self._decision("payout_verified", {"payment_id": payment.id}, {"status": "completed_by_finalizer"})
            return True
        return False

    def resend_processing(self, payment: Payment) -> str:
        """Re-send with the same Idempotency-Key. Returns completed | processing | failed."""
        request = {"rider_id": payment.rider_id, "amount": payment.amount,
                   "reference": payment.reference, "idempotency_key": payment.idempotency_key}
        try:
            result = self.payswift.create_payout(
                payment.rider_id, payment.amount, payment.reference, payment.idempotency_key, timeout=10.0,
            )
        except PaySwiftError as exc:
            self._error("payswift_create_payout", request, {"error": str(exc), "resend": True})
            if exc.definitive:
                self.fail_processing(payment, str(exc))
                return "failed"
            return "processing"
        self._complete(payment, result, "completed on resend by finalizer")
        self._tool("payswift_create_payout", request, {**result, "resend": True})
        return "completed"

    def fail_processing(self, payment: Payment, reason: str) -> None:
        payment.status = "failed"
        payment.error = reason
        self.db.commit()
        self._create_item(
            payment.rider_id, payment.message_id, "escalation", payment.amount,
            f"PaySwift payout of ₹{payment.amount} could not be confirmed: {reason}. Needs a retry by ops.",
            sorted(units_in_reference(payment.reference_key)), {"payment_id": payment.id},
        )

    def pay_approved(self, item: OpsItem, business_day: date | None = None) -> PaymentOutcome:
        """Ops approved an item: reconcile once more, then pay the remainder."""
        units = sorted(units_in_reference(item.reference_key))
        findings = [Finding(**f) for f in (item.details or {}).get("findings", [])]
        owed_by_unit = {f.unit: f.owed for f in findings}

        already_paid = self.paid_units(item.rider_id)  # raises PaySwiftError -> caller handles
        remaining_units = [u for u in units if u not in already_paid]
        amount = sum(owed_by_unit.get(u, 0) for u in remaining_units)

        if not remaining_units or amount <= 0:
            self._decision("ops_approval", {"ops_item_id": item.id}, {"decision": "already_paid"})
            return PaymentOutcome("already_paid", item.amount or 0, "already paid through PaySwift", units)

        return self._pay(
            item.rider_id, amount, remaining_units,
            business_day or datetime.now(timezone.utc).date(),
            item.message_id, kind="approved", label=(item.details or {}).get("label", "approved by ops"),
            ops_item_id=item.id, escalate_on_failure=False,
        )

    # ---------------------------------------------------------------- ops items

    def _create_item(self, rider_id, message_id, type, amount, reason, units, details) -> OpsItem:
        item = OpsItem(
            rider_id=rider_id, message_id=message_id, type=type, amount=amount,
            reason=reason, status="pending", reference_key=" ".join(sorted(units)) if units else None,
            details=details,
        )
        self.db.add(item)
        self.db.commit()
        if self.tracer:
            self.tracer.decision(
                "escalate_to_ops" if type == "escalation" else "request_approval",
                {"amount": amount, "units": units},
                {"ops_item_id": item.id, "type": type, "reason": reason},
            )
        return item

    def escalate(self, rider_id, message_id, reason, units=None, details=None) -> OpsItem:
        return self._create_item(rider_id, message_id, "escalation", None, reason, units or [], details or {})

    def has_pending_escalation(self, rider_id: str, reason_prefix: str) -> bool:
        return (
            self.db.query(OpsItem)
            .filter(OpsItem.rider_id == rider_id, OpsItem.type == "escalation",
                    OpsItem.status == "pending", OpsItem.reason.like(f"{reason_prefix}%"))
            .first()
            is not None
        )

    # ----------------------------------------------------------------- summary

    def rider_summary(self, rider_id: str) -> dict:
        """What a rider has in flight, for answering "kab tak aayega?"."""
        payments = (
            self.db.query(Payment)
            .filter(Payment.rider_id == rider_id)
            .order_by(Payment.created_at.desc())
            .limit(5)
            .all()
        )
        items = (
            self.db.query(OpsItem)
            .filter(OpsItem.rider_id == rider_id, OpsItem.status == "pending")
            .order_by(OpsItem.created_at.asc())
            .all()
        )
        return {
            "last_completed_payment": next(
                ({"amount": p.amount, "at": p.created_at.isoformat()} for p in payments if p.status == "completed"),
                None,
            ),
            "processing_payments": [p.amount for p in payments if p.status == "processing"],
            "failed_payments": [p.amount for p in payments if p.status == "failed"],
            "pending_approvals": [i.amount for i in items if i.type == "approval"],
            "pending_escalations": len([i for i in items if i.type == "escalation"]),
        }
