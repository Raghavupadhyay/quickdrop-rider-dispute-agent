# app/repositories/trips.py

import csv
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.repositories.normalize import normalize_distance_km, normalize_rider_id


IST = ZoneInfo("Asia/Kolkata")


class TripsRepository:
    """In-memory view of data/trips.csv (the export from the trips system).

    The export is used as it came: duplicate rows are dropped (trips are keyed
    by trip_id), rider ids are normalised ("R7" -> "R007") and metre distances
    are converted to km. Every such fix is kept on the row in `data_fixes`.
    "Day" is the calendar day in IST.
    """

    def __init__(self, csv_path: str):
        self.trips: dict[str, dict] = {}
        self.duplicate_rows = 0
        self.fixed_rows = 0

        with open(Path(csv_path), newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["trip_id"] = row["trip_id"].strip().upper()
                if row["trip_id"] in self.trips:
                    self.duplicate_rows += 1
                    continue

                fixes: list[str] = []

                rider_id = normalize_rider_id(row["rider_id"])
                if rider_id != row["rider_id"]:
                    fixes.append(f"rider id {row['rider_id']!r} read as {rider_id}")
                row["rider_id"] = rider_id

                distance, note = normalize_distance_km(float(row["distance_km"]))
                if note:
                    fixes.append(note)
                row["distance_km"] = distance

                row["surge_multiplier"] = float(row["surge_multiplier"])
                row["started_at"] = datetime.fromisoformat(row["started_at"])
                row["day"] = row["started_at"].astimezone(IST).date()
                row["data_fixes"] = fixes
                if fixes:
                    self.fixed_rows += 1

                self.trips[row["trip_id"]] = row

    def get_trip(self, trip_id: str) -> dict | None:
        return self.trips.get(trip_id.strip().upper())

    def get_trip_for_rider(self, trip_id: str, rider_id: str) -> dict | None:
        trip = self.get_trip(trip_id)

        if trip is None or trip["rider_id"] != normalize_rider_id(rider_id):
            return None

        return trip

    def get_rider_trips_for_day(self, rider_id: str, day: date) -> list[dict]:
        rider_id = normalize_rider_id(rider_id)
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
