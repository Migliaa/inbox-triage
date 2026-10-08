"""The triage of one email as a LangGraph graph, with a pause for the approval.

    fetch -> decide -> draft -> approval -> execute
                  \\-> hold (uncertain, injection, spam: a person reviews, nothing is planned)

Same building blocks as the service (decider, routing table, drafter, clients); what
changes is who keeps the state while a person decides. Here it is the checkpointer: the
run stops at `approval`, its state is saved, and it resumes when the verdicts arrive,
even after the process restarted (DECISIONS.md, D13).

Run it in LangGraph Studio with `langgraph dev`; input `{"email_id": "e-003"}`, resume
with `{"send_reply": true, "create_lead": false}`. A string instead of `true` approves
with that text.
"""

from __future__ import annotations

import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

load_dotenv()

from src.decider import INJECTION, LABELS, Decision, decide  # noqa: E402
from src.drafter import DraftError, llm_reply  # noqa: E402
from src.triage_skill import (  # noqa: E402
    Ledger, ProposedAction, TriageClient, execute, plan_actions, template_reply,
)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8099")
READ_TOKEN = os.environ.get("READ_TOKEN", "read-token-dev")
TEXT_FIELD = {"send_reply": "body", "send_alert": "message", "create_lead": "summary"}


class TriageInput(TypedDict):
    """What a run starts from: the id of an inbox email, e.g. "e-003"."""

    email_id: str


class TriageState(TypedDict, total=False):
    email_id: str
    email: dict
    outcome: str                # one of LABELS, "uncertain" or "injection"
    reason: str
    probabilities: dict
    injection_probability: float
    actions: list[dict]         # proposed, not executed
    verdicts: dict              # action kind -> True, False, or an edited text
    executed: list[dict]        # receipts from the client API
    skipped: list[str]          # keys rejected or already done


def _read_client() -> TriageClient:
    return TriageClient(API_BASE_URL, READ_TOKEN)


def _write_client() -> TriageClient:
    return TriageClient(API_BASE_URL, READ_TOKEN, os.environ.get("WRITE_TOKEN", "write-token-dev"))


def _draft(email: dict, label: str) -> str:
    try:
        return llm_reply(email, label)
    except DraftError:
        return template_reply(email, label)


def build_graph(
    *,
    decider: Callable[[dict], Decision] = decide,
    drafter: Callable[[dict, str], str] = _draft,
    read_client: Callable[[], TriageClient] = _read_client,
    write_client: Callable[[], TriageClient] = _write_client,
    ledger: Ledger | None = None,
    checkpointer=None,
):
    ledger = ledger if ledger is not None else Ledger(Path(os.environ.get("LEDGER_PATH", "ledger.json")))

    def fetch(state: TriageState) -> dict:
        email_id = (state.get("email_id") or "").strip()
        inbox = read_client().get_inbox()
        email = next((e for e in inbox if e["id"] == email_id), None)
        if email is None:
            known = ", ".join(e["id"] for e in inbox)
            raise ValueError(f"email_id is {email_id!r}: give one of {known}")
        return {"email": email}

    def decide_node(state: TriageState) -> dict:
        decision = decider(state["email"])
        return {
            "outcome": decision.outcome,
            "reason": decision.reason,
            "probabilities": decision.probabilities,
            "injection_probability": decision.injection_probability,
        }

    def next_step(state: TriageState) -> str:
        acting = state["outcome"] in LABELS and state["outcome"] != "spam"
        return "draft" if acting else "hold"

    def hold(state: TriageState) -> dict:
        # Nothing is planned. The email stays visible with its outcome for a person to review.
        return {"actions": []}

    def draft(state: TriageState) -> dict:
        actions = plan_actions(state["outcome"], state["email"], drafter)
        return {"actions": [asdict(action) for action in actions]}

    def approval(state: TriageState) -> dict:
        # On resume LangGraph runs this node again from the top, so it must do nothing
        # but ask: the slow draft and the writes live in the nodes before and after.
        pending = [a for a in state.get("actions", []) if a["key"] not in ledger]
        if not pending:
            return {"verdicts": {}}
        verdicts = interrupt({
            "email": state["email"],
            "outcome": state["outcome"],
            "reason": state["reason"],
            "approve_or_reject": pending,
            "how": "resume with {kind: true | false | \"edited text\"}; anything missing is rejected",
        })
        return {"verdicts": verdicts if isinstance(verdicts, dict) else {}}

    def execute_node(state: TriageState) -> dict:
        executed, skipped = [], []
        for raw in state.get("actions", []):
            action = ProposedAction(**raw)
            verdict = state.get("verdicts", {}).get(action.kind, False)
            if action.key in ledger or verdict is False or verdict is None:
                skipped.append(action.key)
                continue
            if isinstance(verdict, str):
                action = replace(action, payload={**action.payload, TEXT_FIELD[action.kind]: verdict})
            elif verdict is not True:
                skipped.append(action.key)
                continue
            # The write token appears here, after an approval, and nowhere before.
            executed.append(execute(action, write_client(), approved=True))
            ledger.add(action.key)
        return {"executed": executed, "skipped": skipped}

    builder = StateGraph(TriageState, input_schema=TriageInput)
    builder.add_node("fetch", fetch)
    builder.add_node("decide", decide_node)
    builder.add_node("hold", hold)
    builder.add_node("draft", draft)
    builder.add_node("approval", approval)
    builder.add_node("execute", execute_node)
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "decide")
    builder.add_conditional_edges("decide", next_step, {"draft": "draft", "hold": "hold"})
    builder.add_edge("hold", END)
    builder.add_edge("draft", "approval")
    builder.add_edge("approval", "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer)


# Entry point for `langgraph dev` (langgraph.json). The server brings its own checkpointer.
graph = build_graph()
