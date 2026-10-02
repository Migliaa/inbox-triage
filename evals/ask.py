"""Try the decider by hand: type an email, see the probabilities and the outcome.

    python -m evals.ask                 # threshold 0.9
    python -m evals.ask --threshold 0.8

The model is loaded once, then every email takes a moment. Empty subject to quit.
"""

from __future__ import annotations

import argparse

from src.decider import LABELS, THRESHOLD, ask_model, route


def bar(p: float, width: int = 30) -> str:
    return "#" * round(p * width)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    threshold = parser.parse_args().threshold

    print("Loading the model (about 30 seconds)...")
    ask_model({"subject": "warm-up", "body": "warm-up"})
    print(f"Ready. Threshold {threshold}. Leave the subject empty to quit.\n")

    while True:
        subject = input("Subject: ").strip()
        if not subject:
            return
        body = input("Body:    ").strip()
        sender = input("From (optional): ").strip() or "someone@example.com"

        probabilities, injection = ask_model({"from": sender, "subject": subject, "body": body})
        decision = route(probabilities, injection, threshold=threshold)

        print()
        for label in LABELS:
            print(f"  {label:<11} {probabilities[label]:.2f}  {bar(probabilities[label])}")
        print(f"  {'injection?':<11} {injection:.2f}  {bar(injection)}")
        print(f"\n  -> {decision.outcome.upper()}   ({decision.reason})\n")


if __name__ == "__main__":
    main()
