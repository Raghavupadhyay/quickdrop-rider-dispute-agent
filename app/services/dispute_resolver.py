from app.services.payout_calculator import (
    calculate_expected_trip_amount,
    calculate_daily_incentive,
)


class DisputeResolver:
    def __init__(
        self,
        trips_repo,
        payouts_repo,
    ):
        self.trips_repo = trips_repo
        self.payouts_repo = payouts_repo

    # ---------------------------------------------------------
    # Shared trip resolver
    # ---------------------------------------------------------

    def _resolve_trip_shortfall(
        self,
        rider_id: str,
        trip_id: str,
    ):
        trip = self.trips_repo.get_trip_for_rider(
            trip_id=trip_id,
            rider_id=rider_id,
        )

        if trip is None:
            return {
                "status": "invalid",
                "owed": 0,
                "reason": (
                    "trip_not_found_or_not_owned"
                ),
                "reference_key": (
                    f"trip:{rider_id}:{trip_id}"
                ),
            }

        expected = calculate_expected_trip_amount(
            status=trip["status"],
            distance_km=trip["distance_km"],
            surge_multiplier=(
                trip["surge_multiplier"]
            ),
        )

        actual = (
            self.payouts_repo.get_trip_payout(
                rider_id=rider_id,
                trip_id=trip_id,
            )
        )

        owed = max(
            expected - actual,
            0,
        )

        return {
            "status": (
                "valid"
                if owed > 0
                else "invalid"
            ),
            "expected": expected,
            "actual": actual,
            "owed": owed,
            "trip_id": trip_id,
            "reference_key": (
                f"trip:{rider_id}:{trip_id}"
            ),
        }

    # ---------------------------------------------------------
    # Missing surge
    # ---------------------------------------------------------

    def resolve_missing_surge(
        self,
        rider_id: str,
        trip_id: str,
    ):
        return self._resolve_trip_shortfall(
            rider_id=rider_id,
            trip_id=trip_id,
        )

    # ---------------------------------------------------------
    # Missing trip payment
    # ---------------------------------------------------------

    def resolve_missing_trip_payment(
        self,
        rider_id: str,
        trip_id: str,
    ):
        return self._resolve_trip_shortfall(
            rider_id=rider_id,
            trip_id=trip_id,
        )

    # ---------------------------------------------------------
    # Multiple missing trip payments
    # ---------------------------------------------------------

    def resolve_missing_trip_payments(
        self,
        rider_id: str,
        trip_ids: list[str],
    ):
        results = []
        total_owed = 0

        normalized_ids = sorted(
            set(trip_ids)
        )

        for trip_id in normalized_ids:
            result = (
                self.resolve_missing_trip_payment(
                    rider_id=rider_id,
                    trip_id=trip_id,
                )
            )

            results.append(
                {
                    "trip_id": trip_id,
                    **result,
                }
            )

            total_owed += result.get(
                "owed",
                0,
            )

        reference_part = ",".join(
            normalized_ids
        )

        return {
            "status": (
                "valid"
                if total_owed > 0
                else "invalid"
            ),
            "owed": total_owed,
            "trips": results,
            "reference_key": (
                f"trips:{rider_id}:"
                f"{reference_part}"
            ),
        }

    # ---------------------------------------------------------
    # Missing daily incentive
    # ---------------------------------------------------------

    def resolve_missing_incentive(
        self,
        rider_id: str,
        day,
    ):
        completed_count = (
            self.trips_repo
            .count_completed_trips(
                rider_id=rider_id,
                day=day,
            )
        )

        expected = (
            calculate_daily_incentive(
                completed_count
            )
        )

        actual = (
            self.payouts_repo
            .get_daily_incentive(
                rider_id=rider_id,
                day=day,
            )
        )

        owed = max(
            expected - actual,
            0,
        )

        return {
            "status": (
                "valid"
                if owed > 0
                else "invalid"
            ),
            "completed_trips": (
                completed_count
            ),
            "expected": expected,
            "actual": actual,
            "owed": owed,
            "reference_key": (
                f"incentive:{rider_id}:"
                f"{day.isoformat()}"
            ),
        }

    # ---------------------------------------------------------
    # Duplicate cancellation penalty
    # ---------------------------------------------------------

    def resolve_duplicate_penalty(
        self,
        rider_id: str,
        day,
    ):
        rider_cancellations = (
            self.trips_repo
            .count_rider_cancellations(
                rider_id=rider_id,
                day=day,
            )
        )

        expected_penalty = -(
            rider_cancellations * 10
        )

        actual_penalty = (
            self.payouts_repo
            .get_cancellation_penalties(
                rider_id=rider_id,
                day=day,
            )
        )

        # Example:
        #
        # expected = -10
        # actual   = -20
        #
        # Rider was over-deducted by ₹10.
        owed = max(
            expected_penalty
            - actual_penalty,
            0,
        )

        return {
            "status": (
                "valid"
                if owed > 0
                else "invalid"
            ),
            "rider_cancellations": (
                rider_cancellations
            ),
            "expected_penalty": (
                expected_penalty
            ),
            "actual_penalty": (
                actual_penalty
            ),
            "owed": owed,
            "reference_key": (
                f"penalty:{rider_id}:"
                f"{day.isoformat()}"
            ),
        }