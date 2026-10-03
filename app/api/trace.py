from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import TraceEvent


router = APIRouter()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.get("/trace/{rider_id}")
def get_trace(
    rider_id: str,
    db: Session = Depends(get_db),
):
    events = (
        db.query(TraceEvent)
        .filter(
            TraceEvent.rider_id == rider_id
        )
        .order_by(
            TraceEvent.created_at.asc(),
            TraceEvent.id.asc(),
        )
        .all()
    )

    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "details": event.details,
            "created_at": event.created_at,
        }
        for event in events
    ]