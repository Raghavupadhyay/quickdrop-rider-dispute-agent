import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_agent
from app.db.database import get_db
from app.db.models import Message, OpsItem
from app.services import replies
from app.services.agent import DisputeAgent
from app.services.trace_service import Tracer


logger = logging.getLogger(__name__)

router = APIRouter()

# The vendor resends a message if it has no 2xx after ~10s. Leave a margin.
REPLY_BUDGET_SECONDS = 9.0
# How long a redelivery waits for the original request to finish.
DUPLICATE_WAIT_SECONDS = 6.0


class MessageRequest(BaseModel):
    message_id: str
    rider_id: str
    text: str
    received_at: datetime


class MessageResponse(BaseModel):
    reply: str
    duplicate: bool = False


def _wait_for_reply(db: Session, message_id: str) -> Message | None:
    """A redelivery while the original is still being processed: wait for it
    so both deliveries carry the same real reply instead of a holding text."""
    waited = 0.0
    while waited < DUPLICATE_WAIT_SECONDS:
        time.sleep(0.3)
        waited += 0.3
        db.expire_all()
        message = db.query(Message).filter(Message.message_id == message_id).first()
        if message is not None and message.status != "processing":
            return message
    return None


@router.post("/messages", response_model=MessageResponse)
def receive_message(
    payload: MessageRequest,
    db: Session = Depends(get_db),
    agent: DisputeAgent = Depends(get_agent),
):
    started = time.monotonic()

    # The message row is written *before* any work, so a redelivery (same
    # wamid) is never processed, or paid, twice.
    existing = db.query(Message).filter(Message.message_id == payload.message_id).first()

    if existing is not None:
        if existing.status == "processing":
            existing = _wait_for_reply(db, payload.message_id) or existing
        if existing.status in ("done", "processing"):
            reply = existing.reply if existing.status == "done" and existing.reply else replies.HOLDING
            Tracer(db, payload.rider_id, payload.message_id).message_in(
                "duplicate_delivery",
                {"message_id": payload.message_id, "text": payload.text, "received_at": payload.received_at.isoformat(),
                 "note": "vendor redelivered this message; answered with the stored reply, not processed again"},
            )
            return MessageResponse(reply=reply, duplicate=True)
        message = existing  # failed earlier: try again
        message.status = "processing"
        db.commit()
    else:
        message = Message(
            message_id=payload.message_id,
            rider_id=payload.rider_id,
            text=payload.text,
            received_at=payload.received_at,
            status="processing",
        )
        db.add(message)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            finished = _wait_for_reply(db, payload.message_id)
            if finished is not None and finished.reply:
                return MessageResponse(reply=finished.reply, duplicate=True)
            return MessageResponse(reply=replies.HOLDING, duplicate=True)

    try:
        reply = agent.handle(db, message, deadline=started + REPLY_BUDGET_SECONDS)
        message.reply = reply
        message.status = "done"
        db.commit()
        return MessageResponse(reply=reply)
    except Exception as exc:  # never leave the rider without an answer
        logger.exception("agent failed for %s", payload.message_id)
        db.rollback()
        error = f"{type(exc).__name__}: {exc}"

        tracer = Tracer(db, payload.rider_id, payload.message_id)
        tracer.error("agent_failure", {"text": payload.text}, {"error": error})

        db.add(
            OpsItem(
                rider_id=payload.rider_id,
                message_id=payload.message_id,
                type="escalation",
                amount=None,
                reason=f"Agent error while handling a message: {error}",
                status="pending",
                details={"text": payload.text},
            )
        )
        message = db.query(Message).filter(Message.message_id == payload.message_id).first()
        if message is not None:
            message.reply = replies.SAFE_FALLBACK
            message.status = "failed"
        db.commit()
        tracer.reply(replies.SAFE_FALLBACK)
        return MessageResponse(reply=replies.SAFE_FALLBACK)
