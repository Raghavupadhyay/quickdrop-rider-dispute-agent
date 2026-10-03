from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.messages import router as messages_router
from app.api.ops import router as ops_router
from app.api.trace import router as trace_router
from app.db.database import SessionLocal, init_db
from app.services.finalizer import PayoutFinalizer


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # waits for Postgres, creates tables
    finalizer = PayoutFinalizer(SessionLocal)
    finalizer.start()  # confirms payouts PaySwift was still processing at reply time
    try:
        yield
    finally:
        finalizer.stop()


app = FastAPI(
    title="QuickDrop Rider Payout Dispute Desk",
    lifespan=lifespan,
)

app.include_router(messages_router)
app.include_router(trace_router)
app.include_router(ops_router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ops")


@app.get("/health")
def health():
    return {"status": "ok"}
