# app/services/payout_calculator.py
#
# Pure functions implementing docs/policy.md. No I/O, no model.

from decimal import Decimal, ROUND_HALF_UP

from app.domain.policy import (
    BASE_FARE,
    FREE_DISTANCE_KM,
    PER_KM_RATE,
    DAILY_INCENTIVE_THRESHOLD,
    DAILY_INCENTIVE_AMOUNT,
    RIDER_CANCELLATION_PENALTY,
)


def round_half_up(value: float) -> int:
    """Round to the nearest rupee, 0.5 rounds up (not banker's rounding)."""
    return int(
        Decimal(str(value)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def calculate_completed_trip_fare(
    distance_km: float,
    surge_multiplier: float,
) -> int:
    """₹25 base + ₹6 per km after the first 2 km; surge applies to the whole fare."""
    extra_distance = max(distance_km - FREE_DISTANCE_KM, 0)

    base_amount = BASE_FARE + (extra_distance * PER_KM_RATE)

    total = base_amount * surge_multiplier

    return round_half_up(total)


def calculate_expected_trip_amount(
    status: str,
    distance_km: float,
    surge_multiplier: float,
) -> int:
    """Net effect of one trip on the payout: fare, -₹10 penalty, or nothing."""
    if status == "completed":
        return calculate_completed_trip_fare(
            distance_km=distance_km,
            surge_multiplier=surge_multiplier,
        )

    if status == "cancelled_by_rider":
        return -RIDER_CANCELLATION_PENALTY

    if status == "cancelled_by_customer":
        return 0

    raise ValueError(f"Unknown trip status: {status}")


def expected_trip_fare(trip: dict) -> int:
    """The `trip` payout line a trip should have produced (0 if not completed)."""
    if trip["status"] != "completed":
        return 0

    return calculate_completed_trip_fare(
        distance_km=trip["distance_km"],
        surge_multiplier=trip["surge_multiplier"],
    )


def expected_trip_penalty(trip: dict) -> int:
    """The `cancellation_penalty` line a trip should have produced (<= 0)."""
    if trip["status"] == "cancelled_by_rider":
        return -RIDER_CANCELLATION_PENALTY

    return 0


def calculate_daily_incentive(completed_trip_count: int) -> int:
    if completed_trip_count >= DAILY_INCENTIVE_THRESHOLD:
        return DAILY_INCENTIVE_AMOUNT

    return 0
