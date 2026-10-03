"""Finance's rules, exercised against a fake PaySwift."""

import threading
import time
from datetime import date, datetime, timedelta, timezone

from app.db.models import OpsItem, Payment
from app.services.finalizer import finalize_once
from app.services.investigator import Finding
from app.services.payment_service import PaymentService, make_reference


DAY = date(2026, 9, 22)


def finding(unit, owed, kind="trip_fare"):
    return Finding(unit=unit, kind=kind, expected=owed, paid=0, owed=owed, facts={})


def test_small_amount_is_paid_automatically(db, payswift):
    service = PaymentService(db, payswift)
    outcome = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    assert outcome.decision == "paid" and outcome.amount == 25
    assert payswift.total("R003") == 25
    assert payswift.payouts[0]["reference"] == make_reference("R003", ["trip:T926334"])


def test_above_limit_waits_for_approval(db, payswift):
    service = PaymentService(db, payswift)
    outcome = service.settle("R016", [finding(f"trip:T{i}", 85) for i in range(5)], DAY, "m1", "unpaid trips")
    assert outcome.decision == "approval_pending" and outcome.amount == 425
    assert payswift.total("R016") == 0
    item = db.query(OpsItem).one()
    assert item.type == "approval" and item.amount == 425 and item.status == "pending"


def test_second_auto_pay_same_day_needs_approval(db, payswift):
    service = PaymentService(db, payswift)
    first = service.settle("R027", [finding("trip:T604546", 15)], DAY, "m1", "surge")
    second = service.settle("R027", [finding("penalty:T936237", 10, "penalty")], DAY, "m1", "penalty")
    assert first.decision == "paid"
    assert second.decision == "approval_pending" and second.reason == "second_auto_pay_today"
    assert payswift.total("R027") == 15


def test_next_day_auto_pay_is_allowed_again(db, payswift):
    service = PaymentService(db, payswift)
    service.settle("R027", [finding("trip:A", 15)], DAY, "m1", "a")
    outcome = service.settle("R027", [finding("trip:B", 10)], date(2026, 9, 23), "m2", "b")
    assert outcome.decision == "paid"


def test_never_pays_a_unit_twice(db, payswift):
    service = PaymentService(db, payswift)
    service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    again = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m2", "surge again")
    assert again.decision == "already_paid"
    assert payswift.total("R003") == 25


def test_reconciles_against_payswift_not_own_tables(db, payswift):
    # Someone (an ops approval, an older system) already paid this unit through PaySwift.
    payswift._record("R033", 176, make_reference("R033", ["trip:T206956", "trip:T795007"]), "external")
    service = PaymentService(db, payswift)
    outcome = service.settle("R033", [finding("trip:T795007", 91)], DAY, "m1", "trip")
    assert outcome.decision == "already_paid"
    assert db.query(Payment).count() == 0


def test_gateway_timeout_that_actually_paid_is_verified_not_repeated(db, payswift):
    payswift.script = ["504_processed"]
    service = PaymentService(db, payswift)
    outcome = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    assert outcome.decision == "paid"
    assert payswift.total("R003") == 25
    assert db.query(Payment).one().status == "completed"


def test_real_failure_is_escalated(db, payswift):
    payswift.script = ["400"]
    service = PaymentService(db, payswift)
    outcome = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    assert outcome.decision == "payment_failed"
    assert payswift.total("R003") == 0
    assert db.query(Payment).one().status == "failed"
    assert db.query(OpsItem).filter_by(type="escalation").count() == 1


def test_payswift_down_means_no_auto_money(db, payswift):
    payswift.list_down = True
    service = PaymentService(db, payswift)
    outcome = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    assert outcome.decision == "approval_pending"
    assert db.query(OpsItem).filter_by(type="approval").one().amount == 25
    assert payswift.calls == 0


def test_slow_payout_that_lands_in_time_is_confirmed_from_the_ledger(db, payswift):
    # Our request times out, the payout lands a moment later: the service keeps
    # watching the ledger until its deadline and confirms it.
    payswift.script = ["timeout_pending"]
    service = PaymentService(db, payswift, deadline=time.monotonic() + 2.0)
    threading.Timer(0.6, payswift.release).start()
    outcome = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    assert outcome.decision == "paid"
    assert payswift.total("R003") == 25
    assert payswift.calls == 1  # never re-sent while PaySwift was still working


def test_slow_payout_past_the_deadline_is_processing_then_finalized(db, payswift):
    payswift.script = ["timeout_pending"]
    service = PaymentService(db, payswift, deadline=time.monotonic() + 0.8)
    outcome = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    assert outcome.decision == "processing" and outcome.amount == 25
    payment = db.query(Payment).one()
    assert payment.status == "processing"
    assert db.query(OpsItem).count() == 0  # not an error, nothing for ops yet

    # The rider asks again meanwhile: no second payout, no approval.
    again = service.settle("R003", [finding("trip:T926334", 25)], DAY, "m2", "surge")
    assert again.decision == "processing"

    # PaySwift finishes; the finalizer notices.
    payswift.release()
    later = datetime.now(timezone.utc) + timedelta(seconds=5)
    counts = finalize_once(db, payswift, now=later)
    assert counts["completed"] == 1
    db.refresh(payment)
    assert payment.status == "completed"
    assert payswift.total("R003") == 25


def test_finalizer_resends_a_lost_payout_with_the_same_key(db, payswift):
    payswift.script = ["timeout_pending"]
    service = PaymentService(db, payswift, deadline=time.monotonic() + 0.5)
    service.settle("R003", [finding("trip:T926334", 25)], DAY, "m1", "surge")
    payswift.pending = []  # PaySwift lost it
    much_later = datetime.now(timezone.utc) + timedelta(minutes=2)
    counts = finalize_once(db, payswift, now=much_later)
    assert counts["resent"] == 1 and counts["completed"] == 1
    assert payswift.total("R003") == 25
    assert db.query(Payment).one().idempotency_key == payswift.payouts[0]["idempotency_key"]


def test_in_progress_counts_against_the_daily_auto_pay(db, payswift):
    payswift.script = ["timeout_pending"]
    service = PaymentService(db, payswift, deadline=time.monotonic() + 0.5)
    service.settle("R027", [finding("trip:T604546", 15)], DAY, "m1", "surge")
    second = PaymentService(db, payswift).settle("R027", [finding("penalty:T936237", 10, "penalty")], DAY, "m1", "penalty")
    assert second.decision == "approval_pending" and second.reason == "second_auto_pay_today"


def test_approved_item_is_reconciled_then_paid(db, payswift):
    service = PaymentService(db, payswift)
    pending = service.settle("R016", [finding("trip:A", 300), finding("trip:B", 125)], DAY, "m1", "unpaid")
    item = db.get(OpsItem, pending.details["ops_item_id"])
    # Between the request and the approval, trip:A got paid some other way.
    payswift._record("R016", 300, make_reference("R016", ["trip:A"]), "external")
    outcome = service.pay_approved(item, DAY)
    assert outcome.decision == "paid" and outcome.amount == 125
    assert payswift.total("R016") == 425
