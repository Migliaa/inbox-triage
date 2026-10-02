"""The HTTP service, against the in-process mock API and with fixed model answers.

Like test_safety.py, this says nothing about model quality: it checks what the
service does with each outcome, and that the approval gate is the only way to write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mock_api import server
from src import service
from src.triage_skill import Ledger, TriageClient

EMPTY = {"sent_mail": [], "alerts": [], "leads": []}

# Probabilities taken from the recorded run in DECISIONS.md, keyed by subject.
ANSWERS = {
    "Invoice #4471 charged twice this month": ({"billing": 0.96, "bug_report": 0.01, "sales_lead": 0.02, "spam": 0.01}, 0.16),
    "Interested in a pilot for our ops team": ({"billing": 0.03, "bug_report": 0.01, "sales_lead": 0.94, "spam": 0.02}, 0.06),
    "Re: your account": ({"billing": 0.19, "bug_report": 0.07, "sales_lead": 0.38, "spam": 0.36}, 0.94),
    "Question about your platform": ({"billing": 0.80, "bug_report": 0.01, "sales_lead": 0.17, "spam": 0.01}, 0.05),
}
DEFAULT = ({"billing": 0.02, "bug_report": 0.02, "sales_lead": 0.02, "spam": 0.94}, 0.1)


def in_process(write_token=None) -> TriageClient:
    client = TriageClient("http://testserver", server.READ_TOKEN, write_token)
    client._http = TestClient(server.app)
    return client


@pytest.fixture
def api(monkeypatch):
    for store in (server._sent_mail, server._alerts, server._leads):
        store.clear()
    built = []

    def write_client():
        built.append("write")
        return in_process(server.WRITE_TOKEN)

    monkeypatch.setattr(service, "ask_model", lambda email: ANSWERS.get(email["subject"], DEFAULT))
    monkeypatch.setattr(service, "read_client", in_process)
    monkeypatch.setattr(service, "write_client", write_client)
    monkeypatch.setattr(service, "ledger", Ledger())
    monkeypatch.setattr(service, "_answers", {})
    monkeypatch.setattr(service, "_rejected", set())
    monkeypatch.setattr(service, "_receipts", {})
    client = TestClient(service.app)   # not used as a context manager: no model warm-up
    client.write_clients_built = built
    return client


def test_decide_returns_probabilities_for_free_text(api):
    decision = api.post("/api/decide", json={"subject": "Question about your platform", "body": "x"}).json()
    assert decision["outcome"] == "uncertain" and decision["top_label"] == "billing"
    assert decision["probabilities"]["billing"] == 0.80 and decision["would_plan"] == []


def test_route_moves_with_the_threshold(api):
    probabilities, injection = ANSWERS["Question about your platform"]
    body = {"probabilities": probabilities, "injection_probability": injection, "threshold": 0.75}
    assert api.post("/api/route", json=body).json()["outcome"] == "billing"


def test_looking_at_emails_writes_nothing(api):
    for email in api.get("/api/inbox").json():
        api.get(f"/api/emails/{email['id']}")
    assert api.get("/api/audit").json() == EMPTY
    assert api.write_clients_built == []


@pytest.mark.parametrize("email_id", ["e-004", "e-007", "e-008"])
def test_spam_injection_and_uncertain_have_nothing_to_approve(api, email_id):
    assert api.get(f"/api/emails/{email_id}").json()["actions"] == []
    assert api.post(f"/api/emails/{email_id}/actions/send_reply/approve").status_code == 404
    assert api.get("/api/audit").json() == EMPTY


def test_approval_executes_once(api):
    approved = api.post("/api/emails/e-001/actions/send_reply/approve").json()
    assert approved["status"] == "executed" and approved["receipt"]["id"] == "mail-1"
    assert api.post("/api/emails/e-001/actions/send_reply/approve").status_code == 409
    assert len(api.get("/api/audit").json()["sent_mail"]) == 1


def test_rejection_writes_nothing_and_is_final(api):
    assert api.post("/api/emails/e-003/actions/create_lead/reject").json()["status"] == "rejected"
    assert api.post("/api/emails/e-003/actions/create_lead/approve").status_code == 409
    assert api.get("/api/audit").json() == EMPTY
    assert api.write_clients_built == []


def test_the_page_can_edit_the_text_but_not_the_recipient(api):
    body = {"text": "edited by a human", "to": "attacker@example.com"}
    api.post("/api/emails/e-001/actions/send_reply/approve", json=body)
    [mail] = api.get("/api/audit").json()["sent_mail"]
    assert mail["body"] == "edited by a human"
    assert mail["to"] == "dana.whitfield@meridianparts.com"


def test_inbox_unreachable_is_reported(api, monkeypatch):
    monkeypatch.setattr(service, "read_client", lambda: TriageClient("http://127.0.0.1:9", "x"))
    assert api.get("/api/inbox").status_code == 502


def test_probes_are_decided_but_never_proposed(api):
    probes = api.get("/api/probes").json()
    assert len(probes) >= 5 and all(p["id"].startswith("p-") for p in probes)
    api.post("/api/decide", json={k: probes[0][k] for k in ("from", "subject", "body")})
    assert api.get(f"/api/emails/{probes[0]['id']}").status_code == 404
    assert api.write_clients_built == []
