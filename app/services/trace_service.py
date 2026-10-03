from datetime import datetime, timezone

from app.db.models import TraceStep


def _jsonable(value):
    """Make dates, datetimes and nested dataclass-ish dicts JSON friendly."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


class Tracer:
    """Writes the agent's steps for one rider in order. Each step is committed
    as it happens, so the trace survives a failure half-way through."""

    def __init__(self, db, rider_id: str, message_id: str | None = None):
        self.db = db
        self.rider_id = rider_id
        self.message_id = message_id

    def step(self, type: str, name: str, input=None, output=None) -> TraceStep:
        row = TraceStep(
            rider_id=self.rider_id,
            message_id=self.message_id,
            at=datetime.now(timezone.utc),
            type=type,
            name=name,
            input=_jsonable(input),
            output=_jsonable(output),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def message_in(self, name: str, input):
        return self.step("message_in", name, input, None)

    def tool_call(self, name: str, input, output):
        return self.step("tool_call", name, input, output)

    def decision(self, name: str, input, output):
        return self.step("decision", name, input, output)

    def reply(self, text: str, name: str = "reply_to_rider"):
        return self.step("reply", name, None, text)

    def error(self, name: str, input, output):
        return self.step("error", name, input, output)
