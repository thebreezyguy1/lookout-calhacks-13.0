import httpx
import pytest

from app.integrations.crs import CRSClient, CRSError, load_personas, persona_credit_body


def _client(handler) -> CRSClient:
    http = httpx.Client(base_url="https://crs.test/api", transport=httpx.MockTransport(handler))
    return CRSClient(username="u", password="p", http=http)


def test_personas_fixture_is_complete() -> None:
    personas = load_personas()
    assert len(personas) == 20
    for persona in personas.values():
        assert set(persona["credit_report"]) == {"transunion", "equifax", "experian"}
        assert persona["fraud_finder"]["body"]["email"]


def test_error_in_200_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"token": "t"})
        return httpx.Response(200, json={"errorMessages": [
            {"creditErrorMessageCode": "E1200", "creditErrorMessageText": "deceased file"}
        ]})

    with pytest.raises(CRSError) as exc:
        _client(handler).credit_report(persona_credit_body("P14"))
    assert exc.value.codes == ["E1200"]


def test_success_returns_body_and_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"token": "t"})
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json={"errorMessages": None, "tradelines": []},
                              headers={"RequestID": "abc-123"})

    result = _client(handler).credit_report(persona_credit_body("P20"))
    assert result.request_id == "abc-123"
    assert result.body["tradelines"] == []
