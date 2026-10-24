"""Thin client for the CRS Credit API sandbox.

Sandbox rules (from the CRS MCP server, `npx -y @crscreditapi/crs-calhacks-mcp-server@latest`):
- Only send sandbox persona bodies (see `fixtures/crs_personas.json`). A body that
  doesn't match a persona goes to the real vendor, where it can fail or be billed.
- HTTP 200 does not mean success: always check `errorMessages` in the body.
- Identical requests are served from cache; results never change for a persona.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings

PERSONAS_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "crs_personas.json"

CREDIT_REPORT_PATHS = {
    "transunion": "/transunion/credit-report/standard/tu-prequal-fico9",
    "equifax": "/equifax/credit-report/standard/efx-prequal-vantage4",
    "experian": "/experian/credit-report/standard/exp-prequal-vantage4",
}
FRAUD_FINDER_PATH = "/fraud-finder/fraud-finder"


class CRSError(RuntimeError):
    """The bureau or vendor returned an error, including errors inside a 200 response."""

    def __init__(self, message: str, codes: list[str] | None = None) -> None:
        super().__init__(message)
        self.codes = codes or []


@dataclass
class CRSResponse:
    body: dict
    request_id: str | None  # RequestID header; needed for re-reads, PDFs and debug_request


def load_personas() -> dict:
    return json.loads(PERSONAS_PATH.read_text())


def persona_credit_body(persona_id: str, bureau: str = "experian") -> dict:
    return load_personas()[persona_id]["credit_report"][bureau]["body"]


def persona_fraud_body(persona_id: str) -> dict:
    return load_personas()[persona_id]["fraud_finder"]["body"]


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
        token = resp.json().get("token")
        if not token:
            raise CRSError("CRS login response had no 'token' field")
        self._token = token
        return token

    def _post(self, path: str, body: dict) -> CRSResponse:
        if not self._token:
            self.login()
        resp = self._http.post(path, json=body, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        })
        if resp.status_code == 401:
            raise CRSError("401 from CRS: token expired or account lacks access to this product")
        resp.raise_for_status()
        return CRSResponse(body=resp.json(), request_id=resp.headers.get("RequestID"))

    def credit_report(self, body: dict, bureau: str = "experian") -> CRSResponse:
        """Pull a soft credit report. `body` must be a persona body from the fixtures."""
        result = self._post(CREDIT_REPORT_PATHS[bureau], body)
        errors = result.body.get("errorMessages") or []
        if errors:
            codes = [e.get("creditErrorMessageCode", "?") for e in errors]
            text = "; ".join(f"{e.get('creditErrorMessageCode')}: {e.get('creditErrorMessageText')}"
                             for e in errors)
            raise CRSError(text, codes)
        return result

    def fraud_finder(self, body: dict) -> CRSResponse:
        """Email-centric fraud risk check (AtData). `risk.score` is 0-100, higher is riskier."""
        return self._post(FRAUD_FINDER_PATH, body)
