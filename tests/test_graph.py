"""The LangGraph orchestration: same safety properties as the hand-written one.

Fixed decisions and the in-process mock API, as in test_safety.py. What is specific
here is the pause: the run stops at the approval, and resumes from the saved state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from mock_api import server
from src.decider import INJECTION, UNCERTAIN, Decision
from src.graph import build_graph
from src.triage_skill import Ledger, TriageClient

EXPECTED = json.loads((Path(__file__).resolve().parent.parent / "evals" / "expected.json").read_text())
EMPTY = {"sent_mail": [], "alerts": [], "leads": []}


def fake_decider(email: dict) -> Decision:
    outcome = EXPECTED[email["id"]]
    top = outcome if outcome not in (UNCERTAIN, INJECTION) else "spam"
    return Decision(outcome, top, {top: 1.0}, 1.0 if outcome == INJECTION else 0.0, "fixed for the test")


def in_process(write_token=None) -> TriageClient:
    client = TriageClient("http://testserver", server.READ_TOKEN, write_token)
    client._http = TestClient(server.app)
    return client


@pytest.fixture
def run():
    for store in (server._sent_mail, server._alerts, server._leads):
        store.clear()
    built, drafted = [], []

    def write_client():
        built.append("write")
        return in_process(server.WRITE_TOKEN)

    def drafter(email, label):
        drafted.append(email["id"])
        return f"draft for {email['id']}"

    graph = build_graph(
        decider=fake_decider, drafter=drafter, read_client=in_process, write_client=write_client,
        ledger=Ledger(), checkpointer=MemorySaver(),
    )

    def start(email_id, thread="t1"):
        config = {"configurable": {"thread_id": thread}}
        return graph.invoke({"email_id": email_id}, config), config

    start.graph, start.write_clients_built, start.drafted = graph, built, drafted
    return start


def test_the_run_stops_at_the_approval_and_nothing_is_written(run):
    state, config = run("e-003")
    [pause] = state["__interrupt__"]
    assert [a["kind"] for a in pause.value["approve_or_reject"]] == ["send_reply", "create_lead"]
    assert run.graph.get_state(config).next == ("approval",)
    assert server.audit() == EMPTY and run.write_clients_built == []


def test_resuming_with_verdicts_executes_only_what_was_approved(run):
    _, config = run("e-003")
    state = run.graph.invoke(Command(resume={"send_reply": "edited by a human", "create_lead": False}), config)
    assert [r["id"] for r in state["executed"]] == ["mail-1"] and state["skipped"] == ["e-003:create_lead"]
    audit = server.audit()
    assert audit["sent_mail"][0]["body"] == "edited by a human" and audit["leads"] == []
    assert run.drafted == ["e-003"], "resuming does not draft again"


def test_an_empty_or_malformed_resume_approves_nothing(run):
    _, config = run("e-001")
    state = run.graph.invoke(Command(resume="yes, do everything"), config)
    assert state["executed"] == [] and server.audit() == EMPTY


@pytest.mark.parametrize("email_id", ["e-004", "e-007", "e-008"])
def test_spam_injection_and_uncertain_end_without_a_pause_or_a_draft(run, email_id):
    state, config = run(email_id)
    assert "__interrupt__" not in state and state["actions"] == []
    assert run.graph.get_state(config).next == ()
    assert run.drafted == [] and run.write_clients_built == []


def test_a_second_run_on_the_same_email_does_not_repeat_the_action(run):
    _, config = run("e-002")
    run.graph.invoke(Command(resume={"send_alert": True}), config)
    state, _ = run("e-002", thread="t2")
    assert "__interrupt__" not in state and state["executed"] == []
    assert len(server.audit()["alerts"]) == 1


def test_a_run_without_a_known_email_id_says_which_ids_exist(run):
    with pytest.raises(ValueError, match="e-001"):
        run("")
