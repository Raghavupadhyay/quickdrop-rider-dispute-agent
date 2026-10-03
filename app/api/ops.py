from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from sqlalchemy.orm import Session

from app.clients.payswift import (
    PaySwiftClient,
    PaySwiftError,
)

from app.db.database import (
    SessionLocal,
)

from app.db.models import (
    Dispute,
    PendingApproval,
    PaymentAction,
    TraceEvent,
)


router = APIRouter()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.get("/ops/pending")
def get_pending_ops(
    db: Session = Depends(get_db),
):
    items = (
        db.query(PendingApproval)
        .filter(
            PendingApproval.status
            == "PENDING"
        )
        .order_by(
            PendingApproval.created_at.asc()
        )
        .all()
    )

    return [
        {
            "id": item.id,
            "rider_id": item.rider_id,
            "dispute_id": item.dispute_id,
            "amount": item.amount,
            "status": item.status,
            "reason": item.reason,
            "created_at": item.created_at,
        }
        for item in items
    ]


@router.post(
    "/ops/pending/{approval_id}/approve"
)
def approve_pending(
    approval_id: int,
    db: Session = Depends(get_db),
):
    approval = (
        db.query(PendingApproval)
        .filter(
            PendingApproval.id
            == approval_id
        )
        .first()
    )

    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    if approval.status != "PENDING":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Approval is already "
                f"{approval.status}"
            ),
        )

    dispute = (
        db.query(Dispute)
        .filter(
            Dispute.id
            == approval.dispute_id
        )
        .first()
    )

    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail="Dispute not found",
        )

    if not dispute.reference_key:
        raise HTTPException(
            status_code=500,
            detail=(
                "Dispute has no reference key"
            ),
        )

    idempotency_key = (
        f"approved:"
        f"{dispute.reference_key}"
    )

    existing_payment = (
        db.query(PaymentAction)
        .filter(
            PaymentAction.idempotency_key
            == idempotency_key
        )
        .first()
    )

    if existing_payment:
        return {
            "status": (
                existing_payment.status
            ),
            "amount": (
                existing_payment.amount
            ),
            "message": (
                "Existing approved payment reused"
            ),
        }

    payment_action = PaymentAction(
        rider_id=approval.rider_id,
        dispute_id=dispute.id,
        amount=approval.amount,
        action="AUTO_PAY",
        status="PROCESSING",
        idempotency_key=idempotency_key,
    )

    db.add(payment_action)
    db.flush()

    payswift = PaySwiftClient()

    try:
        result = payswift.create_payout(
            rider_id=approval.rider_id,
            amount=approval.amount,
            reference=(
                f"adjustment:"
                f"{dispute.reference_key}"
            ),
            idempotency_key=(
                idempotency_key
            ),
        )

        payment_action.status = (
            "COMPLETED"
        )

        approval.status = "APPROVED"

        dispute.status = "PAID"

        trace = TraceEvent(
            rider_id=approval.rider_id,
            event_type=(
                "OPS_APPROVED_PAYMENT"
            ),
            details=(
                f"Approval {approval.id} "
                f"approved for ₹"
                f"{approval.amount}"
            ),
        )

        db.add(trace)
        db.commit()

        return {
            "status": "COMPLETED",
            "amount": approval.amount,
            "payswift": result,
        }

    except PaySwiftError as exc:
        payment_action.status = "FAILED"

        trace = TraceEvent(
            rider_id=approval.rider_id,
            event_type=(
                "OPS_PAYMENT_FAILED"
            ),
            details=str(exc),
        )

        db.add(trace)
        db.commit()

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


@router.post(
    "/ops/pending/{approval_id}/reject"
)
def reject_pending(
    approval_id: int,
    db: Session = Depends(get_db),
):
    approval = (
        db.query(PendingApproval)
        .filter(
            PendingApproval.id
            == approval_id
        )
        .first()
    )

    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="Approval not found",
        )

    if approval.status != "PENDING":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Approval is already "
                f"{approval.status}"
            ),
        )

    approval.status = "REJECTED"

    dispute = (
        db.query(Dispute)
        .filter(
            Dispute.id
            == approval.dispute_id
        )
        .first()
    )

    if dispute:
        dispute.status = "REJECTED"

    original_action = (
        db.query(PaymentAction)
        .filter(
            PaymentAction.dispute_id
            == approval.dispute_id,
            PaymentAction.action
            == "REQUIRE_APPROVAL",
        )
        .first()
    )

    if original_action:
        original_action.status = (
            "REJECTED"
        )

    trace = TraceEvent(
        rider_id=approval.rider_id,
        event_type="OPS_REJECTED",
        details=(
            f"Approval {approval.id} "
            f"for ₹{approval.amount} rejected"
        ),
    )

    db.add(trace)
    db.commit()

    return {
        "status": "REJECTED",
        "amount": approval.amount,
    }