Backend README — Groq model fallback and rate-limit guidance

This document explains environment variables and behavior added to handle LLM model selection and rate limits.

Environment variables

- `GROQ_MODEL`: primary Groq model id used by agents (default: `llama-3.1-8b-instant`).
- `GROQ_FALLBACK_MODEL`: optional fallback Groq model id. When set, agents will attempt the fallback model once if the primary returns a rate-limit error.

Behavior

- Agents read `GROQ_MODEL` (and `GROQ_FALLBACK_MODEL`) from the environment.
- On provider rate limits (HTTP 429 / RateLimitError), agents will:
  - Attempt the `GROQ_FALLBACK_MODEL` once if configured (where applicable).
  - Log a rate-limited event with keys like `agent2_rate_limited`, `agent4_rate_limited`, or `agent5_rate_limited` including `job_id` and asset `index` when relevant.
  - If no fallback is available or the error persists, some agents will mark the workflow job as failed (`state.status = "failed"`) and set `state.error` with a helpful message; others will use existing local fallbacks (e.g., image/video local pipelines or placeholder assets).

Recommendations

- Set a smaller model for `GROQ_FALLBACK_MODEL` (for example a 13B variant) to reduce quota usage while preserving functionality.
- Monitor logs for `*_rate_limited` events; if frequent, consider:
  - Upgrading Groq quota or using a different provider
  - Reducing `max_tokens` in prompts
  - Increasing retry backoff or adding cooldown logic

Example `.env`

GROQ_MODEL=llama-3.1-8b-instant
GROQ_FALLBACK_MODEL=llama-3.1-8b-instant

This file is intentionally short — see agent source files for implementation details and exact log keys.
