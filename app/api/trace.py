from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import TraceStep


router = APIRouter()


def step_to_dict(step: TraceStep) -> dict:
    return {
        "at": step.at.isoformat(),
        "type": step.type,
        "name": step.name,
        "input": step.input,
        "output": step.output,
        "message_id": step.message_id,
    }


@router.get("/trace/{rider_id}")
def get_trace(rider_id: str, db: Session = Depends(get_db)):
    """Everything the agent did for this rider, in order."""
    steps = (
        db.query(TraceStep)
        .filter(TraceStep.rider_id == rider_id)
        .order_by(TraceStep.at.asc(), TraceStep.id.asc())
        .all()
    )
    return [step_to_dict(step) for step in steps]
