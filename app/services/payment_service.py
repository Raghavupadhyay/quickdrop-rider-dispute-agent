from datetime import (
    datetime,
    timezone,
    timedelta,
)

from app.clients.payswift import (
    PaySwiftClient,
    PaySwiftError,
)

from app.db.models import (
    Dispute,
    PendingApproval,
    PaymentAction,
)

from app.services.payment_policy import (
    decide_payment_action,
)

from app.services.reconciliation_service import (
    ReconciliationService,
)


IST = timezone(
    timedelta(
        hours=5,
        minutes=30,
    )
)


class PaymentService:
    def __init__(self, db):
        self.db = db

        self.payswift = PaySwiftClient()

        self.reconciliation = (
            ReconciliationService(
                payswift_client=self.payswift
            )
        )

    def _already_auto_paid_today(
        self,
        rider_id: str,
    ) -> bool:
        now_ist = datetime.now(IST)

        start_ist = now_ist.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        start_utc = (
            start_ist.astimezone(
                timezone.utc
            )
        )

        existing = (
            self.db.query(PaymentAction)
            .filter(
                PaymentAction.rider_id
                == rider_id,

                PaymentAction.action
                == "AUTO_PAY",

                PaymentAction.status
                == "COMPLETED",

                PaymentAction.created_at
                >= start_utc,
            )
            .first()
        )

        return existing is not None

    def handle_resolution(
        self,
        rider_id: str,
        dispute_type: str,
        resolution: dict,
    ):
        gross_owed = resolution.get(
            "owed",
            0,
        )

        reference_key = resolution.get(
            "reference_key"
        )

        if not reference_key:
            raise ValueError(
                "Resolution is missing reference_key"
            )

        reconciliation = (
            self.reconciliation
            .calculate_remaining_owed(
                rider_id=rider_id,
                reference_key=reference_key,
                gross_owed=gross_owed,
            )
        )

        remaining_owed = (
            reconciliation[
                "remaining_owed"
            ]
        )

        dispute = Dispute(
            rider_id=rider_id,
            dispute_type=dispute_type,
            status="RESOLVED",
            owed_amount=remaining_owed,
            reason=resolution.get(
                "reason"
            ),
            reference_key=reference_key,
        )

        self.db.add(dispute)
        self.db.flush()

        if remaining_owed <= 0:
            self.db.commit()

            return {
                "decision": "NO_ACTION",
                "amount": 0,
                "dispute_id": dispute.id,
                "reason": (
                    "Already reconciled against PaySwift"
                ),
                "reconciliation": reconciliation,
            }

        already_paid_today = (
            self._already_auto_paid_today(
                rider_id=rider_id,
            )
        )

        decision = decide_payment_action(
            owed_amount=remaining_owed,
            already_auto_paid_today=already_paid_today,
        )

        if decision.action == "NO_ACTION":
            self.db.commit()

            return {
                "decision": "NO_ACTION",
                "amount": 0,
                "dispute_id": dispute.id,
                "reason": decision.reason,
                "reconciliation": reconciliation,
            }

        if decision.action == "REQUIRE_APPROVAL":
            approval_key = (
                f"approval:{reference_key}"
            )

            existing_action = (
                self.db.query(
                    PaymentAction
                )
                .filter(
                    PaymentAction.idempotency_key
                    == approval_key
                )
                .first()
            )

            if existing_action:
                self.db.commit()

                return {
                    "decision": "REQUIRE_APPROVAL",
                    "amount": remaining_owed,
                    "dispute_id": dispute.id,
                    "reason": (
                        "Approval already exists"
                    ),
                    "reconciliation": reconciliation,
                }

            approval = PendingApproval(
                rider_id=rider_id,
                dispute_id=dispute.id,
                amount=remaining_owed,
                status="PENDING",
                reason=decision.reason,
            )

            payment_action = PaymentAction(
                rider_id=rider_id,
                dispute_id=dispute.id,
                amount=remaining_owed,
                action="REQUIRE_APPROVAL",
                status="PENDING",
                idempotency_key=approval_key,
            )

            self.db.add(approval)
            self.db.add(payment_action)

            self.db.commit()

            return {
                "decision": "REQUIRE_APPROVAL",
                "amount": remaining_owed,
                "dispute_id": dispute.id,
                "reason": decision.reason,
                "reconciliation": reconciliation,
            }

        idempotency_key = (
            f"autopay:{reference_key}"
        )

        existing_action = (
            self.db.query(
                PaymentAction
            )
            .filter(
                PaymentAction.idempotency_key
                == idempotency_key
            )
            .first()
        )

        if existing_action:
            self.db.commit()

            return {
                "decision": "AUTO_PAY",
                "amount": (
                    existing_action.amount
                ),
                "dispute_id": dispute.id,
                "status": (
                    existing_action.status
                ),
                "reason": (
                    "Existing payment action reused"
                ),
                "reconciliation": reconciliation,
            }

        payment_action = PaymentAction(
            rider_id=rider_id,
            dispute_id=dispute.id,
            amount=remaining_owed,
            action="AUTO_PAY",
            status="PROCESSING",
            idempotency_key=idempotency_key,
        )

        self.db.add(payment_action)
        self.db.flush()

        payswift_reference = (
            f"adjustment:{reference_key}"
        )

        try:
            payswift_result = (
                self.payswift.create_payout(
                    rider_id=rider_id,
                    amount=remaining_owed,
                    reference=payswift_reference,
                    idempotency_key=idempotency_key,
                )
            )

            payment_action.status = (
                "COMPLETED"
            )

            self.db.commit()

            return {
                "decision": "AUTO_PAY",
                "amount": remaining_owed,
                "dispute_id": dispute.id,
                "status": "COMPLETED",
                "payswift": payswift_result,
                "reconciliation": reconciliation,
            }

        except PaySwiftError as exc:
            payment_action.status = (
                "FAILED"
            )

            self.db.commit()

            return {
                "decision": "AUTO_PAY",
                "amount": remaining_owed,
                "dispute_id": dispute.id,
                "status": "FAILED",
                "reason": str(exc),
                "reconciliation": reconciliation,
            }