from dataclasses import dataclass
from typing import Literal

from app.domain.policy import AUTO_PAY_LIMIT


@dataclass
class PaymentDecision:
    action: Literal["AUTO_PAY", "REQUIRE_APPROVAL", "NO_ACTION"]
    amount: int
    reason: str


def decide_payment_action(
    owed_amount: int,
    already_auto_paid_today: bool,
) -> PaymentDecision:

    if owed_amount <= 0:
        return PaymentDecision(
            action="NO_ACTION",
            amount=0,
            reason="No money is owed",
        )

    if owed_amount > AUTO_PAY_LIMIT:
        return PaymentDecision(
            action="REQUIRE_APPROVAL",
            amount=owed_amount,
            reason="Amount exceeds auto-pay limit",
        )

    if already_auto_paid_today:
        return PaymentDecision(
            action="REQUIRE_APPROVAL",
            amount=owed_amount,
            reason="Rider already received an auto-payment today",
        )

    return PaymentDecision(
        action="AUTO_PAY",
        amount=owed_amount,
        reason="Eligible for automatic payout",
    )