"""Safety properties, tested against the real mock API (in-process) with a fake decider.

The decider is replaced by fixed outcomes so these tests say nothing about model
quality — only about what the code does with each outcome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mock_api import server
from src.decider import INJECTION, UNCERTAIN, Decision
from src.triage_skill import (
    Ledger,
    ProposedAction,
    TriageClient,
    WriteScopeError,
    execute,
    plan_actions,
    triage_inbox,
)

EXPECTED = json.loads((Path(__file__).resolve().parent.parent / "evals" / "expected.json").read_text())


def fake_decider(email: dict) -> Decision:
    outcome = EXPECTED[email["id"]]
    top = outcome if outcome not in (UNCERTAIN, INJECTION) else "spam"
    return Decision(outcome, top, {top: 1.0}, 1.0 if outcome == INJECTION else 0.0, "fixed for the test")


class RecordingClient(TriageClient):
    """A TriageClient wired to the in-process mock API that records every request."""

    def __init__(self, read_token, write_token=None):
        super().__init__("http://testserver", read_token, write_token)
        self._http = TestClient(server.app)
        self.requests: list[tuple[str, str]] = []
        original = self._http.request

        def recording(method, url, **kwargs):
            self.requests.append((method, str(url)))
            return original(method, url, **kwargs)

        self._http.request = recording


@pytest.fixture(autouse=True)
def clean_server():
    for store in (server._sent_mail, server._alerts, server._leads):
        store.clear()


def audit() -> dict:
    return server.audit()


def approve_all(email, action):
    return True


def reject_all(email, action):
    return False


def run(approver, *, ledger=None, built=None):
    built = built if built is not None else []

    def factory():
        client = RecordingClient(server.READ_TOKEN, server.WRITE_TOKEN)
        built.append(client)
        return client

    reader = RecordingClient(server.READ_TOKEN)
    results = triage_inbox(reader, approver, fake_decider, write_client_factory=factory, ledger=ledger)
    return reader, results, built


def test_nothing_is_written_without_approval():
    _, results, built = run(reject_all)
    assert audit() == {"sent_mail": [], "alerts": [], "leads": []}
    assert built == [], "the write client must not even be created when nothing is approved"
    assert sum(len(r.actions) for r in results) > 0, "actions were proposed, just not executed"


def test_execute_does_not_touch_the_client_unless_approved():
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("client used without approval")

    action = ProposedAction("send_reply", {"to": "a@b.c", "subject": "s", "body": "b"})
    assert execute(action, Exploding(), approved=False) is None


def test_reader_never_holds_the_write_token():
    reader, _, _ = run(approve_all)
    assert not reader.can_write
    assert [method for method, _ in reader.requests] == ["GET"]
    with pytest.raises(WriteScopeError):
        reader.send_reply(to="a@b.c", subject="s", body="b")


def test_mock_api_rejects_a_write_with_the_read_token():
    forged = RecordingClient(server.READ_TOKEN, write_token=server.READ_TOKEN)
    # The in-process test client raises its own HTTP error type, so check the status only.
    with pytest.raises(Exception) as error:
        forged.send_alert(channel="#engineering", message="x")
    assert error.value.response.status_code == 403
    assert audit()["alerts"] == []


@pytest.mark.parametrize("email_id", ["e-004", "e-007", "e-008"])
def test_spam_injection_and_uncertain_plan_no_action(email_id):
    _, results, _ = run(approve_all)
    result = next(r for r in results if r.email_id == email_id)
    assert result.actions == [] and result.executed == []


def test_no_write_mentions_the_attacker_or_the_spammer():
    run(approve_all)
    written = json.dumps(audit())
    assert "reach-leads-pro.com" not in written
    assert "lucky-rewards-intl.biz" not in written


def test_routing_table_when_everything_is_approved():
    run(approve_all)
    state = audit()
    assert sorted(m["in_reply_to"] for m in state["sent_mail"]) == ["e-001", "e-003", "e-005"]
    assert len(state["alerts"]) == 2 and all(a["channel"] == "#engineering" for a in state["alerts"])
    assert [lead["email"] for lead in state["leads"]] == ["priya.n@northwind-logistics.com"]


def test_running_twice_does_not_repeat_actions(tmp_path):
    path = tmp_path / "ledger.json"
    run(approve_all, ledger=Ledger(path))
    first = audit()
    run(approve_all, ledger=Ledger(path))   # a new process would reload the file
    assert audit() == first


def test_approver_can_edit_the_text_before_sending():
    def edit_replies(email, action):
        if action.kind == "send_reply":
            return ProposedAction(action.kind, {**action.payload, "body": "edited by a human"})
        return True

    run(edit_replies)
    assert {m["body"] for m in audit()["sent_mail"]} == {"edited by a human"}


def test_unknown_label_plans_nothing():
    assert plan_actions("something_else", {"id": "x", "from": "a@b.c", "subject": "s", "body": "b"}) == []
