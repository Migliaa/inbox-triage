"""Inbox Triage skill worker.

Flow: read the inbox -> decide a label -> plan actions -> ask a human -> execute.

Two properties hold by construction (DECISIONS.md, D5 and D6):

* Nothing is written without approval: `execute` is the only function that calls a
  write endpoint, and it returns before touching the client unless `approved` is True.
* The read path never holds the write token: the inbox is read with a client built
  from the read token alone, and the write client is created only inside the
  execution step, after a human approved an action. Spam, injections and uncertain
  emails plan no action, so that step is never reached for them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import httpx

from src.decider import INJECTION, LABELS, UNCERTAIN, Decision, decide

log = logging.getLogger("triage")

# Which actions each classification implies. `spam` implies none.
ROUTING: dict[str, list[str]] = {
    "billing": ["send_reply"],
    "bug_report": ["send_alert"],
    "sales_lead": ["send_reply", "create_lead"],
    "spam": [],
}

ACTION_KINDS = ("send_reply", "send_alert", "create_lead")
ALERT_CHANNEL = "#engineering"


@dataclass
class ProposedAction:
    """An action the agent WANTS to take. Proposing is not doing — nothing here
    touches the outside world until it has been approved and executed."""

    kind: str
    payload: dict
    # Every external write requires the write scope. Reads/no-ops do not.
    requires_write: bool = True
    rationale: str = ""
    # Identifies this action across runs, so it is never executed twice (D8).
    key: str = ""


@dataclass
class TriageResult:
    email_id: str
    label: str                      # one of LABELS, or "uncertain" / "injection"
    actions: list[ProposedAction] = field(default_factory=list)
    decision: Decision | None = None
    executed: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # keys rejected or already done


class WriteScopeError(PermissionError):
    """A write was attempted by a client that does not hold the write token."""


class TriageClient:
    """Thin wrapper over the mock API.

    A client built without `write_token` can only read the inbox: its write
    methods raise before any request leaves the process.
    """

    def __init__(self, base_url: str, read_token: str, write_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self._read_token = read_token
        self._write_token = write_token
        # Retries cover connections that could not be opened: nothing was sent, so they are safe for writes too.
        self._http = httpx.Client(timeout=10, transport=httpx.HTTPTransport(retries=2))

    @property
    def can_write(self) -> bool:
        return self._write_token is not None

    def get_inbox(self) -> list[dict]:
        response = self._http.get(f"{self.base_url}/inbox", headers=self._auth(self._read_token))
        response.raise_for_status()
        return response.json()

    def get_audit(self) -> dict:
        """What the mock API has received so far. The endpoint asks for no token."""
        response = self._http.get(f"{self.base_url}/_audit")
        response.raise_for_status()
        return response.json()

    def send_reply(self, *, to: str, subject: str, body: str, in_reply_to: str | None = None) -> dict:
        return self._write("/mail/send", {"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to})

    def send_alert(self, *, channel: str, message: str) -> dict:
        return self._write("/slack/alert", {"channel": channel, "message": message})

    def create_lead(self, *, name: str, email: str, company: str | None = None, summary: str | None = None) -> dict:
        return self._write("/crm/lead", {"name": name, "email": email, "company": company, "summary": summary})

    def _write(self, path: str, body: dict) -> dict:
        if self._write_token is None:
            raise WriteScopeError(f"{path} needs the write token; this client is read-only")
        response = self._http.post(f"{self.base_url}{path}", json=body, headers=self._auth(self._write_token))
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}


class Ledger:
    """Remembers which actions were already executed, across runs (D8)."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._done: set[str] = set()
        if path and path.exists():
            self._done = set(json.loads(path.read_text(encoding="utf-8")))

    def __contains__(self, key: str) -> bool:
        return key in self._done

    def add(self, key: str) -> None:
        self._done.add(key)
        if self.path:
            self.path.write_text(json.dumps(sorted(self._done), indent=2), encoding="utf-8")


def classify_email(email: dict) -> str:
    """Return the outcome for an email: one of LABELS, or "uncertain" / "injection"."""
    return decide(email).outcome


def template_reply(email: dict, label: str) -> str:
    """Placeholder draft. Replaced by a generated draft in the drafting step."""
    topic = "your billing question" if label == "billing" else "your interest in our platform"
    return (
        f"Hi,\n\nThanks for reaching out about {topic}. "
        "We have received your message and a member of our team will follow up shortly.\n\nBest regards"
    )


def _sender_name(address: str) -> str:
    local = address.split("@")[0]
    return " ".join(part.capitalize() for part in local.replace("_", ".").split(".") if part)


def _sender_company(address: str) -> str:
    domain = address.split("@")[-1]
    return domain.rsplit(".", 1)[0].replace("-", " ").title()


def plan_actions(label: str, email: dict, drafter: Callable[[dict, str], str] = template_reply) -> list[ProposedAction]:
    """Turn a classification into the actions it implies, per the routing table.

    Deterministic mapping; anything that is not an acting label plans nothing.
    """
    actions: list[ProposedAction] = []
    for kind in ROUTING.get(label, []):
        if kind == "send_reply":
            payload = {
                "to": email["from"],
                "subject": f"Re: {email['subject']}",
                "body": drafter(email, label),
                "in_reply_to": email["id"],
            }
            rationale = f"classified as {label}: reply to the customer"
        elif kind == "send_alert":
            payload = {
                "channel": ALERT_CHANNEL,
                "message": f"Bug report from {email['from']} — {email['subject']}\n{email['body'][:400]}",
            }
            rationale = "classified as bug_report: alert engineering"
        else:
            payload = {
                "name": _sender_name(email["from"]),
                "email": email["from"],
                "company": _sender_company(email["from"]),
                "summary": f"{email['subject']} — {email['body'][:200]}",
            }
            rationale = "classified as sales_lead: record the lead in the CRM"
        actions.append(ProposedAction(kind, payload, True, rationale, key=f"{email['id']}:{kind}"))
    return actions


def execute(action: ProposedAction, client: TriageClient, *, approved: bool) -> dict | None:
    """Execute a single proposed action — but only if a human approved it.

    This is the human-in-the-loop gate. If `approved` is False, nothing external
    happens: the function returns before the client is used.
    """
    if approved is not True:
        return None
    if action.kind not in ACTION_KINDS:
        raise ValueError(f"unknown action kind: {action.kind!r}")
    return getattr(client, action.kind)(**action.payload)


def triage_inbox(
    client: TriageClient,
    approver: Callable[[dict, ProposedAction], bool | ProposedAction],
    classifier: Callable[[dict], Decision] = decide,
    *,
    write_client_factory: Callable[[], TriageClient] | None = None,
    ledger: Ledger | None = None,
    drafter: Callable[[dict, str], str] = template_reply,
) -> list[TriageResult]:
    """Orchestrate the whole run.

    `client` reads the inbox and should be read-only. `write_client_factory` builds
    the client that holds the write token; it is called only after an approval.

    `approver(email, action)` returns False to reject, True to approve, or an edited
    `ProposedAction` to approve with changes.
    """
    ledger = ledger if ledger is not None else Ledger()
    results: list[TriageResult] = []

    for email in client.get_inbox():
        decision = classifier(email)
        result = TriageResult(email["id"], decision.outcome, decision=decision)
        results.append(result)

        if decision.outcome in (UNCERTAIN, INJECTION) or decision.outcome not in LABELS:
            log.info("%s: %s — no action (%s)", email["id"], decision.outcome, decision.reason)
            continue

        result.actions = plan_actions(decision.outcome, email, drafter)
        if not result.actions:
            log.info("%s: spam — logged and dropped (%s)", email["id"], decision.reason)
            continue

        for action in result.actions:
            if action.key in ledger:
                log.info("%s: already executed, skipped", action.key)
                result.skipped.append(action.key)
                continue

            verdict = approver(email, action)
            if isinstance(verdict, ProposedAction):
                # The human edited the text; identity of the action does not change.
                action, verdict = replace(verdict, key=action.key, kind=action.kind), True
            if verdict is not True:
                log.info("%s: rejected by the approver", action.key)
                result.skipped.append(action.key)
                continue

            if write_client_factory is None:
                raise WriteScopeError("an action was approved but no write client can be built")
            receipt = execute(action, write_client_factory(), approved=True)
            ledger.add(action.key)
            result.executed.append(receipt)
            log.info("%s: executed -> %s", action.key, receipt.get("id"))

    return results
