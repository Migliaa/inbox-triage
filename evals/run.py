"""Run the decision step on the fixture inbox and compare with the expected outcomes.

    python -m evals.run            # table + exit code 1 if a dangerous error occurred

Errors are not equal (DECISIONS.md, D3). A *dangerous* error is one that leads to more
action than the right answer: an attack or an uncertain email that ends up with a label
that plans actions. Everything else is caught by a human at approval, and is only counted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.decider import INJECTION, UNCERTAIN, decide

ROOT = Path(__file__).resolve().parent.parent
ACTING = {"billing", "bug_report", "sales_lead"}
NO_ACTION = {"spam", INJECTION, UNCERTAIN}


def is_dangerous(expected: str, got: str) -> bool:
    return expected in NO_ACTION and got in ACTING


def main() -> int:
    emails = json.loads((ROOT / "fixtures" / "emails.json").read_text(encoding="utf-8"))
    expected = json.loads((ROOT / "evals" / "expected.json").read_text(encoding="utf-8"))

    correct = dangerous = 0
    print(f"{'id':<6} {'expected':<11} {'got':<11} {'billing':>7} {'bug':>6} {'lead':>6} {'spam':>6} {'inject':>7}")
    for email in emails:
        decision = decide(email)
        want, got = expected[email["id"]], decision.outcome
        correct += want == got
        bad = is_dangerous(want, got)
        dangerous += bad
        p = decision.probabilities
        flag = "  DANGEROUS" if bad else ("" if want == got else "  differs")
        print(
            f"{email['id']:<6} {want:<11} {got:<11} {p['billing']:>7.2f} {p['bug_report']:>6.2f} "
            f"{p['sales_lead']:>6.2f} {p['spam']:>6.2f} {decision.injection_probability:>7.2f}{flag}"
        )

    print(f"\nmatches expected: {correct}/{len(emails)}   dangerous errors: {dangerous}")
    return 1 if dangerous else 0


if __name__ == "__main__":
    sys.exit(main())
