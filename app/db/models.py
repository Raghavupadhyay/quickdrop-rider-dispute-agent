from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Message(Base):
    """One rider message from the vendor. message_id (wamid) is unique, so a
    redelivered message is answered with the stored reply instead of being
    processed again."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String, unique=True, nullable=False, index=True)
    rider_id = Column(String, nullable=False, index=True)
    text = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    reply = Column(Text, nullable=True)
    # processing -> done | failed
    status = Column(String, nullable=False, default="processing")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class TraceStep(Base):
    """Everything the agent did, in order. Shape follows SUBMISSION.md:
    at, type (message_in | tool_call | decision | reply | error), name, input, output."""

    __tablename__ = "trace_steps"

    id = Column(Integer, primary_key=True)
    rider_id = Column(String, nullable=False, index=True)
    message_id = Column(String, nullable=True, index=True)
    at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)


class OpsItem(Base):
    """Something waiting for a human: an `approval` (money to release) or an
    `escalation` (something the agent could not or should not decide)."""

    __tablename__ = "ops_items"

    id = Column(Integer, primary_key=True)
    rider_id = Column(String, nullable=False, index=True)
    message_id = Column(String, nullable=True)
    type = Column(String, nullable=False)  # approval | escalation
    amount = Column(Integer, nullable=True)  # rupees, approvals only
    reason = Column(Text, nullable=False)
    # pending -> approved | rejected | closed
    status = Column(String, nullable=False, default="pending", index=True)
    # Units of money this item covers, e.g. "trip:T926334 penalty:T637155".
    reference_key = Column(String, nullable=True, index=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class Payment(Base):
    """A payout we asked PaySwift for. PaySwift is the source of truth for what
    was paid; this table is our record of attempts and of the once-a-day rule."""

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    rider_id = Column(String, nullable=False, index=True)
    message_id = Column(String, nullable=True)
    ops_item_id = Column(Integer, ForeignKey("ops_items.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)  # auto | approved
    # processing -> completed | failed
    status = Column(String, nullable=False, default="processing")
    reference = Column(String, nullable=False)
    reference_key = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, unique=True, nullable=False)
    # IST calendar day the rider's message was received on; the once-a-day
    # auto-pay rule is counted per business day.
    business_day = Column(Date, nullable=False, index=True)
    payswift_payout_id = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
