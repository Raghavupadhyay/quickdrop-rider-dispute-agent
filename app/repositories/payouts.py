# app/repositories/payouts.py

import csv
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from pyparsing import line


IST = ZoneInfo("Asia/Kolkata")

# app/repositories/payouts.py

import csv
from datetime import date
from pathlib import Path


class PayoutRepository:
    def __init__(self, csv_path: str):
        self.lines = []

        with open(Path(csv_path), newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                row["amount"] = int(row["amount"])
                row["payout_date"] = date.fromisoformat(
                    row["payout_date"]
                )

                self.lines.append(row)

    def get_trip_payout(
        self,
        rider_id: str,
        trip_id: str,
    ) -> int:
        total = 0

        for line in self.lines:
            if (
                line["rider_id"] == rider_id
                and line["trip_id"] == trip_id
                and line["line_type"] == "trip"
            ):
                total += line["amount"]

        return total

    def get_daily_incentive(
        self,
        rider_id: str,
        day,
    ) -> int:
        return sum(
            line["amount"]
            for line in self.lines
            if line["rider_id"] == rider_id
            and line["line_type"] == "daily_incentive"
            and line["payout_date"] == day
        )

    def get_cancellation_penalties(
        self,
        rider_id: str,
        day,
    ) -> int:
        return sum(
            line["amount"]
            for line in self.lines
            if line["rider_id"] == rider_id
            and line["line_type"] == "cancellation_penalty"
            and line["payout_date"] == day
        )
        def _get_line_day(self, line):
            if line.get("payout_date"):
                return datetime.fromisoformat(
                    line["payout_date"]
                ).date()

            return None