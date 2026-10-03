# app/services/payout_calculator.py

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
    extra_distance = max(distance_km - FREE_DISTANCE_KM, 0)

    base_amount = BASE_FARE + (extra_distance * PER_KM_RATE)

    total = base_amount * surge_multiplier

    return round_half_up(total)


def calculate_expected_trip_amount(
    status: str,
    distance_km: float,
    surge_multiplier: float,
) -> int:
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


def calculate_daily_incentive(completed_trip_count: int) -> int:
    if completed_trip_count >= DAILY_INCENTIVE_THRESHOLD:
        return DAILY_INCENTIVE_AMOUNT

    return 0