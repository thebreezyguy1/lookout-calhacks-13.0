"""Thin read-only client for Capital One's Nessie hackathon API.

Docs: http://api.nessieisreal.com/ — the API key is passed as the `key` query parameter.
This client only reads. Creating demo purchases belongs in the scenario injector.
"""

import httpx

from app.config import settings


class NessieClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 http: httpx.Client | None = None) -> None:
        self._key = api_key or settings.nessie_api_key
        self._http = http or httpx.Client(base_url=base_url or settings.nessie_base_url, timeout=10)

    def _get(self, path: str) -> list | dict:
        resp = self._http.get(path, params={"key": self._key})
        resp.raise_for_status()
        return resp.json()

    def customers(self) -> list[dict]:
        return self._get("/customers")

    def accounts(self, customer_id: str) -> list[dict]:
        return self._get(f"/customers/{customer_id}/accounts")

    def purchases(self, account_id: str) -> list[dict]:
        return self._get(f"/accounts/{account_id}/purchases")

    def transfers(self, account_id: str) -> list[dict]:
        return self._get(f"/accounts/{account_id}/transfers")

    def withdrawals(self, account_id: str) -> list[dict]:
        return self._get(f"/accounts/{account_id}/withdrawals")

    def merchant(self, merchant_id: str) -> dict:
        return self._get(f"/merchants/{merchant_id}")
