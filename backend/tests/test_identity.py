from datetime import date

from app.agent.identity import identity_level, identity_signals

TODAY = date(2026, 10, 24)

# Shaped like real CRS sandbox responses, trimmed to the fields the signals read.
CLEAN_FRAUD = {
    "eam": {"date_first_seen": "2014-05-04"},
    "risk": {"score": 8, "postal": {"ip_postal_distance": "5 miles"},
             "phone": {"line_type": "landline", "status": "valid", "prepaid": False,
                       "owner": {"last_name_match": "match"}}},
    "email_validation": {"status": "valid"},
}


def report(*inquiry_dates: str, alerts: list[dict] | None = None, comments: list[str] | None = None) -> dict:
    return {
        "inquiries": [{"inquiryDate": d} for d in inquiry_dates],
        "responseAlertMessages": alerts or [],
        "creditFiles": [{"creditFileDetail": {"creditFileResultStatusType": "FileReturned"},
                         "comments": [{"commentText": c} for c in comments or []]}],
    }


def rules(hits) -> set[str]:
    return {h.rule for h in hits}


def test_clean_identity_is_clear() -> None:
    hits = identity_signals({"experian": report("2026-08-19")}, {}, CLEAN_FRAUD, TODAY)
    assert hits == [] and identity_level(hits)[1] == "clear"


def test_deceased_file_is_flagged() -> None:
    errors = {b: "E1200: This is a deceased file" for b in ("transunion", "equifax", "experian")}
    hits = identity_signals({}, errors, CLEAN_FRAUD, TODAY)
    assert "deceased_identity" in rules(hits) and identity_level(hits)[1] == "flag"


def test_inquiry_burst_but_not_steady_shopping() -> None:
    burst = report(*[f"2026-08-{d:02d}" for d in (19, 22, 25, 28, 31)])
    monthly = report(*[f"2026-{m:02d}-19" for m in range(5, 11)])  # six inquiries, one a month
    assert "inquiry_burst" in rules(identity_signals({"tu": burst}, {}, CLEAN_FRAUD, TODAY))
    assert "inquiry_burst" not in rules(identity_signals({"tu": monthly}, {}, CLEAN_FRAUD, TODAY))


def test_fraud_alert_from_alert_message_or_comment() -> None:
    by_alert = report(alerts=[{"responseAlertMessageCategoryType": "FACTAFraudVictimInitial"}])
    by_comment = report(comments=["INITIAL FRAUD ALERT: CONSUMER STATEMENT ON FILE."])
    for r in (by_alert, by_comment):
        assert "fraud_alert_on_file" in rules(identity_signals({"tu": r}, {}, CLEAN_FRAUD, TODAY))


def test_other_alerts_are_not_fraud_alerts() -> None:
    r = report(alerts=[{"responseAlertMessageCategoryType": "SSNVerification"}])
    assert identity_signals({"tu": r}, {}, CLEAN_FRAUD, TODAY) == []


def test_contact_details_that_belong_to_someone_else() -> None:
    fraud = {
        "eam": {"date_first_seen": "2026-10-15"},
        "risk": {"score": 88, "postal": {"ip_postal_distance": "> 1000 miles"},
                 "phone": {"line_type": "voip", "prepaid": True, "owner": {"last_name_match": "no_match"}}},
    }
    hits = identity_signals({}, {}, fraud, TODAY)
    assert {"fraud_finder_high", "phone_owner_mismatch", "disposable_phone", "new_email",
            "ip_far_from_address"} <= rules(hits)
    assert identity_level(hits)[1] == "flag"


def test_medium_fraud_score_alone_stays_clear() -> None:
    fraud = dict(CLEAN_FRAUD, risk=dict(CLEAN_FRAUD["risk"], score=52))
    assert identity_level(identity_signals({}, {}, fraud, TODAY))[1] == "clear"


def test_no_fraud_check_no_crash() -> None:
    assert identity_signals({}, {}, None, TODAY) == []
