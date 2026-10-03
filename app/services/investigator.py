"""Deterministic investigation tools.

Each tool looks at the trips export and the payout lines and reports
*findings*: for every unit of money (a trip fare, a trip's cancellation
penalty, a day's incentive) what the policy says should have been paid, what
was paid, and the shortfall. Overpayments are reported (owed = 0) but never
clawed back. The agent calls these; the model never computes money.
"""

from dataclasses import asdict, dataclass, field
from datetime import date

from app.domain.policy import DISPUTE_WINDOW_DAYS
from app.repositories.normalize import normalize_rider_id
from app.services.payout_calculator import (
    calculate_daily_incentive,
    expected_trip_fare,
    expected_trip_penalty,
)


@dataclass
class Finding:
    unit: str  # "trip:T926334" | "penalty:T637155" | "incentive:2026-09-18"
    kind: str  # trip_fare | penalty | incentive
    expected: int
    paid: int
    owed: int
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TripCheck:
    trip_id: str
    status: str  # ok | not_found | not_owned | out_of_window
    trip: dict | None
    findings: list[Finding]
    days_old: int | None = None

    @property
    def owed(self) -> int:
        return sum(f.owed for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "trip_id": self.trip_id,
            "status": self.status,
            "trip": self.trip,
            "days_old": self.days_old,
            "owed": self.owed,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class DayAudit:
    day: date
    status: str  # ok | out_of_window | no_trips
    completed_trips: int
    rider_cancellations: int
    trips_checked: int
    incentive_expected: int
    incentive_paid: int
    findings: list[Finding]
    days_old: int

    @property
    def owed(self) -> int:
        return sum(f.owed for f in self.findings)

    @property
    def shortfalls(self) -> list[Finding]:
        return [f for f in self.findings if f.owed > 0]

    def to_dict(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "status": self.status,
            "days_old": self.days_old,
            "completed_trips": self.completed_trips,
            "rider_cancellations": self.rider_cancellations,
            "trips_checked": self.trips_checked,
            "incentive_expected": self.incentive_expected,
            "incentive_paid": self.incentive_paid,
            "owed": self.owed,
            "findings": [f.to_dict() for f in self.findings],
        }


def _trip_facts(trip: dict) -> dict:
    facts = {
        "trip_id": trip["trip_id"],
        "rider_id": trip["rider_id"],
        "day": trip["day"].isoformat(),
        "started_at": trip["started_at"].isoformat(),
        "status": trip["status"],
        "distance_km": trip["distance_km"],
        "surge_multiplier": trip["surge_multiplier"],
    }
    if trip.get("data_fixes"):
        facts["data_fixes"] = trip["data_fixes"]
    return facts


class Investigator:
    def __init__(self, trips_repo, payouts_repo):
        self.trips = trips_repo
        self.payouts = payouts_repo

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def days_old(day: date, as_of: date) -> int:
        return (as_of - day).days

    @classmethod
    def in_window(cls, day: date, as_of: date) -> bool:
        age = cls.days_old(day, as_of)
        return 0 <= age <= DISPUTE_WINDOW_DAYS

    def _trip_findings(self, rider_id: str, trip: dict) -> list[Finding]:
        findings: list[Finding] = []
        facts = _trip_facts(trip)

        fare_expected = expected_trip_fare(trip)
        fare_paid = self.payouts.get_trip_payout(rider_id, trip["trip_id"])
        findings.append(
            Finding(
                unit=f"trip:{trip['trip_id']}",
                kind="trip_fare",
                expected=fare_expected,
                paid=fare_paid,
                owed=max(fare_expected - fare_paid, 0),
                facts=facts,
            )
        )

        penalty_expected = expected_trip_penalty(trip)
        penalty_paid = self.payouts.get_trip_penalty(rider_id, trip["trip_id"])
        if penalty_expected != 0 or penalty_paid != 0:
            findings.append(
                Finding(
                    unit=f"penalty:{trip['trip_id']}",
                    kind="penalty",
                    expected=penalty_expected,
                    paid=penalty_paid,
                    # expected -10, paid -20  ->  over-deducted by 10
                    owed=max(penalty_expected - penalty_paid, 0),
                    facts={
                        **facts,
                        "penalties_charged": abs(penalty_paid) // 10 if penalty_paid else 0,
                    },
                )
            )

        return findings

    # -------------------------------------------------------------------- tools

    def check_trip(self, rider_id: str, trip_id: str, as_of: date) -> TripCheck:
        """Everything about one trip: does it exist, is it this rider's, is it
        within the 7-day window, and was its fare / penalty paid correctly."""
        trip = self.trips.get_trip(trip_id)

        if trip is None:
            return TripCheck(trip_id=trip_id, status="not_found", trip=None, findings=[])

        if trip["rider_id"] != normalize_rider_id(rider_id):
            # Report that it exists, but nothing about it: it is another rider's trip.
            return TripCheck(
                trip_id=trip_id,
                status="not_owned",
                trip={"trip_id": trip_id, "day": trip["day"].isoformat()},
                findings=[],
            )

        age = self.days_old(trip["day"], as_of)
        findings = self._trip_findings(rider_id, trip)

        if not self.in_window(trip["day"], as_of):
            return TripCheck(
                trip_id=trip_id,
                status="out_of_window",
                trip=_trip_facts(trip),
                findings=findings,
                days_old=age,
            )

        return TripCheck(
            trip_id=trip_id,
            status="ok",
            trip=_trip_facts(trip),
            findings=findings,
            days_old=age,
        )

    def audit_day(self, rider_id: str, day: date, as_of: date) -> DayAudit:
        """Recompute one IST day for the rider: every trip's fare and penalty,
        plus the daily incentive, against what the payout lines say was paid."""
        trips = self.trips.get_rider_trips_for_day(rider_id, day)
        lines = self.payouts.get_lines_for_day(rider_id, day)
        age = self.days_old(day, as_of)

        completed = sum(1 for t in trips if t["status"] == "completed")
        cancelled_by_rider = sum(1 for t in trips if t["status"] == "cancelled_by_rider")

        findings: list[Finding] = []
        for trip in trips:
            findings.extend(self._trip_findings(rider_id, trip))

        # Penalty lines for trips that are not this rider's cancellations that day.
        day_trip_ids = {t["trip_id"] for t in trips}
        for line in lines:
            if line["line_type"] == "cancellation_penalty" and line["trip_id"] not in day_trip_ids:
                findings.append(
                    Finding(
                        unit=f"penalty:{line['trip_id'] or day.isoformat()}",
                        kind="penalty",
                        expected=0,
                        paid=line["amount"],
                        owed=max(0 - line["amount"], 0),
                        facts={"trip_id": line["trip_id"], "day": day.isoformat(), "note": "penalty without a matching rider cancellation"},
                    )
                )

        incentive_expected = calculate_daily_incentive(completed)
        incentive_paid = self.payouts.get_daily_incentive(rider_id, day)
        findings.append(
            Finding(
                unit=f"incentive:{day.isoformat()}",
                kind="incentive",
                expected=incentive_expected,
                paid=incentive_paid,
                owed=max(incentive_expected - incentive_paid, 0),
                facts={"day": day.isoformat(), "completed_trips": completed},
            )
        )

        if not trips and not lines:
            status = "no_trips"
        elif not self.in_window(day, as_of):
            status = "out_of_window"
        else:
            status = "ok"

        return DayAudit(
            day=day,
            status=status,
            completed_trips=completed,
            rider_cancellations=cancelled_by_rider,
            trips_checked=len(trips),
            incentive_expected=incentive_expected,
            incentive_paid=incentive_paid,
            findings=findings,
            days_old=age,
        )
