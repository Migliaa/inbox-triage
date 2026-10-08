"""Build the static demo: the lab page with the service's responses recorded inside it.

    python scripts/build_demo.py           # writes site/index.html

Starts the mock API and the service on spare ports, with the model's answers replayed from
evals/recorded_answers.json, asks the service everything the page can ask, and embeds the
answers in a copy of web/lab.html (web/demo_api.js takes the place of the calls to the
service). Reply drafts are recorded from the drafting model if one is reachable at
LLM_BASE_URL; otherwise the demo shows the fixed template.

Run it again after changing the page, the routing, the emails or the recorded answers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MOCK, LAB = "http://127.0.0.1:8199", "http://127.0.0.1:8100"
LABELS = ("billing", "bug_report", "sales_lead", "spam")
REPOSITORY = "https://github.com/Migliaa/inbox-triage"
BANNER = (
    '<div style="background:#fff7e0;border-bottom:1px solid #e5d9a8;padding:8px 20px;font-size:13px;color:#4a4020">'
    "Static demo. The model's answers and the reply drafts were recorded from the running system; "
    "approvals happen in this page only and are gone on reload. "
    f'<a href="{REPOSITORY}">Code and decisions</a></div>'
)


def text_key(email: dict) -> str:
    """Same key as textKey() in web/demo_api.js."""
    return json.dumps([(email.get(part) or "").strip() for part in ("from", "subject", "body")], ensure_ascii=False, separators=(",", ":"))


def start(module: str, port: int, env: dict) -> subprocess.Popen:
    command = [sys.executable, "-m", "uvicorn", module, "--port", str(port), "--log-level", "warning"]
    return subprocess.Popen(command, cwd=ROOT, env=env)


def record() -> dict:
    client = httpx.Client(base_url=LAB, timeout=300)
    for _ in range(60):
        try:
            client.get("/api/config").raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(1)

    data = {"config": {**client.get("/api/config").json(), "model": "recorded answers"}}
    data["inbox"] = client.get("/api/inbox").json()
    data["probes"] = client.get("/api/probes").json()
    data["views"] = {email["id"]: client.get(f"/api/emails/{email['id']}").json() for email in data["inbox"]}

    data["decisions"] = {}
    for email in data["inbox"] + data["probes"]:
        body = {"from": email.get("from"), "subject": email["subject"], "body": email["body"]}
        data["decisions"][text_key(email)] = client.post("/api/decide", json=body).json()

    # The slider: the outcome of every recorded decision at every threshold it can take.
    data["outcomes"], data["routes"] = [], {}
    for decision in data["decisions"].values():
        answers = {"probabilities": decision["probabilities"], "injection_probability": decision["injection_probability"]}
        indexes = []
        for step in range(50, 100):
            outcome = client.post("/api/route", json={**answers, "threshold": step / 100}).json()
            if outcome not in data["outcomes"]:
                data["outcomes"].append(outcome)
            indexes.append(data["outcomes"].index(outcome))
        key = json.dumps([answers["probabilities"], answers["injection_probability"]], separators=(",", ":"))
        data["routes"][key] = indexes

    data["drafts"] = {}
    for email_id, view in data["views"].items():
        for action in view["actions"]:
            if action["draft"] == "template":
                response = client.post(f"/api/emails/{email_id}/actions/send_reply/draft")
                if response.is_success:
                    data["drafts"][action["key"]] = response.json()
                    print("draft recorded for", email_id)
                else:
                    print("no draft for", email_id, "-", response.json().get("detail"))
    if not data["drafts"]:
        data["config"]["draft_model"] = "none"

    data["labelled"] = {}
    for email_id, view in data["views"].items():
        if view["handling"]["reviewable"]:
            data["labelled"][email_id] = {
                label: client.post(f"/api/emails/{email_id}/label", json={"label": label}).json() for label in LABELS
            }
    return data


def page(data: dict) -> str:
    html = (ROOT / "web" / "lab.html").read_text(encoding="utf-8")
    shim = (ROOT / "web" / "demo_api.js").read_text(encoding="utf-8")
    shim = shim.replace("__DEMO_DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    html, replaced = re.subn(r"async function api\(path, body\) \{.*?\n\}\n", lambda _: shim, html, count=1, flags=re.S)
    assert replaced == 1, "api() not found in web/lab.html"
    assert "</header>" in html and '<a href="/docs">API docs</a>' in html
    html = html.replace('<a href="/docs">API docs</a>', f'<a href="{REPOSITORY}">GitHub</a>', 1)
    return html.replace("</header>", "</header>\n" + BANNER, 1)


def main() -> None:
    env = {
        **os.environ,
        "API_BASE_URL": MOCK,
        "DECIDER_ANSWERS": "evals/recorded_answers.json",
        "LEDGER_PATH": str(ROOT / "site" / ".ledger.json"),
        "LANGSMITH_TRACING": "false",
    }
    (ROOT / "site").mkdir(exist_ok=True)
    servers = [start("mock_api.server:app", 8199, env), start("src.service:app", 8100, env)]
    try:
        data = record()
    finally:
        for server in servers:
            server.terminate()
        for server in servers:
            server.wait()
        (ROOT / "site" / ".ledger.json").unlink(missing_ok=True)
    output = ROOT / "site" / "index.html"
    output.write_text(page(data), encoding="utf-8", newline="\n")
    print(f"{output.relative_to(ROOT)}: {output.stat().st_size // 1024} KB, {len(data['decisions'])} emails, {len(data['drafts'])} drafts")


if __name__ == "__main__":
    main()
