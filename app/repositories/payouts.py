# app/repositories/payouts.py

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path


class PayoutRepository:
    """In-memory view of data/payout_lines.csv: what riders were actually paid.

    Line types are `trip`, `daily_incentive` and `cancellation_penalty`
    (penalties are negative). payout_date is the IST day of the trip.
    """

    def __init__(self, csv_path: str):
        self.lines: list[dict] = []
        self._by_rider: dict[str, list[dict]] = defaultdict(list)

        with open(Path(csv_path), newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["amount"] = int(row["amount"])
                row["payout_date"] = date.fromisoformat(row["payout_date"])

                self.lines.append(row)
                self._by_rider[row["rider_id"]].append(row)

    def _rider_lines(self, rider_id: str) -> list[dict]:
        return self._by_rider.get(rider_id, [])

    def get_trip_payout(self, rider_id: str, trip_id: str) -> int:
        """Sum of `trip` lines paid for this trip."""
        return sum(
            line["amount"]
            for line in self._rider_lines(rider_id)
            if line["line_type"] == "trip" and line["trip_id"] == trip_id
        )

    def get_trip_penalty(self, rider_id: str, trip_id: str) -> int:
        """Sum of `cancellation_penalty` lines charged for this trip (<= 0)."""
        return sum(
            line["amount"]
            for line in self._rider_lines(rider_id)
            if line["line_type"] == "cancellation_penalty"
            and line["trip_id"] == trip_id
        )

    def get_daily_incentive(self, rider_id: str, day: date) -> int:
        return sum(
            line["amount"]
            for line in self._rider_lines(rider_id)
            if line["line_type"] == "daily_incentive"
            and line["payout_date"] == day
        )

    def get_cancellation_penalties(self, rider_id: str, day: date) -> int:
        return sum(
            line["amount"]
            for line in self._rider_lines(rider_id)
            if line["line_type"] == "cancellation_penalty"
            and line["payout_date"] == day
        )

    def get_lines_for_day(self, rider_id: str, day: date) -> list[dict]:
        return [
            line
            for line in self._rider_lines(rider_id)
            if line["payout_date"] == day
        ]
