"""Shared fixtures: an in-memory SQLite database, a fake PaySwift with a
scriptable failure mode, the real data exports, and an agent that uses the
rule-based reader only (no network, deterministic)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401  (registers tables)
from app.ai.client import LLMClient
from app.ai.extractor import ClaimExtractor
from app.clients.payswift import PaySwiftError
from app.db.database import Base
from app.repositories.payouts import PayoutRepository
from app.repositories.trips import TripsRepository
from app.services.agent import DisputeAgent
from app.services.investigator import Investigator


class FakePaySwift:
    """In-memory PaySwift. `script` is a list of failure modes consumed one per
    create_payout call:
      "503"              instant failure, nothing processed
      "504_processed"    processed, but reported as a 504
      "timeout_pending"  we time out; the payout only lands when release() is called
      "in_progress"      409 request_in_progress (first attempt still running)
      "400"              rejected for good
    `list_down` makes listing fail."""

    def __init__(self):
        self.payouts: list[dict] = []
        self.script: list[str] = []
        self.pending: list[dict] = []
        self.list_down = False
        self.calls = 0

    def list_payouts(self, rider_id):
        if self.list_down:
            raise PaySwiftError("503 service_unavailable", status_code=503)
        return [dict(p) for p in self.payouts if p["rider_id"] == rider_id]

    def _record(self, rider_id, amount, reference, idempotency_key):
        payout = {
            "payout_id": f"pout_{len(self.payouts) + len(self.pending) + 1:04d}",
            "rider_id": rider_id,
            "amount": amount,
            "reference": reference,
            "status": "processed",
            "idempotency_key": idempotency_key,
        }
        self.payouts.append(payout)
        return payout

    def release(self):
        """Finish the slow-path payouts that were still pending."""
        for payout in self.pending:
            self.payouts.append(payout)
        self.pending = []

    def create_payout(self, rider_id, amount, reference, idempotency_key, timeout=None):
        self.calls += 1
        for payout in self.pending:
            if payout["idempotency_key"] == idempotency_key:
                raise PaySwiftError("409 request_in_progress", status_code=409, maybe_processed=True, in_progress=True)
        if self.script:
            mode = self.script.pop(0)
            if mode == "503":
                raise PaySwiftError("503 service_unavailable", status_code=503, maybe_processed=True)
            if mode == "504_processed":
                self._record(rider_id, amount, reference, idempotency_key)
                raise PaySwiftError("504 gateway_timeout", status_code=504, maybe_processed=True)
            if mode == "timeout_pending":
                self.pending.append({
                    "payout_id": f"pout_{len(self.payouts) + len(self.pending) + 1:04d}",
                    "rider_id": rider_id, "amount": amount, "reference": reference,
                    "status": "processed", "idempotency_key": idempotency_key,
                })
                raise PaySwiftError("timed out", maybe_processed=True, timed_out=True)
            if mode == "in_progress":
                raise PaySwiftError("409 request_in_progress", status_code=409, maybe_processed=True, in_progress=True)
            if mode == "400":
                raise PaySwiftError("400 invalid_amount", status_code=400)
        for payout in self.payouts:
            if payout["idempotency_key"] == idempotency_key:
                if payout["amount"] != amount or payout["reference"] != reference:
                    raise PaySwiftError("409 idempotency_key_reused", status_code=409, maybe_processed=True)
                return dict(payout)
        return dict(self._record(rider_id, amount, reference, idempotency_key))

    def total(self, rider_id) -> int:
        return sum(p["amount"] for p in self.payouts if p["rider_id"] == rider_id)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def payswift():
    return FakePaySwift()


@pytest.fixture(scope="session")
def trips():
    return TripsRepository("data/trips.csv")


@pytest.fixture(scope="session")
def payouts():
    return PayoutRepository("data/payout_lines.csv")


@pytest.fixture(scope="session")
def investigator(trips, payouts):
    return Investigator(trips, payouts)


@pytest.fixture
def agent(investigator, payswift):
    """Agent with the LLM switched off: the rule-based reader handles every message."""
    return DisputeAgent(
        extractor=ClaimExtractor(LLMClient(api_key="")),
        investigator=investigator,
        payswift_factory=lambda: payswift,
    )
