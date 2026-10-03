from datetime import date, timedelta


def test_duplicate_trip_rows_are_counted_once(trips):
    # trips.csv contains 10 duplicated rows; T310191 (R009, 17 Sep) is one of them.
    assert trips.duplicate_rows == 10
    assert trips.get_trip("T310191")["rider_id"] == "R009"
    # 12 rows on 17 Sep, 11 distinct trips -> no incentive is due.
    assert trips.count_completed_trips("R009", date(2026, 9, 17)) == 11


def test_day_is_calendar_day_in_ist(trips):
    # T136344 starts 2026-09-16T03:10Z = 08:40 IST on the 16th.
    assert trips.get_trip("T136344")["day"] == date(2026, 9, 16)
    # Any trip at or after 18:30Z belongs to the next IST day.
    late = next(t for t in trips.trips.values() if t["started_at"].hour >= 19)
    assert late["day"] == late["started_at"].date() + timedelta(days=1)


def test_trip_ownership(trips):
    assert trips.get_trip_for_rider("T926334", "R003")["trip_id"] == "T926334"
    assert trips.get_trip_for_rider("T926334", "R004") is None
    assert trips.get_trip("T000000") is None


def test_payout_lookups(payouts):
    assert payouts.get_trip_payout("R003", "T926334") == 49
    assert payouts.get_trip_payout("R016", "T481678") == 0
    assert payouts.get_daily_incentive("R009", date(2026, 9, 13)) == 150
    assert payouts.get_daily_incentive("R009", date(2026, 9, 17)) == 0
    # R014 was charged the T637155 penalty twice on 18 Sep.
    assert payouts.get_trip_penalty("R014", "T637155") == -20
    assert payouts.get_cancellation_penalties("R014", date(2026, 9, 18)) == -20
