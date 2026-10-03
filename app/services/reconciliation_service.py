from app.clients.payswift import (
    PaySwiftClient,
    PaySwiftError,
)


class ReconciliationService:
    """
    PaySwift is the source of truth for adjustment payouts
    already sent to riders.

    Historical/base payout information still comes from
    payout_lines.csv.
    """

    def __init__(
        self,
        payswift_client: PaySwiftClient | None = None,
    ):
        self.payswift = (
            payswift_client
            if payswift_client is not None
            else PaySwiftClient()
        )

    def get_adjustments_paid(
        self,
        rider_id: str,
        reference_key: str,
    ) -> int:
        """
        Returns the total amount already paid through PaySwift
        for this logical dispute reference.
        """

        payouts = self.payswift.list_payouts(
            rider_id=rider_id,
        )

        expected_reference = (
            f"adjustment:{reference_key}"
        )

        total = 0

        for payout in payouts:
            if (
                payout.get("reference")
                != expected_reference
            ):
                continue

            try:
                amount = int(
                    payout.get("amount", 0)
                )
            except (TypeError, ValueError):
                continue

            total += amount

        return total

    def calculate_remaining_owed(
        self,
        rider_id: str,
        reference_key: str,
        gross_owed: int,
    ) -> dict:
        """
        gross_owed:
            What deterministic QuickDrop policy says is owed.

        already_paid:
            What PaySwift says has already been issued as an
            adjustment for this logical dispute.

        remaining_owed:
            Maximum additional amount we may send.
        """

        already_paid = self.get_adjustments_paid(
            rider_id=rider_id,
            reference_key=reference_key,
        )

        remaining_owed = max(
            gross_owed - already_paid,
            0,
        )

        return {
            "gross_owed": gross_owed,
            "already_paid": already_paid,
            "remaining_owed": remaining_owed,
        }