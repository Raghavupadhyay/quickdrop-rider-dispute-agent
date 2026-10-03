import os
from functools import lru_cache

from app.ai.client import LLMClient
from app.ai.extractor import ClaimExtractor
from app.clients.payswift import PaySwiftClient
from app.repositories.payouts import PayoutRepository
from app.repositories.trips import TripsRepository
from app.services.agent import DisputeAgent
from app.services.investigator import Investigator


DATA_DIR = os.getenv("DATA_DIR", "data")


def get_payswift() -> PaySwiftClient:
    return PaySwiftClient()


@lru_cache
def get_agent() -> DisputeAgent:
    """One agent per process: the CSV exports are loaded once, the LLM client is
    shared. Everything per-request (DB session, tracer) is created in handle()."""
    trips = TripsRepository(os.path.join(DATA_DIR, "trips.csv"))
    payouts = PayoutRepository(os.path.join(DATA_DIR, "payout_lines.csv"))

    return DisputeAgent(
        extractor=ClaimExtractor(LLMClient()),
        investigator=Investigator(trips, payouts),
    )
