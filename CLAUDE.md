# ProductFoundry

AI product manager agent: turns a software product idea into an evidence-backed
build plan (competitors, review-mined pain points, PRD, tasks, roadmap,
tracking).

- Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Build order and rules: [docs/BUILD_SPEC.md](docs/BUILD_SPEC.md)

Work is done one phase at a time from BUILD_SPEC.md. Read its "Rules for every
phase" before changing code, and finish and commit one phase before starting the
next.

Git:

- Push to `origin` (`main`) after every commit.
- Do not add a Claude `Co-Authored-By` line, or any other AI attribution, to
  commit messages.

Build and test (from `backend/`):

```
uv run ruff check . && uv run pytest
```

API keys live in `.env` (never committed). Never print or log them. All LLM
providers are on free tiers with shared daily limits, so tests use recorded
responses and live calls are made only when a phase allows them.
