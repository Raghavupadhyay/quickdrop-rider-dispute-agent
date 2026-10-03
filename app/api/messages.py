from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.extractor import ClaimExtractor

from app.db.database import SessionLocal
from app.db.models import Message

from app.repositories.trips import TripsRepository
from app.repositories.payouts import PayoutRepository

from app.services.dispute_resolver import DisputeResolver
from app.services.message_processor import MessageProcessor
from app.services.payment_service import PaymentService
from app.services.trace_service import TraceService


router = APIRouter()


class MessageRequest(BaseModel):
    message_id: str
    rider_id: str
    text: str
    received_at: datetime


class MessageResponse(BaseModel):
    reply: str


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


trips_repo = TripsRepository(
    "data/trips.csv"
)

payouts_repo = PayoutRepository(
    "data/payout_lines.csv"
)

dispute_resolver = DisputeResolver(
    trips_repo=trips_repo,
    payouts_repo=payouts_repo,
)

llm_client = LLMClient()

claim_extractor = ClaimExtractor(
    llm_client=llm_client,
)


@router.post(
    "/messages",
    response_model=MessageResponse,
)
def receive_message(
    payload: MessageRequest,
    db: Session = Depends(get_db),
):
    existing = (
        db.query(Message)
        .filter(
            Message.message_id
            == payload.message_id
        )
        .first()
    )

    if existing:
        return {
            "reply": existing.reply
            or "Message received."
        }

    trace = TraceService(db)

    trace.log(
        rider_id=payload.rider_id,
        event_type="MESSAGE_RECEIVED",
        details=payload.text,
    )

    history_rows = (
        db.query(Message)
        .filter(
            Message.rider_id
            == payload.rider_id
        )
        .order_by(
            Message.received_at.asc()
        )
        .all()
    )

    conversation_history = []

    for row in history_rows[-10:]:
        conversation_history.append(
            {
                "from": "rider",
                "text": row.text,
            }
        )

        if row.reply:
            conversation_history.append(
                {
                    "from": "agent",
                    "text": row.reply,
                }
            )

    payment_service = PaymentService(
        db
    )

    message_processor = MessageProcessor(
        dispute_resolver=dispute_resolver,
        payment_service=payment_service,
        claim_extractor=claim_extractor,
    )

    result = message_processor.process(
        rider_id=payload.rider_id,
        text=payload.text,
        received_at=payload.received_at,
        conversation_history=conversation_history,
    )

    reply = result["reply"]

    trace.log(
        rider_id=payload.rider_id,
        event_type="CLAIMS_EXTRACTED",
        details=str(
            result.get("parsed")
        ),
    )

    trace.log(
        rider_id=payload.rider_id,
        event_type="DISPUTE_RESOLVED",
        details=str(
            result.get("resolutions")
        ),
    )

    trace.log(
        rider_id=payload.rider_id,
        event_type="PAYMENT_DECISION",
        details=str(
            result.get("payments")
        ),
    )

    message = Message(
        message_id=payload.message_id,
        rider_id=payload.rider_id,
        text=payload.text,
        received_at=payload.received_at,
        reply=reply,
    )

    db.add(message)
    db.commit()
    db.refresh(message)

    trace.log(
        rider_id=payload.rider_id,
        event_type="REPLY_GENERATED",
        details=reply,
    )

    return {
        "reply": reply
    }