# tests/test_payout_calculator.py

from app.services.payout_calculator import (
    calculate_completed_trip_fare,
    calculate_expected_trip_amount,
    calculate_daily_incentive,
)


def test_trip_without_surge():
    assert calculate_completed_trip_fare(6.0, 1.0) == 49


def test_trip_with_surge():
    assert calculate_completed_trip_fare(6.0, 1.5) == 74


def test_distance_under_two_km():
    assert calculate_completed_trip_fare(1.5, 1.0) == 25


def test_rider_cancel_penalty():
    assert (
        calculate_expected_trip_amount(
            "cancelled_by_rider",
            0,
            1.0,
        )
        == -10
    )


def test_customer_cancel_zero():
    assert (
        calculate_expected_trip_amount(
            "cancelled_by_customer",
            0,
            1.0,
        )
        == 0
    )


def test_daily_incentive_12_trips():
    assert calculate_daily_incentive(12) == 150


def test_daily_incentive_11_trips():
    assert calculate_daily_incentive(11) == 0