# app/repositories/trips.py

import csv
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


class TripsRepository:
    """In-memory view of data/trips.csv (the export from the trips system).

    The export contains duplicate rows for some trips, so trips are keyed by
    trip_id and every trip is counted once. "Day" is the calendar day in IST.
    """

    def __init__(self, csv_path: str):
        self.trips: dict[str, dict] = {}
        self.duplicate_rows = 0

        with open(Path(csv_path), newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["trip_id"] in self.trips:
                    self.duplicate_rows += 1
                    continue

                row["distance_km"] = float(row["distance_km"])
                row["surge_multiplier"] = float(row["surge_multiplier"])
                row["started_at"] = datetime.fromisoformat(row["started_at"])
                row["day"] = row["started_at"].astimezone(IST).date()

                self.trips[row["trip_id"]] = row

    def get_trip(self, trip_id: str) -> dict | None:
        return self.trips.get(trip_id)

    def get_trip_for_rider(self, trip_id: str, rider_id: str) -> dict | None:
        trip = self.get_trip(trip_id)

        if trip is None or trip["rider_id"] != rider_id:
            return None

        return trip

    def get_rider_trips_for_day(self, rider_id: str, day: date) -> list[dict]:
        return sorted(
            (
                trip
                for trip in self.trips.values()
                if trip["rider_id"] == rider_id and trip["day"] == day
            ),
            key=lambda trip: trip["started_at"],
        )

    def count_completed_trips(self, rider_id: str, day: date) -> int:
        return sum(
            1
            for trip in self.get_rider_trips_for_day(rider_id, day)
            if trip["status"] == "completed"
        )

    def count_rider_cancellations(self, rider_id: str, day: date) -> int:
        return sum(
            1
            for trip in self.get_rider_trips_for_day(rider_id, day)
            if trip["status"] == "cancelled_by_rider"
        )
