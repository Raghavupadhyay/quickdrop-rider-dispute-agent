"""Background finalizer for payouts PaySwift was still processing when the
rider had to be answered.

PaySwift's slow path takes ~8s, the messaging vendor gives us ~10s, so a
payout can be in flight when the reply goes out. This loop asks the ledger
until it appears, re-sends with the same Idempotency-Key if PaySwift seems to
have lost it, and escalates to ops if it still is not there after a while.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.clients.payswift import PaySwiftClient, PaySwiftError
from app.db.models import Payment
from app.services.payment_service import PaymentService
from app.services.trace_service import Tracer


logger = logging.getLogger(__name__)

VERIFY_AFTER = timedelta(seconds=2)      # don't race a request that is still being awaited
RESEND_AFTER = timedelta(seconds=45)     # PaySwift normally finishes within ~8s
GIVE_UP_AFTER = timedelta(minutes=15)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def finalize_once(db, payswift: PaySwiftClient, now: datetime | None = None) -> dict:
    """One pass over processing payments. Returns counts for logging/tests."""
    now = now or datetime.now(timezone.utc)
    counts = {"checked": 0, "completed": 0, "resent": 0, "failed": 0}

    payments = db.query(Payment).filter(Payment.status == "processing").all()
    for payment in payments:
        age = now - _aware(payment.created_at)
        if age < VERIFY_AFTER:
            continue
        counts["checked"] += 1

        tracer = Tracer(db, payment.rider_id, payment.message_id)
        service = PaymentService(db, payswift, tracer)
        try:
            if service.verify_processing(payment):
                counts["completed"] += 1
                continue
            if age > GIVE_UP_AFTER:
                service.fail_processing(payment, "PaySwift never confirmed the payout")
                counts["failed"] += 1
            elif age > RESEND_AFTER:
                outcome = service.resend_processing(payment)
                counts["resent"] += 1
                if outcome == "completed":
                    counts["completed"] += 1
                elif outcome == "failed":
                    counts["failed"] += 1
        except PaySwiftError as exc:
            logger.warning("finalizer: PaySwift unavailable for payment %s: %s", payment.id, exc)
    return counts


class PayoutFinalizer:
    def __init__(self, session_factory, payswift_factory=PaySwiftClient, interval: float = 2.0):
        self.session_factory = session_factory
        self.payswift_factory = payswift_factory
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self):
        while not self._stop.is_set():
            db = self.session_factory()
            try:
                finalize_once(db, self.payswift_factory())
            except Exception:  # keep the loop alive whatever happens
                logger.exception("finalizer pass failed")
                db.rollback()
            finally:
                db.close()
            self._stop.wait(self.interval)

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="payout-finalizer", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
