"""Rider-facing sentences, in Hinglish, built from facts the tools found.

Replies are templated on purpose: every number a rider is told comes from a
finding, never from the model, so the reply can't promise money that the
policy did not compute.
"""

from datetime import date

from app.domain.policy import AUTO_PAY_LIMIT, DAILY_INCENTIVE_THRESHOLD, DISPUTE_WINDOW_DAYS
from app.services.investigator import DayAudit, Finding, TripCheck
from app.services.payment_service import PaymentOutcome


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEFAULT_CLARIFICATION = (
    "Kaunse din ya kaunse order ka payout galat laga? Date ya order ID bata dijiye."
)
SUSPICIOUS = (
    "Main sirf is number se jude account ke baare mein madad kar sakta hoon. "
    "Aapke account ki koi specific problem ho to date ya order ID bataiye."
)
OTHER = "Payout se judi koi problem ho to date ya order ID bataiye, main check karta hoon."
SAFE_FALLBACK = (
    "Aapka message mil gaya hai. Abhi check karne mein dikkat aa rahi hai, "
    "ops team ko bhej diya hai; jald hi jawab milega."
)
HOLDING = "Aapka message mil gaya hai, check kar raha hoon."


def fmt_day(day) -> str:
    if isinstance(day, str):
        day = date.fromisoformat(day[:10])
    return f"{day.day} {MONTHS[day.month - 1]}"


def rs(amount: int) -> str:
    return f"₹{int(amount)}"


def _surge(trip: dict) -> str:
    return f"{trip['surge_multiplier']:g}x"


def _fare_sentence(f: Finding, claim_type: str) -> str:
    trip = f.facts
    trip_id, day = trip["trip_id"], fmt_day(trip["day"])
    if f.owed > 0:
        if f.paid == 0:
            return f"Order {trip_id} ({day}) ka payment nahi mila tha: {rs(f.expected)} banta hai."
        if trip["surge_multiplier"] > 1:
            return (
                f"Order {trip_id} ({day}) pe {_surge(trip)} surge laga tha: "
                f"{rs(f.expected)} banta tha, {rs(f.paid)} mila. Farak {rs(f.owed)}."
            )
        return (
            f"Order {trip_id} ({day}, {trip['distance_km']:g} km): "
            f"{rs(f.expected)} banta tha, {rs(f.paid)} mila. Farak {rs(f.owed)}."
        )
    # nothing short
    if claim_type == "missing_surge" and trip["surge_multiplier"] <= 1:
        return (
            f"Order {trip_id} ({day}) pe surge nahi tha ({_surge(trip)}): "
            f"{trip['distance_km']:g} km ke {rs(f.expected)} bante hain aur {rs(f.paid)} hi mile. Koi farak nahi."
        )
    if f.paid > f.expected:
        return f"Order {trip_id} ({day}): {rs(f.expected)} banta tha aur {rs(f.paid)} mila, yani zyada hi mila hai."
    return (
        f"Order {trip_id} ({day}, {trip['distance_km']:g} km, surge {_surge(trip)}): "
        f"{rs(f.expected)} banta hai aur {rs(f.paid)} hi mila. Koi farak nahi."
    )


def _penalty_sentence(f: Finding) -> str:
    trip = f.facts
    trip_id, day = trip.get("trip_id"), fmt_day(trip["day"])
    charged = abs(f.paid)
    if f.owed > 0:
        times = trip.get("penalties_charged") or (charged // 10 if charged else 0)
        return (
            f"{day}: order {trip_id} ki cancel penalty {times} baar kati ({rs(charged)}), "
            f"ek cancel pe {rs(abs(f.expected))} banti thi. {rs(f.owed)} extra kata."
        )
    if f.expected < 0:
        return (
            f"Order {trip_id} ({day}) aapne cancel kiya tha; policy ke hisaab se "
            f"{rs(abs(f.expected))} penalty lagti hai aur {rs(charged)} hi kati."
        )
    return f"Order {trip_id} ({day}) pe {rs(charged)} penalty kati thi."


def trip_checked(check: TripCheck, claim_type: str) -> str:
    trip = check.trip or {}
    if trip.get("status") == "cancelled_by_customer":
        return (
            f"Order {check.trip_id} ({fmt_day(trip['day'])}) customer ne cancel kiya tha; "
            f"ispe na payment banta hai na penalty."
        )
    parts = []
    for f in check.findings:
        if f.kind == "trip_fare" and (trip.get("status") == "completed" or f.owed > 0):
            parts.append(_fare_sentence(f, claim_type))
        elif f.kind == "penalty":
            parts.append(_penalty_sentence(f))
    return " ".join(parts) or f"Order {check.trip_id} check kiya, koi farak nahi mila."


def trip_not_found(trip_id: str) -> str:
    return f"Order {trip_id} humare records mein nahi mila. Order ID dobara check karke bhejiye."


def trip_not_owned(trip_id: str) -> str:
    return (
        f"Order {trip_id} aapke account ka nahi hai, isliye main ispe kuch nahi kar sakta. "
        f"Ops team ko check karne ke liye bhej diya hai."
    )


def trip_out_of_window(check: TripCheck) -> str:
    day = fmt_day(check.trip["day"])
    base = (
        f"Order {check.trip_id} {day} ka hai, yani {check.days_old} din purana. "
        f"Hum sirf pichle {DISPUTE_WINDOW_DAYS} din ke payout disputes dekh sakte hain."
    )
    if check.owed > 0:
        base += f" Record mein {rs(check.owed)} ka farak dikh raha hai, isliye ops team ko bhej diya hai."
    else:
        base += " Ops team ko bhej diya hai."
    return base


def day_no_trips(day: date) -> str:
    return f"{fmt_day(day)} ko aapke account mein koi trip nahi mili. Date dobara check kar lijiye."


def day_out_of_window(audit: DayAudit) -> str:
    base = (
        f"{fmt_day(audit.day)} {audit.days_old} din purana hai; hum sirf pichle "
        f"{DISPUTE_WINDOW_DAYS} din ke disputes dekh sakte hain."
    )
    if audit.owed > 0:
        base += f" Record mein {rs(audit.owed)} ka farak dikh raha hai, ops team ko bhej diya hai."
    else:
        base += " Ops team ko bhej diya hai."
    return base


def day_checked(audit: DayAudit, claim_type: str, skip_units: set[str]) -> str:
    day = fmt_day(audit.day)
    parts: list[str] = []

    incentive = next(f for f in audit.findings if f.kind == "incentive")
    fares = [f for f in audit.findings if f.kind == "trip_fare" and f.owed > 0 and f.unit not in skip_units]
    penalties = [f for f in audit.findings if f.kind == "penalty" and f.owed > 0 and f.unit not in skip_units]

    if claim_type == "missing_incentive" or incentive.owed > 0:
        if incentive.owed > 0:
            parts.append(
                f"{day} ko aapke {audit.completed_trips} trips complete hue; "
                f"{rs(incentive.owed)} incentive nahi mila tha."
            )
        elif incentive.paid > 0:
            parts.append(f"{day} ka {rs(incentive.paid)} incentive aapko mil chuka hai.")
        else:
            parts.append(
                f"{day} ko aapke {audit.completed_trips} trips complete hue the. "
                f"Incentive {DAILY_INCENTIVE_THRESHOLD} trips pe milta hai, isliye nahi bana."
            )

    unpaid = [f for f in fares if f.paid == 0]
    short = [f for f in fares if f.paid > 0]
    if len(unpaid) > 1:
        ids = ", ".join(f.facts["trip_id"] for f in unpaid)
        parts.append(
            f"{day} ke {len(unpaid)} orders ({ids}) ka payment nahi mila tha: "
            f"total {rs(sum(f.owed for f in unpaid))} banta hai."
        )
    else:
        parts.extend(_fare_sentence(f, claim_type) for f in unpaid)
    parts.extend(_fare_sentence(f, claim_type) for f in short)
    parts.extend(_penalty_sentence(f) for f in penalties)

    if not parts:
        if claim_type == "duplicate_penalty":
            if audit.rider_cancellations:
                charged = -sum(f.paid for f in audit.findings if f.kind == "penalty")
                parts.append(
                    f"{day} ko aapne {audit.rider_cancellations} order cancel kiya; "
                    f"{rs(charged)} penalty policy ke hisaab se sahi hai."
                )
            else:
                parts.append(f"{day} ko aapke account mein koi cancel penalty nahi kati.")
        else:
            parts.append(
                f"{day} ke {audit.trips_checked} trips check kiye: sab ka payment policy ke "
                f"hisaab se sahi hai, koi farak nahi mila."
            )
    return " ".join(parts)


def cancellation_dispute(check: TripCheck | None) -> str:
    if check and check.trip and check.trip.get("status") == "cancelled_by_rider":
        return (
            "Policy ke hisaab se rider cancel pe ₹10 penalty lagti hai; main ise khud hata nahi sakta. "
            "Aapki wajah ke saath ops team ko bhej diya hai, woh review karegi."
        )
    return "Penalty waiver main khud nahi kar sakta; ops team ko review ke liye bhej diya hai."


def distance_dispute(check: TripCheck) -> str:
    return (
        "Distance main records se verify nahi kar sakta, isliye ops team ko check karne ke liye bhej diya hai."
    )


def payment(outcome: PaymentOutcome) -> str:
    amount = rs(outcome.amount)
    if outcome.decision == "paid":
        return f"{amount} PaySwift pe bhej diya hai."
    if outcome.decision == "approval_pending":
        if outcome.reason == "above_limit":
            return (
                f"{amount} ₹{AUTO_PAY_LIMIT} se zyada hai, isliye ops team approve karegi; "
                f"approve hote hi aa jayega."
            )
        if outcome.reason == "second_auto_pay_today":
            return (
                f"Ek din mein ek hi auto-payment ho sakta hai, isliye {amount} ops team "
                f"approve karegi; approve hote hi aa jayega."
            )
        return f"{amount} ka payment abhi verify nahi ho paya; ops team dekh rahi hai."
    if outcome.decision == "processing":
        return (
            f"{amount} ka payment PaySwift pe process ho raha hai; confirm hote hi "
            f"aapke account mein aa jayega."
        )
    if outcome.decision == "approval_exists":
        return f"{amount} pehle se ops approval ke liye pending hai; approve hote hi aa jayega."
    if outcome.decision == "already_paid":
        return f"{amount} ka payment pehle hi PaySwift pe process ho chuka hai."
    if outcome.decision == "payment_failed":
        return f"{amount} ka payment abhi process nahi ho paya; ops team ko bhej diya hai, woh dobara try karegi."
    return ""


def pushback_no_change() -> str:
    return "Dobara check kiya, record wahi hai. Agar aapko lagta hai record galat hai, maine ise ops team ko bhej diya hai."


def follow_up(summary: dict) -> str:
    parts = []
    if summary.get("pending_approvals"):
        amounts = " aur ".join(rs(a) for a in summary["pending_approvals"])
        parts.append(f"{amounts} ops approval ke liye pending hai; approve hote hi aa jayega.")
    if summary.get("processing_payments"):
        amounts = " aur ".join(rs(a) for a in summary["processing_payments"])
        parts.append(f"{amounts} ka payment PaySwift pe process ho raha hai; confirm hote hi aa jayega.")
    if summary.get("last_completed_payment"):
        parts.append(
            f"{rs(summary['last_completed_payment']['amount'])} ka payment PaySwift pe process ho gaya hai; "
            f"aam taur pe 1-2 din mein account mein aa jata hai."
        )
    if summary.get("failed_payments"):
        parts.append("Ek payment process nahi ho paya tha; ops team use dobara try kar rahi hai.")
    if summary.get("pending_escalations") and not parts:
        parts.append("Aapka issue ops team ke paas review mein hai; jald jawab milega.")
    if not parts:
        parts.append("Abhi aapka koi payment pending nahi hai. Koi aur payout problem ho to date ya order ID bataiye.")
    return " ".join(parts)
