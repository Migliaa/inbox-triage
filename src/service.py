"""HTTP service: the decision lab and the approval gate, on top of the triage skill.

    python -m uvicorn src.service:app --port 8000      # then open http://127.0.0.1:8000

Two things live here:

* The lab (`/api/decide`, `/api/route`): any text in, probabilities out, and the
  outcome at whatever threshold the slider is on. It never plans or writes anything.
* The approval gate (`/api/emails/...`): actions are proposed only for emails read
  from the inbox, at the configured threshold. The browser sends a verdict and, at
  most, an edited text; recipient and action kind are rebuilt here from the inbox,
  so they cannot be changed from the page.

Least privilege (DECISIONS.md, D6) holds here too: every read goes through a client
that has the read token only, and the write client is built inside `approve`.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from langsmith import traceable, tracing_context
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()  # before importing the decider, which reads its settings at import time

from src.decider import (  # noqa: E402
    INJECTION, INJECTION_THRESHOLD, LABELS, THRESHOLD, UNCERTAIN, Decision, ask_model, route,
)
from src.drafter import MODEL as DRAFT_MODEL, DraftError, llm_reply  # noqa: E402
from src.triage_skill import (  # noqa: E402
    ROUTING, Ledger, ProposedAction, TriageClient, execute, plan_actions, template_reply,
)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8099")
READ_TOKEN = os.environ.get("READ_TOKEN", "read-token-dev")
ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "lab.html"
PROBES = ROOT / "evals" / "probes.json"

# The one field of each action a human may rewrite before approving.
TEXT_FIELD = {"send_reply": "body", "send_alert": "message", "create_lead": "summary"}

ledger = Ledger(Path(os.environ.get("LEDGER_PATH", "ledger.json")))
_rejected: set[str] = set()
_receipts: dict[str, dict] = {}
# Reply drafts written by the model, by action key. Until one exists the template stands in.
_drafts: dict[str, str] = {}
_draft_lock = threading.Lock()

# Outcomes that plan nothing. They are not discarded: a person sees them and can give
# the email a label, which then plans actions like any other (DECISIONS.md, D12).
REVIEWABLE = (UNCERTAIN, INJECTION, "spam")
_human_labels: dict[str, str] = {}
# Emails the model flagged as injections: the drafting model never reads them.
_flagged: set[str] = set()
# Writes that timed out: they may or may not have reached the client system.
_unconfirmed: set[str] = set()

_model_lock = threading.Lock()
_answers: dict[tuple[str, str, str], tuple[dict[str, float], float]] = {}
_model_state = {"status": "loading"}


@lru_cache(maxsize=1)
def read_client() -> TriageClient:
    return TriageClient(API_BASE_URL, READ_TOKEN)


def write_client() -> TriageClient:
    """Built on demand, inside the approval step: nothing else holds the write token."""
    return TriageClient(API_BASE_URL, READ_TOKEN, os.environ.get("WRITE_TOKEN", "write-token-dev"))


def answers_for(email: dict) -> tuple[dict[str, float], float]:
    """Model answers for an email, computed once per distinct text."""
    key = (email.get("from", ""), email.get("subject", ""), email.get("body", ""))
    with _model_lock:
        if key not in _answers:
            try:
                with _trace_thread(email.get("id", "free-text")):
                    _answers[key] = ask_model(email)
            except Exception as error:  # no outcome means no action: the email waits
                raise HTTPException(503, f"the decision model failed: {error}") from error
    return _answers[key]


def _trace_thread(email_id: str):
    """Groups the runs about one email (decision, draft, verdicts) in the trace store."""
    return tracing_context(metadata={"thread_id": email_id})


def _warm_up() -> None:
    try:
        with tracing_context(enabled=False):
            answers_for({"subject": "warm-up", "body": "warm-up"})
        _model_state["status"] = "ready"
    except Exception as error:  # shown on the page instead of a silent hang
        _model_state["status"] = f"error: {error}"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Loading the model takes a while; the page opens meanwhile and shows the state.
    threading.Thread(target=_warm_up, daemon=True).start()
    yield


app = FastAPI(
    title="Inbox Triage — Decision Lab",
    version="0.5.0",
    description="Probabilities for any email, the outcome at a given threshold, and the approval gate.",
    lifespan=lifespan,
)


class EmailIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sender: str = Field("someone@example.com", alias="from")
    subject: str = ""
    body: str = ""

    def as_email(self) -> dict:
        return {"from": self.sender, "subject": self.subject, "body": self.body}


class RouteIn(BaseModel):
    probabilities: dict[str, float]
    injection_probability: float
    threshold: float = Field(THRESHOLD, ge=0, le=1)


class Edit(BaseModel):
    text: str | None = Field(None, description="Replaces the reply body, alert message or lead summary.")


def _decision_json(decision: Decision) -> dict:
    return {
        "outcome": decision.outcome,
        "top_label": decision.top_label,
        "reason": decision.reason,
        "probabilities": decision.probabilities,
        "injection_probability": decision.injection_probability,
        "would_plan": ROUTING.get(decision.outcome, []),
    }


def _status(key: str) -> str:
    if key in ledger:
        return "executed"
    if key in _unconfirmed:
        return "unconfirmed"
    return "rejected" if key in _rejected else "pending"


def _action_json(action: ProposedAction) -> dict:
    return {
        "key": action.key,
        "kind": action.kind,
        "payload": action.payload,
        "rationale": action.rationale,
        "text_field": TEXT_FIELD[action.kind],
        "draft": _draft_state(action),
        "status": _status(action.key),
        "receipt": _receipts.get(action.key),
    }


def _draft_state(action: ProposedAction) -> str | None:
    if action.kind != "send_reply":
        return None
    if action.payload.get("in_reply_to") in _flagged:
        return "blocked"
    return "model" if action.key in _drafts else "template"


def _inbox() -> list[dict]:
    try:
        return read_client().get_inbox()
    except httpx.HTTPError as error:
        raise HTTPException(502, f"the mock API at {API_BASE_URL} did not answer: {error}") from error


def _inbox_email(email_id: str) -> dict:
    for email in _inbox():
        if email["id"] == email_id:
            return email
    raise HTTPException(404, f"no email {email_id!r} in the inbox")


@traceable(name="human_verdict")
def _record_verdict(email_id: str, kind: str, verdict: str, edited: bool = False, receipt: dict | None = None) -> dict:
    """What a person decided about one action: kept as a run of its own in the trace store."""
    return {"email_id": email_id, "kind": kind, "verdict": verdict, "edited": edited, "receipt": receipt}


def _proposed(email: dict) -> tuple[Decision, str, list[ProposedAction]]:
    """The model's decision, the label in force, and the actions that label implies.

    The label in force is the model's outcome, unless that outcome plans nothing and a
    person gave the email a label.
    """
    decision = route(*answers_for(email))
    if decision.outcome == INJECTION:
        _flagged.add(email["id"])
    label = decision.outcome
    if label in REVIEWABLE:
        label = _human_labels.get(email["id"], label)
    actions = plan_actions(label, email, _draft_or_template) if label in LABELS else []
    return decision, label, actions


def _draft_or_template(email: dict, label: str) -> str:
    """The model's draft if it was already written, the template otherwise. Never calls the model."""
    return _drafts.get(f"{email['id']}:send_reply") or template_reply(email, label)


def _pending_action(email_id: str, kind: str) -> ProposedAction:
    _, _, actions = _proposed(_inbox_email(email_id))
    action = next((a for a in actions if a.kind == kind), None)
    if action is None:
        raise HTTPException(404, f"{kind!r} was not proposed for {email_id}")
    if _status(action.key) != "pending":
        raise HTTPException(409, f"{action.key} is already {_status(action.key)}")
    return action


@app.get("/", include_in_schema=False)
def page() -> FileResponse:
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/config", tags=["lab"])
def config() -> dict:
    """Thresholds in force and whether the model has finished loading."""
    return {
        "labels": LABELS,
        "threshold": THRESHOLD,
        "injection_threshold": INJECTION_THRESHOLD,
        "model": _model_state["status"],
        "draft_model": DRAFT_MODEL,
    }


@app.get("/api/probes", tags=["lab"])
def probes() -> list[dict]:
    """Ready-made emails that press on one weak point each. They have no expected outcome."""
    return json.loads(PROBES.read_text(encoding="utf-8"))


@app.post("/api/decide", tags=["lab"])
def decide(email: EmailIn) -> dict:
    """Ask the model about any text. Plans nothing and writes nothing."""
    probabilities, injection_probability = answers_for(email.as_email())
    return _decision_json(route(probabilities, injection_probability))


@app.post("/api/route", tags=["lab"])
def reroute(body: RouteIn) -> dict:
    """Outcome for the same probabilities at another threshold. No model call."""
    return _decision_json(route(body.probabilities, body.injection_probability, threshold=body.threshold))


@app.get("/api/inbox", tags=["approval"])
def inbox() -> list[dict]:
    """The inbox, read from the mock API with the read token."""
    return _inbox()


@app.get("/api/emails/{email_id}", tags=["approval"])
def email_view(email_id: str) -> dict:
    """An inbox email with its decision and the actions waiting for a verdict."""
    email = _inbox_email(email_id)
    decision, label, actions = _proposed(email)
    reviewable = decision.outcome in REVIEWABLE
    handling = {"label": label, "by": "person" if reviewable and label != decision.outcome else "model", "reviewable": reviewable}
    return {
        "email": email,
        "decision": _decision_json(decision),
        "handling": handling,
        "actions": [_action_json(a) for a in actions],
    }


class LabelIn(BaseModel):
    label: str | None = Field(None, description="One of the four labels, or null to take the person's label back.")


@app.post("/api/emails/{email_id}/label", tags=["approval"])
def set_label(email_id: str, body: LabelIn) -> dict:
    """A person labels an email the model left without actions. Every action still needs approval."""
    email = _inbox_email(email_id)
    if route(*answers_for(email)).outcome not in REVIEWABLE:
        raise HTTPException(409, "the model labelled this email: reject its actions instead")
    if body.label is None:
        _human_labels.pop(email_id, None)
    elif body.label in LABELS:
        _human_labels[email_id] = body.label
        with _trace_thread(email_id):
            _record_verdict(email_id, "label", body.label)
    else:
        raise HTTPException(422, f"label must be one of {', '.join(LABELS)}")
    return email_view(email_id)


@app.post("/api/emails/{email_id}/actions/{kind}/approve", tags=["approval"])
def approve(email_id: str, kind: str, edit: Edit | None = None) -> dict:
    """Execute one proposed action, optionally with its text rewritten."""
    action = _pending_action(email_id, kind)
    if edit is not None and edit.text is not None:
        action = replace(action, payload={**action.payload, TEXT_FIELD[kind]: edit.text})
    try:
        receipt = execute(action, write_client(), approved=True)
    except httpx.TimeoutException as error:
        # The request left and no answer came back: retrying could send it twice.
        _unconfirmed.add(action.key)
        detail = "the client system did not confirm the write: it may have happened. Check there before doing it again."
        raise HTTPException(504, detail) from error
    except httpx.HTTPError as error:
        # Refused or unreachable: nothing was written, the action stays pending.
        raise HTTPException(502, f"the client system refused the write: {error}") from error
    ledger.add(action.key)
    _receipts[action.key] = receipt
    with _trace_thread(email_id):
        _record_verdict(email_id, kind, "approved", edited=edit is not None and edit.text is not None, receipt=receipt)
    return _action_json(action)


@app.post("/api/emails/{email_id}/actions/send_reply/draft", tags=["approval"])
def draft(email_id: str) -> dict:
    """Have the drafting model write the reply. Slow on a CPU; the result is kept."""
    action = _pending_action(email_id, "send_reply")
    with _draft_lock:
        if action.key not in _drafts:
            email = _inbox_email(email_id)
            decision, label, _ = _proposed(email)
            if decision.outcome == INJECTION:
                raise HTTPException(409, "flagged as a possible injection: the drafting model does not read this email")
            try:
                with _trace_thread(email_id):
                    _drafts[action.key] = llm_reply(email, label)
            except DraftError as error:
                raise HTTPException(502, str(error)) from error
    return _action_json(_pending_action(email_id, "send_reply"))


@app.post("/api/emails/{email_id}/actions/{kind}/reject", tags=["approval"])
def reject(email_id: str, kind: str) -> dict:
    """Drop one proposed action. Nothing is written."""
    action = _pending_action(email_id, kind)
    _rejected.add(action.key)
    with _trace_thread(email_id):
        _record_verdict(email_id, kind, "rejected")
    return _action_json(action)


@app.get("/api/audit", tags=["approval"])
def audit() -> dict:
    """Everything the mock API has received so far."""
    try:
        return read_client().get_audit()
    except httpx.HTTPError as error:
        raise HTTPException(502, f"the mock API at {API_BASE_URL} did not answer: {error}") from error
