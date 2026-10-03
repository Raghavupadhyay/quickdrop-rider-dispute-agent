# AI session logs

Raw exports of the AI sessions used for this submission, one file per session, unedited.
Claude Code writes one JSONL file per session; every line is a message, tool call or tool
result, so the files show the actual back-and-forth, including the wrong turns.

| File | Tool | When (IST) | What it covers |
|---|---|---|---|
| `<earlier-session>.jsonl` | <tool used for the first version, e.g. Claude Code / Codex CLI / Copilot> | before 3 Oct 2026 19:30 | First version of the service: FastAPI skeleton, LLM claim extraction with Groq, deterministic resolver and payout calculator, PaySwift client, ops endpoints, first unit tests. |
| `66bd8073-34ff-4f0a-9983-36b80cbec21e.jsonl` | Claude Code (Opus 5.5, then Fable 5.1) | 3 Oct 2026 ~19:00–20:45 | Started as a debugging question (`GROQ_MODEL` missing, `load_dotenv` import). Then: gap analysis against the brief, emergency push to `main` at the deadline, Dockerfile/compose, data-trap discovery (duplicate trips, `R7`/`r19`, metre distances), rebuild of the agent (day audit, 7-day window, PaySwift reconciliation, escalations, trace/ops shapes), probing the PaySwift sandbox (503s, 8s slow path, 200 replays, `request_in_progress`), model fallback after Groq retired the model id, eval runner, 97 tests, ops page, CI, README. |

## Where the exports come from

- Claude Code: `~/.claude/projects/<project-dir>/<session-id>.jsonl` — for this repo the project dir is
  `-home-raghav-codes-ProcureYard-assignment`; sessions started from the home directory are under `-home-raghav`.
- Codex CLI: `~/.codex/sessions/<date>/rollout-*.jsonl` · Copilot in VS Code: Command Palette → "Chat: Export Chat…" ·
  Gemini CLI: `/export jsonl` · Antigravity: chat export (see SUBMISSION.md).

Nothing in these files is edited. The assistant only ever listed `.env` variable names, never values, but grep each export for
your key before pushing (`grep -c "$GROQ_API_KEY" ai-logs/*.jsonl` should print 0).
