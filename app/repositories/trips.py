# app/repositories/trips.py

import csv
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


class TripsRepository:
    def __init__(self, csv_path: str):
        self.trips = []

        with open(Path(csv_path), newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                row["distance_km"] = float(row["distance_km"])
                row["surge_multiplier"] = float(row["surge_multiplier"])

                # parse timestamp once
                row["started_at"] = datetime.fromisoformat(row["started_at"])

                self.trips.append(row)

    def get_trip(self, trip_id: str):
        for trip in self.trips:
            if trip["trip_id"] == trip_id:
                return trip

        return None

    def get_trip_for_rider(self, trip_id: str, rider_id: str):
        trip = self.get_trip(trip_id)

        if trip is None:
            return None

        if trip["rider_id"] != rider_id:
            return None

        return trip

    def get_rider_trips_for_day(self, rider_id: str, day):
        result = []

        for trip in self.trips:
            if trip["rider_id"] != rider_id:
                continue

            trip_day = trip["started_at"].astimezone(IST).date()

            if trip_day == day:
                result.append(trip)

        return result

    def count_completed_trips(self, rider_id: str, day) -> int:
        trips = self.get_rider_trips_for_day(rider_id, day)

        return sum(
            1
            for trip in trips
            if trip["status"] == "completed"
        )

    def count_rider_cancellations(self, rider_id: str, day) -> int:
        trips = self.get_rider_trips_for_day(rider_id, day)

        return sum(
            1
            for trip in trips
            if trip["status"] == "cancelled_by_rider"
        )