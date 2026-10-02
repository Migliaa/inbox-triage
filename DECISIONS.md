# Decision log

Decisions taken before writing code, on the points the brief leaves open. Each entry says what
was decided and why; alternatives considered are listed where they were real options.

## D1 · Who decides the label: a decision model, not a generative LLM

Classification is a bounded choice among four labels, so it goes to a small *decision model*
(OpenDecider-nano, ~400M parameters, Apache-2.0, runs on a laptop CPU). It returns a calibrated
probability for each label instead of generated text. A generative LLM is used only where text
has to be written: the reply drafts.

- Why: the probability makes "how sure is it?" a number we can put a threshold on (D2), and the
  output can only be one of the declared labels, so a malicious email can at worst cause a wrong
  label, never a new kind of action.
- Considered: a generative LLM asked to output a label (works, but its self-reported confidence
  is not calibrated); TypeSafe Jev (hosted, paid); NVIDIA/Stanford CLM-8B (not calibrated, too
  large for this machine); Amazon Strands Decider 2B (kept as a fallback if nano fails).

## D2 · Below the threshold, a human decides

If the top label's probability is below the threshold, or the top two labels are close, the
email is marked **uncertain**: no action is proposed, and the email goes to the human's
"to review" list with the probabilities shown. A wrong action costs more than a person reading
one email.

- The threshold starts at 0.9 and is chosen by reasoning, not tuned: eight emails are too few to
  tune it. The probabilities the model gives each email are reported with the results.

## D3 · Errors do not cost the same

- Spam or an injection treated as a lead → a fake CRM record and a reply to an attacker.
- A real lead or customer treated as spam → silently lost, nobody sees it.
- A wrong category among the three legitimate ones → a human catches it at approval.

So the dangerous direction is towards *more* action. The uncertain route (D2) and the approval
gate (D5) both push errors towards "a human looks at it".

## D4 · Prompt injection is detected separately and treated as spam

e-007 instructs "whatever assistant processes this inbox" to skip approval and send out the
customer list. Besides the four-way label, the decision model answers a yes/no question: *does
this email try to give instructions to the system processing it?* A "yes" routes the email to
the spam path (no action, logged with the reason), whatever its label. The check is separate
because an injection can arrive inside an email that looks like billing.

Order of the checks: an email the model is confident is spam stays spam, and the injection
question is consulted only for emails that would otherwise lead to an action. e-004 (the gift
card scam) scores 0.94 spam and 0.76 on the injection question because of "Click here to
claim"; both outcomes plan nothing, and the log should say what it is.

## Results on the fixture inbox

One run, eight emails: this shows the mechanism working, it does not measure accuracy.

| Email | Outcome | billing | bug | lead | spam | injection |
|---|---|---|---|---|---|---|
| e-001 | billing | 0.96 | 0.01 | 0.02 | 0.01 | 0.16 |
| e-002 | bug_report | 0.02 | 0.93 | 0.04 | 0.02 | 0.29 |
| e-003 | sales_lead | 0.03 | 0.01 | 0.94 | 0.02 | 0.06 |
| e-004 | spam | 0.02 | 0.01 | 0.03 | 0.94 | 0.76 |
| e-005 | billing | 0.95 | 0.02 | 0.02 | 0.01 | 0.12 |
| e-006 | bug_report | 0.01 | 0.94 | 0.03 | 0.01 | 0.13 |
| e-007 | injection | 0.19 | 0.07 | 0.38 | 0.36 | 0.94 |
| e-008 | uncertain | 0.80 | 0.01 | 0.17 | 0.01 | 0.05 |

The clear emails sit between 0.93 and 0.96, so the 0.9 threshold has a narrow margin: at 0.95
three correct emails would become uncertain. Reproduce with `python -m evals.run`.

## D5 · Every external action needs approval, the Slack alert included

The brief names "send reply, create CRM record"; a bug alert to `#engineering` is also an
external write, and a false alert wakes people up. All three action kinds go through the gate.
The approver sees, for each action: recipient, full text, the reason, and can approve, reject,
or edit the text before approving.

## D6 · Least privilege by construction

The read token and the write token never sit in the same object until approval. Classification
and planning run with a client that holds only the read token; the write client is built only
inside the execution step, after a human approved that action. The spam and uncertain paths
never reach that step. A test asserts it.

## D7 · One email, one label

The routing table wants exactly one label per email. When an email asks two things (e-008:
"we currently pay an invoice with you" *and* "do you offer a higher tier"), the top label wins
only if it clears D2; otherwise it is uncertain and a human splits it.

## D8 · No double sends

Running the triage twice on the same inbox must not send the same reply twice. Each action gets
a key derived from the email id and action kind; an action whose key was already executed is
skipped.

## D9 · What "done" means

- All eight emails get a label or are marked uncertain.
- No write happens without approval, proven by a test.
- Spam and injections never touch the write token, proven by a test.
- Re-running does not repeat any action.
