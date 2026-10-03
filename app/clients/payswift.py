import os
import time

import httpx


class PaySwiftError(Exception):
    """PaySwift could not be used.

    maybe_processed: the payout may have gone through anyway (timeouts, 5xx,
                     in-progress), so the caller must check the ledger instead
                     of assuming failure.
    in_progress:     PaySwift is still working on a request with this
                     Idempotency-Key; the result will appear in the ledger.
    timed_out:       we stopped waiting; PaySwift may still finish it.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        maybe_processed: bool = False,
        in_progress: bool = False,
        timed_out: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.maybe_processed = maybe_processed
        self.in_progress = in_progress
        self.timed_out = timed_out

    @property
    def definitive(self) -> bool:
        """True when PaySwift certainly did not and will not pay (e.g. 400)."""
        return not self.maybe_processed


class PaySwiftClient:
    """Thin client for the PaySwift Payouts API.

    The sandbox behaves like production: ~20% of calls fail instantly with a
    5xx (nothing happened), ~10% take about 8 seconds and then succeed, and a
    retried Idempotency-Key answers 200 with the original payout, or 409
    request_in_progress while the first attempt is still running. So:
      * every payout carries an Idempotency-Key;
      * instant 5xx/429 are retried here, with the same key;
      * timeouts and in-progress answers are reported, not retried blindly:
        the payment service decides how long it can keep waiting.
    """

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 4.0,
        attempts: int = 5,
        backoff: float = 0.15,
    ):
        self.base_url = (base_url or os.getenv("PAYSWIFT_BASE_URL", "http://localhost:8081")).rstrip("/")
        self.timeout = timeout
        self.attempts = attempts
        self.backoff = backoff

    # ------------------------------------------------------------------ helpers

    def _request(self, method: str, path: str, timeout: float | None = None, **kwargs) -> httpx.Response:
        timeout = self.timeout if timeout is None else timeout
        last_response: httpx.Response | None = None

        for attempt in range(self.attempts):
            try:
                response = httpx.request(method, f"{self.base_url}{path}", timeout=timeout, **kwargs)
            except httpx.TimeoutException as exc:
                raise PaySwiftError(
                    f"PaySwift {method} {path} timed out after {timeout:.1f}s",
                    maybe_processed=(method == "POST"),
                    timed_out=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise PaySwiftError(
                    f"PaySwift {method} {path} unreachable: {exc}",
                    maybe_processed=(method == "POST"),
                ) from exc

            if response.status_code not in self.RETRY_STATUSES:
                return response

            last_response = response
            if attempt < self.attempts - 1:
                time.sleep(self.backoff * (attempt + 1))

        assert last_response is not None
        raise PaySwiftError(
            f"PaySwift {method} {path} failed after {self.attempts} attempts: "
            f"{last_response.status_code} {last_response.text}",
            status_code=last_response.status_code,
            # A 504 can mean the request was processed; a 503 means it was not,
            # but we only trust the ledger.
            maybe_processed=(method == "POST"),
        )

    # ---------------------------------------------------------------- endpoints

    def health(self) -> dict:
        response = self._request("GET", "/health")

        if response.status_code != 200:
            raise PaySwiftError(
                f"PaySwift health check failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    def list_payouts(self, rider_id: str) -> list[dict]:
        response = self._request("GET", "/v1/payouts", params={"rider_id": rider_id})

        if response.status_code != 200:
            raise PaySwiftError(
                f"Failed to fetch payouts: {response.status_code} {response.text}",
                status_code=response.status_code,
            )

        return response.json().get("data", [])

    def create_payout(
        self,
        rider_id: str,
        amount: int,
        reference: str,
        idempotency_key: str,
        timeout: float | None = None,
    ) -> dict:
        response = self._request(
            "POST",
            "/v1/payouts",
            timeout=timeout,
            headers={
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
            },
            json={
                "rider_id": rider_id,
                "amount": int(amount),
                "reference": reference,
            },
        )

        # 201: created now. 200: same Idempotency-Key, the original payout.
        if response.status_code in (200, 201):
            return response.json()

        if response.status_code == 409:
            body = response.text
            if "request_in_progress" in body:
                raise PaySwiftError(
                    f"PaySwift is still processing this payout: {body}",
                    status_code=409,
                    maybe_processed=True,
                    in_progress=True,
                )
            raise PaySwiftError(
                f"Idempotency-Key reused with a different body: {body}",
                status_code=409,
                maybe_processed=True,
            )

        raise PaySwiftError(
            f"PaySwift rejected the payout: {response.status_code} {response.text}",
            status_code=response.status_code,
        )
