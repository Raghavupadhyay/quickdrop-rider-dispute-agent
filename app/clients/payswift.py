import os

import httpx


class PaySwiftError(Exception):
    pass


class PaySwiftClient:
    def __init__(self):
        self.base_url = os.getenv(
            "PAYSWIFT_BASE_URL",
            "http://localhost:8081",
        )

    def health(self) -> dict:
        response = httpx.get(
            f"{self.base_url}/health",
            timeout=5.0,
        )

        if response.status_code != 200:
            raise PaySwiftError(
                f"PaySwift health check failed: "
                f"{response.status_code} {response.text}"
            )

        return response.json()

    def list_payouts(
        self,
        rider_id: str,
    ) -> list[dict]:
        response = httpx.get(
            f"{self.base_url}/v1/payouts",
            params={
                "rider_id": rider_id,
            },
            timeout=5.0,
        )

        if response.status_code != 200:
            raise PaySwiftError(
                f"Failed to fetch payouts: "
                f"{response.status_code} {response.text}"
            )

        body = response.json()

        return body.get("data", [])

    def create_payout(
        self,
        rider_id: str,
        amount: int,
        reference: str,
        idempotency_key: str,
    ) -> dict:
        response = httpx.post(
            f"{self.base_url}/v1/payouts",
            headers={
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
            },
            json={
                "rider_id": rider_id,
                "amount": amount,
                "reference": reference,
            },
            timeout=5.0,
        )

        if response.status_code == 201:
            return response.json()

        if response.status_code == 400:
            raise PaySwiftError(
                f"Invalid payout request: {response.text}"
            )

        if response.status_code == 409:
            raise PaySwiftError(
                f"Duplicate payout request: {response.text}"
            )

        if response.status_code == 429:
            raise PaySwiftError(
                "PaySwift rate limit exceeded"
            )

        if response.status_code >= 500:
            raise PaySwiftError(
                f"PaySwift server error: "
                f"{response.status_code} {response.text}"
            )

        raise PaySwiftError(
            f"Unexpected PaySwift response: "
            f"{response.status_code} {response.text}"
        )