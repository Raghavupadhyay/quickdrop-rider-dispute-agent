"""The deterministic half of the sample conversations: given the right place
to look, the investigator must find exactly the amounts data/conversations.json
expects. These numbers are the policy applied to the exports."""

from datetime import date

import pytest


SEP = lambda d: date(2026, 9, d)  # noqa: E731


def owed_units(result):
    return {f.unit: f.owed for f in result.findings if f.owed > 0}


@pytest.mark.parametrize(
    "rider, day, as_of, expected_owed, expected_units",
    [
        ("R003", 20, 22, 25, {"trip:T926334": 25}),       # surge not applied, found without a trip id
        ("R027", 20, 23, 15, {"trip:T604546": 15}),       # surge not applied
        ("R027", 21, 23, 10, {"penalty:T936237": 10}),    # penalty charged twice
        ("R011", 19, 22, 0, {}),                           # 10 trips, no incentive due
        ("R005", 21, 22, 150, {"incentive:2026-09-21": 150}),
        ("R016", 19, 22, 425, None),                       # 5 unpaid trips
        ("R034", 19, 23, 249, None),                       # 3 unpaid trips
        ("R014", 18, 22, 10, {"penalty:T637155": 10}),
        ("R024", 18, 23, 150, {"incentive:2026-09-18": 150}),
        ("R025", 19, 23, 0, {}),                           # 11 trips
        ("R009", 17, 23, 0, {}),                           # 12 rows but 11 distinct trips
        ("R035", 21, 23, 25, {"trip:T980582": 25}),
    ],
)
def test_day_audit_matches_expected(investigator, rider, day, as_of, expected_owed, expected_units):
    audit = investigator.audit_day(rider, SEP(day), SEP(as_of))
    assert audit.status == "ok"
    assert audit.owed == expected_owed
    if expected_units is not None:
        assert owed_units(audit) == expected_units


def test_day_audit_facts_for_replies(investigator):
    audit = investigator.audit_day("R011", SEP(19), SEP(22))
    assert audit.completed_trips == 10
    assert audit.incentive_expected == 0

    audit = investigator.audit_day("R009", SEP(17), SEP(23))
    assert audit.completed_trips == 11  # duplicate export row must not count

    audit = investigator.audit_day("R016", SEP(19), SEP(22))
    assert len([f for f in audit.findings if f.kind == "trip_fare" and f.paid == 0 and f.owed > 0]) == 5


@pytest.mark.parametrize(
    "rider, trip_id, as_of, status, owed",
    [
        ("R003", "T926334", 22, "ok", 25),
        ("R008", "T252921", 22, "ok", 0),          # surge was 1.0, paid correctly
        ("R007", "T840677", 23, "ok", 34),
        ("R031", "T312538", 23, "ok", 22),
        ("R026", "T672899", 23, "ok", 32),         # 7.4 km paid as ₹25
        ("R035", "T980582", 23, "ok", 25),         # rider claimed ₹300; owed is ₹25
        ("R036", "T990831", 23, "ok", 0),          # distance dispute, data is consistent
        ("R028", "T849302", 23, "ok", 0),          # rider cancellation, penalty correct
        ("R021", "T482410", 22, "not_owned", 0),   # belongs to R030
        ("R021", "T000001", 22, "not_found", 0),
        ("R022", "T502951", 23, "out_of_window", 22),  # 10 days old: shortfall reported, not paid
    ],
)
def test_check_trip(investigator, rider, trip_id, as_of, status, owed):
    check = investigator.check_trip(rider, trip_id, SEP(as_of))
    assert check.status == status
    assert check.owed == owed


def test_two_unpaid_trips(investigator):
    total = sum(investigator.check_trip("R033", t, SEP(23)).owed for t in ["T795007", "T206956"])
    assert total == 176


def test_window_boundaries(investigator):
    assert investigator.in_window(SEP(16), SEP(23))       # 7 days old: still in
    assert not investigator.in_window(SEP(15), SEP(23))   # 8 days old: out
    assert not investigator.in_window(SEP(24), SEP(23))   # future date: out


def test_overpayment_is_never_clawed_back(investigator):
    audit = investigator.audit_day("R014", SEP(18), SEP(22))
    assert all(f.owed >= 0 for f in audit.findings)
