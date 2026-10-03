from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_payswift
from app.clients.payswift import PaySwiftClient, PaySwiftError
from app.db.database import get_db
from app.db.models import Message, OpsItem
from app.services.payment_service import PaymentService
from app.services.trace_service import Tracer


router = APIRouter()

OPS_PAGE = Path(__file__).resolve().parent.parent / "static" / "ops.html"


def item_to_dict(item: OpsItem) -> dict:
    return {
        "id": item.id,
        "rider_id": item.rider_id,
        "type": item.type,
        "amount": item.amount,
        "reason": item.reason,
        "created_at": item.created_at.isoformat(),
        "status": item.status,
        "message_id": item.message_id,
        "reference_key": item.reference_key,
        "details": item.details,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
    }


@router.get("/ops/pending")
def get_pending(db: Session = Depends(get_db)):
    """What is waiting for ops: approvals (money to release) and escalations."""
    items = (
        db.query(OpsItem)
        .filter(OpsItem.status == "pending")
        .order_by(OpsItem.created_at.asc(), OpsItem.id.asc())
        .all()
    )
    return [item_to_dict(item) for item in items]


@router.get("/ops/items")
def get_items(status: str = "all", db: Session = Depends(get_db)):
    query = db.query(OpsItem)
    if status != "all":
        query = query.filter(OpsItem.status == status)
    items = query.order_by(OpsItem.created_at.desc(), OpsItem.id.desc()).limit(500).all()
    return [item_to_dict(item) for item in items]


@router.get("/ops/conversations")
def get_conversations(db: Session = Depends(get_db)):
    rows = (
        db.query(Message)
        .order_by(Message.received_at.asc(), Message.id.asc())
        .all()
    )
    by_rider: dict[str, dict] = {}
    for row in rows:
        conv = by_rider.setdefault(row.rider_id, {"rider_id": row.rider_id, "messages": [], "last_at": None})
        conv["messages"].append(
            {
                "message_id": row.message_id,
                "text": row.text,
                "reply": row.reply,
                "status": row.status,
                "received_at": row.received_at.isoformat(),
            }
        )
        conv["last_at"] = row.received_at.isoformat()
    return sorted(by_rider.values(), key=lambda c: c["last_at"] or "", reverse=True)


def _load_pending(item_id: int, db: Session) -> OpsItem:
    item = db.query(OpsItem).filter(OpsItem.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.status != "pending":
        raise HTTPException(status_code=409, detail=f"Item is already {item.status}")
    return item


@router.post("/ops/pending/{item_id}/approve")
def approve(
    item_id: int,
    db: Session = Depends(get_db),
    payswift: PaySwiftClient = Depends(get_payswift),
):
    """Approve: for an approval, pay the rider (reconciled against PaySwift
    once more); for an escalation, mark it handled."""
    item = _load_pending(item_id, db)
    tracer = Tracer(db, item.rider_id, item.message_id)

    if item.type == "escalation":
        item.status = "closed"
        item.resolved_at = datetime.now(timezone.utc)
        db.commit()
        tracer.decision("ops_closed_escalation", {"ops_item_id": item.id}, {"reason": item.reason})
        return {"status": item.status, "item": item_to_dict(item)}

    payments = PaymentService(db, payswift, tracer)
    try:
        outcome = payments.pay_approved(item)
    except PaySwiftError as exc:
        tracer.error("ops_approval_payout", {"ops_item_id": item.id}, {"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"PaySwift unavailable, item left pending: {exc}")

    if outcome.decision in ("paid", "already_paid"):
        item.status = "approved"
        item.resolved_at = datetime.now(timezone.utc)
        db.commit()
        tracer.decision("ops_approved", {"ops_item_id": item.id, "amount": item.amount}, outcome.to_dict())
        return {"status": item.status, "outcome": outcome.to_dict(), "item": item_to_dict(item)}

    item.details = {**(item.details or {}), "last_error": outcome.reason}
    db.commit()
    tracer.error("ops_approval_payout", {"ops_item_id": item.id}, outcome.to_dict())
    raise HTTPException(status_code=502, detail=f"Payout failed, item left pending: {outcome.reason}")


@router.post("/ops/pending/{item_id}/reject")
def reject(item_id: int, db: Session = Depends(get_db)):
    item = _load_pending(item_id, db)
    item.status = "rejected"
    item.resolved_at = datetime.now(timezone.utc)
    db.commit()
    Tracer(db, item.rider_id, item.message_id).decision(
        "ops_rejected", {"ops_item_id": item.id, "amount": item.amount}, {"reason": item.reason}
    )
    return {"status": item.status, "item": item_to_dict(item)}


@router.get("/ops", response_class=HTMLResponse, include_in_schema=False)
def ops_page():
    return OPS_PAGE.read_text(encoding="utf-8")
