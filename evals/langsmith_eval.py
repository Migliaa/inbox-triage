"""Put the emails in LangSmith as datasets and run the decider on them as experiments.

    python -m evals.langsmith_eval

Needs LANGSMITH_API_KEY in .env (and LANGSMITH_ENDPOINT for an EU account). It creates
two datasets if they are missing, then records one experiment on each:

* `inbox-triage-fixtures`: the eight inbox emails with the expected outcome. Two scores,
  as in `evals.run`: the outcome matches, and the error is not a dangerous one (D3).
* `inbox-triage-probes`: the probe emails. They have no expected outcome, so the
  experiment only records what the decider answered.

Eight emails and one run show the mechanism working; they do not measure accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from langsmith import Client, evaluate  # noqa: E402

from evals.run import is_dangerous  # noqa: E402
from src.decider import decide  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("id", "from", "subject", "body")


def ensure_dataset(client: Client, name: str, description: str, examples: list[dict]) -> None:
    if client.has_dataset(dataset_name=name):
        return
    dataset = client.create_dataset(name, description=description)
    client.create_examples(dataset_id=dataset.id, examples=examples)


def target(inputs: dict) -> dict:
    decision = decide(inputs)
    return {
        "outcome": decision.outcome,
        "top_label": decision.top_label,
        "probabilities": decision.probabilities,
        "injection_probability": decision.injection_probability,
        "reason": decision.reason,
    }


def matches_expected(outputs: dict, reference_outputs: dict) -> bool:
    return outputs["outcome"] == reference_outputs["outcome"]


def not_dangerous(outputs: dict, reference_outputs: dict) -> bool:
    """False when the decider would act on an email that should have been left alone."""
    return not is_dangerous(reference_outputs["outcome"], outputs["outcome"])


def main() -> None:
    client = Client()
    emails = json.loads((ROOT / "fixtures" / "emails.json").read_text(encoding="utf-8"))
    expected = json.loads((ROOT / "evals" / "expected.json").read_text(encoding="utf-8"))
    probes = json.loads((ROOT / "evals" / "probes.json").read_text(encoding="utf-8"))

    ensure_dataset(
        client, "inbox-triage-fixtures", "The eight inbox emails of the exercise, with the expected outcome.",
        [{"inputs": {k: e[k] for k in FIELDS}, "outputs": {"outcome": expected[e["id"]]}} for e in emails],
    )
    ensure_dataset(
        client, "inbox-triage-probes", "Emails written to press on one weak point each. No expected outcome.",
        [{"inputs": {k: p[k] for k in FIELDS}, "metadata": {"probes": p["probes"]}} for p in probes],
    )

    evaluate(target, data="inbox-triage-fixtures", evaluators=[matches_expected, not_dangerous],
             experiment_prefix="decider", max_concurrency=1, client=client)
    evaluate(target, data="inbox-triage-probes", experiment_prefix="decider-probes", max_concurrency=1, client=client)


if __name__ == "__main__":
    main()
