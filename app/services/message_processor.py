from datetime import date

from app.ai.schemas import Claim


class MessageProcessor:
    def __init__(
        self,
        dispute_resolver,
        payment_service,
        claim_extractor,
    ):
        self.dispute_resolver = (
            dispute_resolver
        )

        self.payment_service = (
            payment_service
        )

        self.claim_extractor = (
            claim_extractor
        )

    def process(
        self,
        rider_id: str,
        text: str,
        received_at,
        conversation_history=None,
    ):
        parsed = (
            self.claim_extractor.extract(
                rider_id=rider_id,
                text=text,
                received_at=(
                    received_at
                ),
                conversation_history=(
                    conversation_history
                ),
            )
        )

        if parsed.needs_clarification:
            return {
                "reply": (
                    parsed
                    .clarification_question
                    or (
                        "Kaunse din ya "
                        "order ka issue hai?"
                    )
                ),
                "parsed": (
                    parsed.model_dump()
                ),
                "resolutions": [],
                "payments": [],
            }

        if not parsed.claims:
            return {
                "reply": (
                    "Is payout issue ko "
                    "samajhne ke liye thoda "
                    "aur detail bata dijiye."
                ),
                "parsed": (
                    parsed.model_dump()
                ),
                "resolutions": [],
                "payments": [],
            }

        resolutions = []
        payments = []

        for claim in parsed.claims:
            resolution = (
                self._resolve_claim(
                    rider_id=(
                        rider_id
                    ),
                    claim=claim,
                )
            )

            resolutions.append(
                {
                    "claim": (
                        claim.model_dump()
                    ),
                    "resolution": (
                        resolution
                    ),
                }
            )

            if resolution is None:
                continue

            if (
                resolution.get(
                    "status"
                )
                != "valid"
            ):
                continue

            payment = (
                self.payment_service
                .handle_resolution(
                    rider_id=(
                        rider_id
                    ),
                    dispute_type=(
                        claim.type
                    ),
                    resolution=(
                        resolution
                    ),
                )
            )

            payments.append(
                payment
            )

        reply = self._build_reply(
            resolutions=resolutions,
            payments=payments,
        )

        return {
            "reply": reply,
            "parsed": (
                parsed.model_dump()
            ),
            "resolutions": (
                resolutions
            ),
            "payments": payments,
        }

    # ---------------------------------------------------------
    # Deterministic routing
    # ---------------------------------------------------------

    def _resolve_claim(
        self,
        rider_id: str,
        claim: Claim,
    ):
        if claim.type == "missing_surge":
            if not claim.trip_ids:
                return {
                    "status": (
                        "needs_info"
                    ),
                    "owed": 0,
                    "reason": (
                        "trip_id_required"
                    ),
                }

            return (
                self.dispute_resolver
                .resolve_missing_surge(
                    rider_id=(
                        rider_id
                    ),
                    trip_id=(
                        claim.trip_ids[0]
                    ),
                )
            )

        if (
            claim.type
            == "missing_trip_payment"
        ):
            if not claim.trip_ids:
                return {
                    "status": (
                        "needs_info"
                    ),
                    "owed": 0,
                    "reason": (
                        "trip_id_required"
                    ),
                }

            if len(
                claim.trip_ids
            ) == 1:
                return (
                    self.dispute_resolver
                    .resolve_missing_trip_payment(
                        rider_id=(
                            rider_id
                        ),
                        trip_id=(
                            claim
                            .trip_ids[0]
                        ),
                    )
                )

            return (
                self.dispute_resolver
                .resolve_missing_trip_payments(
                    rider_id=(
                        rider_id
                    ),
                    trip_ids=(
                        claim.trip_ids
                    ),
                )
            )

        if (
            claim.type
            == "missing_incentive"
        ):
            if not claim.date:
                return {
                    "status": (
                        "needs_info"
                    ),
                    "owed": 0,
                    "reason": (
                        "date_required"
                    ),
                }

            day = date.fromisoformat(
                claim.date
            )

            return (
                self.dispute_resolver
                .resolve_missing_incentive(
                    rider_id=(
                        rider_id
                    ),
                    day=day,
                )
            )

        if (
            claim.type
            == "duplicate_penalty"
        ):
            if not claim.date:
                return {
                    "status": (
                        "needs_info"
                    ),
                    "owed": 0,
                    "reason": (
                        "date_required"
                    ),
                }

            day = date.fromisoformat(
                claim.date
            )

            return (
                self.dispute_resolver
                .resolve_duplicate_penalty(
                    rider_id=(
                        rider_id
                    ),
                    day=day,
                )
            )

        if claim.type == "wrong_distance":
            return {
                "status": "uncertain",
                "owed": 0,
                "reason": (
                    "Distance cannot be "
                    "independently verified "
                    "from available data"
                ),
            }

        if (
            claim.type
            == "cancellation_dispute"
        ):
            return {
                "status": "uncertain",
                "owed": 0,
                "reason": (
                    "Cancellation exception "
                    "requires ops review"
                ),
            }

        return {
            "status": "uncertain",
            "owed": 0,
            "reason": (
                "Unsupported or unclear "
                "dispute"
            ),
        }

    # ---------------------------------------------------------
    # Rider-facing response
    # ---------------------------------------------------------

    def _build_reply(
        self,
        resolutions: list,
        payments: list,
    ):
        valid = []

        for item in resolutions:
            resolution = (
                item["resolution"]
            )

            if resolution is None:
                continue

            if (
                resolution.get(
                    "status"
                )
                == "valid"
            ):
                valid.append(
                    (
                        item["claim"],
                        resolution,
                    )
                )

        if not valid:
            uncertain = any(
                item["resolution"]
                and (
                    item["resolution"]
                    .get("status")
                    == "uncertain"
                )
                for item in resolutions
            )

            if uncertain:
                return (
                    "Is issue ko available "
                    "records se confirm nahi "
                    "kar pa raha hoon. Ops "
                    "team ko review ke liye "
                    "bhejna hoga."
                )

            needs_info = any(
                item["resolution"]
                and (
                    item["resolution"]
                    .get("status")
                    == "needs_info"
                )
                for item in resolutions
            )

            if needs_info:
                return (
                    "Isko check karne ke "
                    "liye order ID ya date "
                    "chahiye."
                )

            return (
                "Records check kiye. "
                "Koi payable difference "
                "nahi mila."
            )

        parts = []

        for claim, resolution in valid:
            owed = resolution.get(
                "owed",
                0,
            )

            trip_ids = claim.get(
                "trip_ids",
                [],
            )

            if trip_ids:
                reference = ", ".join(
                    trip_ids
                )

                parts.append(
                    f"{reference} ke liye "
                    f"₹{owed} ka difference "
                    "mila."
                )

            else:
                parts.append(
                    f"₹{owed} ka difference "
                    "mila."
                )

        for payment in payments:
            decision = payment.get(
                "decision"
            )

            if (
                decision
                == "REQUIRE_APPROVAL"
            ):
                parts.append(
                    f"₹{payment['amount']} "
                    "ops approval ke liye "
                    "bheja gaya hai."
                )

            elif decision == "AUTO_PAY":
                status = payment.get(
                    "status"
                )

                if status == "COMPLETED":
                    parts.append(
                        f"₹{payment['amount']} "
                        "PaySwift par process "
                        "ho gaya hai."
                    )

                elif status == "FAILED":
                    parts.append(
                        "Payment process nahi "
                        "ho paya. Isse ops "
                        "review ki zarurat hai."
                    )

                else:
                    parts.append(
                        "Payment pehle hi "
                        "process kiya ja chuka "
                        "hai."
                    )

            elif decision == "NO_ACTION":
                reconciliation = (
                    payment.get(
                        "reconciliation",
                        {},
                    )
                )

                already_paid = (
                    reconciliation.get(
                        "already_paid",
                        0,
                    )
                )

                if already_paid > 0:
                    parts.append(
                        "Is adjustment ka "
                        "payment PaySwift par "
                        "pehle hi process ho "
                        "chuka hai."
                    )

        return " ".join(parts)