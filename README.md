# Rider Payout Dispute Desk

An agent that handles QuickDrop riders' payout complaints end to end: reads the WhatsApp message, checks the trips and payout exports, pays what is owed through PaySwift within Finance's limits, explains when nothing is owed, and hands anything unclear or shady to ops with a full trace.

## Run

```bash
cp .env.example .env               # add GROQ_API_KEY (free at console.groq.com); runs without one, see Design
docker compose up --build          # service :8000, PaySwift sandbox :8081, Postgres
python evals/run.py http://localhost:8000     # evals: replays data/conversations.json, prints a report
python -m pytest                              # 97 unit + API tests, no network needed
```

Without Docker: start Postgres and PaySwift, set `DATABASE_URL`/`PAYSWIFT_BASE_URL` in `.env`, then `uvicorn app.main:app`. CI (`.github/workflows/ci.yml`) runs the tests, then `docker compose up` + the evals, on every push.

| Endpoint | What it does |
|---|---|
| `POST /messages` | one rider message in → `{"reply": ...}`; a redelivered `wamid` gets the same stored reply, never a second run |
| `GET /trace/{rider_id}` | every step for the rider, in order: `message_in`, `tool_call`, `decision`, `reply`, `error` |
| `GET /ops/pending` | approvals (with `amount`) and escalations waiting for a human |
| `POST /ops/pending/{id}/approve` · `/reject` | approve = reconcile against PaySwift once more, then pay; reject = close |
| `GET /ops` | ops page: conversations, what the agent did, approve/reject buttons |
| `GET /health` | 200 when ready |

```
app/ai/          client.py (Groq, JSON mode, model fallback) · prompts.py · schemas.py · rules.py (rule-based reader)
app/services/    agent.py (orchestration) · investigator.py (tools) · payment_service.py (money) · finalizer.py · replies.py
app/repositories normalize.py + trips.py + payouts.py (the CSV exports, cleaned)      app/api/   HTTP layer, static/ops.html
app/domain/      policy.py: every number from docs/policy.md and Finance's note        evals/     run.py     tests/    97 tests
```

## Design

One request = one rider message. `agent.py` orchestrates; everything is written to the trace as it happens.

**The model decides** what the rider is talking about: claim type (surge / unpaid trip / incentive / duplicate penalty / distance / cancellation / vague), trip ids, the date (resolving *kal*, *20 wala*, *19th* against `received_at`), whether the message is a follow-up or pushback on an earlier answer, whether it is suspicious (speaks for another rider, prompt injection), and how to ask for missing details. It never sees money rules, and code checks its output: trip ids in the text are always kept, impersonation/injection is always flagged. If Groq is down or rate-limited the next model is tried, then the rule-based reader takes over.

**Code decides** everything else, deterministically:

- *Tools* (`investigator.py`): `check_trip(rider, trip_id)` — exists? this rider's? within 7 days? fare and penalty owed vs paid. `audit_day(rider, day)` — recomputes a whole IST day (every trip's fare, penalties, the 12-trip incentive), so "20 wala" or "19 sept ke 5 orders" is found without a trip id. Each finding is a *unit* (`trip:T926334`, `penalty:T637155`, `incentive:2026-09-18`) with expected, paid, owed.
- *Money* (`payment_service.py`): never pay more than owed — a unit is paid once, reconciled against **PaySwift's ledger** (the payout reference carries the unit ids), not our DB; ≤ ₹200 per dispute and one auto-payout per rider per day, otherwise an approval item; one claim = one dispute (R016's five unpaid trips are one ₹425 approval, not five auto-pays). Every payout has an idempotency key; a timed-out or in-progress call is watched in the ledger until the reply deadline (vendor retries at ~10s), then finished by a background finalizer — never re-sent blindly, never assumed failed.
- *Escalations*: not verifiable from data (distance, penalty waiver), rider disputes a correct answer, trip belongs to another rider, outside the 7-day window, suspicious message, payout failure, agent error.
- *Replies* are templated Hinglish built from the findings, so every number a rider reads was computed, not generated.

## Assumptions

- The exports are used "as they came" and cleaned in code: 10 duplicate trip rows dropped; rider ids `R7`/`r19` → `R007`/`R019`; the Pune riders' distances are in metres (800–11400) and read as km/1000 (T312538 is 5.0 km, owed ₹22, not ₹44,977). Each fix is recorded in the trace.
- "Day" is the IST calendar day of `started_at`; `payout_date` always matches it. The 7-day window and the once-a-day rule are counted from the message's `received_at`.
- Overpayments are reported, never clawed back or netted against shortfalls. Claimed amounts ("300 kam aaye") are never trusted.
- A date-only claim audits the whole day and pays every shortfall found, not only the thing the rider named — what an exec would do.
- The vendor's `rider_id` is trusted; a message claiming to be another rider is refused and escalated.

## Eval results

`python evals/run.py http://localhost:8000` on a fresh `docker compose up`: **24/24** sample conversations pass (payout reaching PaySwift, approval amount, escalation, facts mentioned in replies, trace shape). On one shared system it reports 23 PASS + 1 STALE: R003 appears in two conversations and the second correctly finds T926334 already paid. Slowest reply 1.6s; with PaySwift's 8s slow path the payout is still confirmed inside the reply (worst seen 8.2s). The same 24 pass with the LLM off (rule-based reader), which the CI job exercises.

## Skipped

- No LLM polishing of replies (templates only): correct but plain. `riders.csv` unused (no greeting by name, no unknown-rider check).
- Ops page has no auth, polls every 5s, no notes on approve/reject. Groq free-tier limits are handled by model fallback and the rule-based reader, not by queuing.
