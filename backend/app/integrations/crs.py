"""Thin client for the CRS Credit API sandbox.

Sandbox only, with CRS-provided test identities. Getting started:
https://calhacks.crscreditapi.com — the Postman collection from the invite email
has the full request/response shapes for each product.
"""

import httpx

from app.config import settings

CREDIT_REPORT_PATH = "/experian/credit-profile/credit-report/standard/exp-prequal-vantage4"


class CRSClient:
    def __init__(self, username: str | None = None, password: str | None = None,
                 base_url: str | None = None, http: httpx.Client | None = None) -> None:
        self._username = username or settings.crs_username
        self._password = password or settings.crs_password
        self._http = http or httpx.Client(base_url=base_url or settings.crs_base_url, timeout=30)
        self._token: str | None = None

    def login(self) -> str:
        resp = self._http.post("/users/login", json={
            "username": self._username,
            "password": self._password,
        })
        resp.raise_for_status()
        body = resp.json()
        # TODO: confirm the token field name against the Postman collection.
        token = body.get("token") or body.get("accessToken") or body.get("access_token")
        if not token:
            raise RuntimeError(f"CRS login response had no token field: {list(body)}")
        self._token = token
        return token

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            self.login()
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def credit_report(self, identity: dict) -> dict:
        """Pull a credit report for a sandbox test identity.

        `identity` follows the CRS request shape: firstName, middleName, lastName,
        suffix, birthDate, ssn, addresses[...]. Use sandbox test identities only.
        """
        resp = self._http.post(CREDIT_REPORT_PATH, json=identity, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()
