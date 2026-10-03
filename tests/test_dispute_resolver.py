# tests/test_dispute_resolver.py

from datetime import date

from app.repositories.trips import TripsRepository
from app.repositories.payouts import PayoutRepository
from app.services.dispute_resolver import DisputeResolver


def make_resolver():
    return DisputeResolver(
        trips_repo=TripsRepository("data/trips.csv"),
        payouts_repo=PayoutRepository(
            "data/payout_lines.csv"
        ),
    )


def test_missing_surge_real_case():
    resolver = make_resolver()

    result = resolver.resolve_missing_surge(
        rider_id="R003",
        trip_id="T926334",
    )

    assert result["owed"] == 25


def test_missing_incentive():
    resolver = make_resolver()

    result = resolver.resolve_missing_incentive(
        rider_id="R024",
        day=date(2026, 9, 18),
    )

    assert result["owed"] == 150


def test_duplicate_penalty():
    resolver = make_resolver()

    result = resolver.resolve_duplicate_penalty(
        rider_id="R014",
        day=date(2026, 9, 18),
    )

    assert result["owed"] == 10


def test_multiple_missing_trip_payments():
    resolver = make_resolver()

    result = resolver.resolve_missing_trip_payments(
        rider_id="R033",
        trip_ids=["T795007", "T206956"],
    )

    assert result["owed"] == 176    