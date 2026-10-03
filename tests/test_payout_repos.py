from app.repositories.trips import TripsRepository
from app.repositories.payouts import PayoutRepository
from app.services.dispute_resolver import DisputeResolver


def test_real_missing_surge_case():
    trips = TripsRepository("data/trips.csv")
    payouts = PayoutRepository("data/payout_lines.csv")

    resolver = DisputeResolver(
        trips_repo=trips,
        payouts_repo=payouts,
    )

    result = resolver.resolve_missing_surge(
        rider_id="R003",
        trip_id="T926334",
    )

    assert result["expected"] == 74
    assert result["actual"] == 49
    assert result["owed"] == 25