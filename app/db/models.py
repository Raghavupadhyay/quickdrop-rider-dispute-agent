from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
)

from sqlalchemy.orm import relationship

from app.db.database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(
        Integer,
        primary_key=True,
    )

    message_id = Column(
        String,
        unique=True,
        nullable=False,
        index=True,
    )

    rider_id = Column(
        String,
        nullable=False,
        index=True,
    )

    text = Column(
        Text,
        nullable=False,
    )

    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    reply = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )


class Dispute(Base):
    __tablename__ = "disputes"

    id = Column(
        Integer,
        primary_key=True,
    )

    rider_id = Column(
        String,
        nullable=False,
        index=True,
    )

    dispute_type = Column(
        String,
        nullable=False,
    )

    status = Column(
        String,
        nullable=False,
        default="OPEN",
    )

    owed_amount = Column(
        Integer,
        nullable=False,
        default=0,
    )

    reason = Column(
        Text,
        nullable=True,
    )

    reference_key = Column(
        String,
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )


class PaymentAction(Base):
    __tablename__ = "payment_actions"

    id = Column(
        Integer,
        primary_key=True,
    )

    rider_id = Column(
        String,
        nullable=False,
        index=True,
    )

    dispute_id = Column(
        Integer,
        ForeignKey("disputes.id"),
        nullable=True,
    )

    amount = Column(
        Integer,
        nullable=False,
    )

    action = Column(
        String,
        nullable=False,
    )

    status = Column(
        String,
        nullable=False,
        default="PENDING",
    )

    idempotency_key = Column(
        String,
        unique=True,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    dispute = relationship(
        "Dispute"
    )


class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id = Column(
        Integer,
        primary_key=True,
    )

    rider_id = Column(
        String,
        nullable=False,
        index=True,
    )

    dispute_id = Column(
        Integer,
        ForeignKey("disputes.id"),
        nullable=False,
    )

    amount = Column(
        Integer,
        nullable=False,
    )

    status = Column(
        String,
        nullable=False,
        default="PENDING",
    )

    reason = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id = Column(
        Integer,
        primary_key=True,
    )

    rider_id = Column(
        String,
        nullable=False,
        index=True,
    )

    event_type = Column(
        String,
        nullable=False,
    )

    details = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )