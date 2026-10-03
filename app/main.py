from fastapi import FastAPI

from app.api.messages import router as messages_router
from app.api.trace import router as trace_router
from app.api.ops import router as ops_router

from app.db.database import Base, engine


Base.metadata.create_all(
    bind=engine
)


app = FastAPI(
    title="QuickDrop Rider Payout Dispute Desk"
)


app.include_router(
    messages_router
)

app.include_router(
    trace_router
)

app.include_router(
    ops_router
)


@app.get("/")
def root():
    return {
        "service": "QuickDrop Rider Payout Dispute Desk",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }