# Engineering log

How the work went, in the order it happened. The reasons behind each choice are in
[`DECISIONS.md`](DECISIONS.md); this file keeps what went wrong, how it was noticed and what
changed because of it.

## How the work was broken down

Decisions first, code after. The points the brief leaves open (who decides the label, what
happens when the model is not sure, which errors cost more, what counts as an external action)
were written down as D1 to D9 before any code, together with the expected outcome of each of
the eight emails. Then one layer at a time, each with its own check before the next:

1. the decision step alone, run against the expected outcomes (`python -m evals.run`);
2. the routing table, the approval gate and the separation of the tokens, with tests;
3. a service and a page to see the decisions and to approve the actions;
4. reply drafts from a generative model;
5. robustness: what happens to emails without actions, and what each failure does;
6. the same flow as a LangGraph graph, to compare it with the hand-written one;
7. traces and experiments in LangSmith;
8. tests on every push, containers, a public demo.

The page came early on purpose. Tests say whether a property holds; the page shows what the
model actually receives and answers, and two of the problems below were found by looking at it.

## What went wrong, and how it was noticed

- **Garbled characters reached the model.** On Windows the mock API read the fixture file with
  the system code page, so e-004 and e-008 arrived with broken characters. The tests passed,
  because they do not look at the text. It showed up on the page, in the email body. Fixed by
  reading the file as UTF-8, the one change made to the provided mock API.
- **The model was missing from the requirements.** `opendecider` was installed by hand and never
  listed; a clean install would have failed at the first decision. Found when the dependencies
  were split in two files for CI.
- **The drafting model wrote nothing.** Gemma reasons before answering and spent the whole
  token budget on the reasoning: the answer came back empty. Reasoning is now switched off for
  drafts, and an empty answer is treated as a failure with its own test.
- **The first draft promised a refund.** With no rules in the prompt, the reply to the double
  charge of e-001 said "I have processed a refund for the duplicate payment". The prompt now
  forbids claiming that something was done and promising refunds, prices or dates. What limits
  the damage is still the position of the step (no tools, no token, a person reads the text),
  not the prompt.
- **A draft got a number wrong.** One draft for e-003 called the operations team "60 people":
  the company has 60 people, the pilot is for 12 seats. A second run got it right. It is the
  kind of error the approval is there for, and it is the reason the draft is kept once written
  instead of being regenerated at every opening.
- **Resuming the graph would have drafted twice.** In LangGraph the node that pauses runs again
  from its first line when the run resumes. With the draft in the same node as the pause, every
  approval would have called the drafting model again, about 45 seconds on this machine. The
  draft moved to the node before, the writes to the node after, and a test checks it.
- **An empty graph in Studio.** The LangSmith account is in the EU region; the default address
  refused the key and Studio, opened on the US address, showed nothing after login. One
  variable for the endpoint, one different address for Studio.
- **Free hosting had no room for the model.** The service with the model needs about 1.9 GB of
  memory; the hosting that looked suitable turned out to require a paid plan for containers.
  The public demo became a static copy of the page built from recorded responses (D17), with
  what is simulated stated on the page itself.

## What the probes changed

The eight inbox emails all came out as expected, which says little: they are the emails the
design was written around. Twelve more were written to press on one weak point each, with no
expected outcome.

- Two real customers were flagged as injections: one quoting a phishing email it had received,
  one writing a bug report in an imperative tone. Under the first design an injection verdict
  discarded the email, so a real bug report would have been lost without anyone seeing it.
  **Changed:** nothing is dropped unseen any more. Uncertain, injection and spam stay in the
  list, and a person can give the label (D12).
- Two attacks passed the injection check, at 0.20 and 0.21 against a threshold of 0.50: fake
  `[SYSTEM]` tags after a genuine-looking lead, and a request to add a "verify your account"
  link to the reply. **Not changed:** the check was not tuned to catch them. They obtained
  nothing in one run each, because the label can only select actions from a fixed table, the
  tags are never parsed, the draft left the link out, and a person reads the text before it
  leaves. The log records them as misses, and the README lists the check as a limit.

These are single runs on twenty emails in all. They were enough to find a design error; they
are not a measure of how often the model is right.

## What was left out

- **Tuning the threshold.** Eight emails are too few. 0.90 was chosen by reasoning, and the
  results show how narrow its margin is (the clear emails score 0.93 to 0.96).
- **A second decision model for comparison.** One fallback was identified (D1) and never
  needed.
- **Login and several approvers.** Approvals are kept in the process and in a local file.
- **The person's label inside the graph.** It exists in the service only; the graph holds the
  email and stops (D13).
- **Building the container images in CI.** The tests do not need them, and a build with torch
  at every push costs minutes to check files that rarely change.
- **The real service online.** See above; the recorded answers also run the real service
  without the model, which is how the repository opens in Codespaces.

## The decisions that carried the most weight

- **The label comes from a model that cannot write.** Everything else follows from this: a
  threshold on a calibrated number, an output that is always one of the declared options, and a
  generative model confined to the one step where text is needed.
- **The write token does not exist until a person approves.** Least privilege is a property of
  how the objects are built, not a check that could be skipped, and a test fails if that
  changes. The approval gate has a test of the same kind: removing the check on a trial branch
  turned the run red in CI.
- **A failure stops instead of guessing.** A write sent with no answer becomes "unconfirmed" and
  is not offered again, because offering it again risks a second email to a customer.
