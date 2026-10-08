"""Record the model's answers for the inbox and the probe emails.

    python -m evals.record         # writes evals/recorded_answers.json

With DECIDER_ANSWERS pointing at that file the service replays these answers instead of
loading the model. Run it again whenever the model, the questions or the emails change.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.decider import MODEL_NAME, ask_model, email_key

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "evals" / "recorded_answers.json"


def main() -> None:
    emails = json.loads((ROOT / "fixtures" / "emails.json").read_text(encoding="utf-8"))
    emails += json.loads((ROOT / "evals" / "probes.json").read_text(encoding="utf-8"))
    answers = {}
    for email in emails:
        probabilities, injection = ask_model(email)
        answers[email_key(email)] = {"id": email["id"], "probabilities": probabilities, "injection": injection}
        print(email["id"], max(probabilities, key=probabilities.get), round(injection, 2))
    OUTPUT.write_text(json.dumps({"model": MODEL_NAME, "answers": answers}, indent=2) + "\n", encoding="utf-8")
    print(f"{len(answers)} answers written to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
