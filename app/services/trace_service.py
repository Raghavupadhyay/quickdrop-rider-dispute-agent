from app.db.models import TraceEvent


class TraceService:
    def __init__(self, db):
        self.db = db

    def log(
        self,
        rider_id: str,
        event_type: str,
        details: str | None = None,
    ):
        event = TraceEvent(
            rider_id=rider_id,
            event_type=event_type,
            details=details,
        )

        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)

        return event