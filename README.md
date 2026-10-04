# ProductFoundry

An AI product manager agent. It turns a software product idea into an
evidence-backed build plan: competitor research, pain points mined from real
reviews, a PRD, engineering tasks with acceptance criteria, a prioritized
roadmap and progress tracking. Every claim links back to the reviews or pages
it came from.

The project is being built one phase at a time. See
[docs/BUILD_SPEC.md](docs/BUILD_SPEC.md) for progress and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Setup

Needs Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) and Docker. The
commands below run uv as `py -m uv`; use plain `uv` if it is on your PATH. The
database listens on `127.0.0.1:5433`.

```
cp .env.example .env
docker compose up -d db
cd backend
py -m uv sync
py -m uv run productfoundry --version
py -m uv run productfoundry db upgrade
```

Runs are stored in the database. Pass `--memory` to keep them in a local JSON
file instead, with no database needed.

## Use

From `backend/`. Stages are stand-ins until their phases are built.

```
py -m uv run productfoundry run --input input.json   # stops at the first checkpoint
py -m uv run productfoundry status [RUN_ID] [--output STAGE]
py -m uv run productfoundry approve RUN_ID [--edit edited.json]
py -m uv run productfoundry resume RUN_ID [--from-stage STAGE]
py -m uv run productfoundry schema [NAME] [--out DIR]  # JSON Schema of the contracts
py -m uv run productfoundry llm check                  # one small live call per provider
```

LLM providers and their pinned models are set in
`backend/src/productfoundry/llm/routing.toml`. Put `GEMINI_API_KEY`,
`GROQ_API_KEY` and `OPENROUTER_API_KEY` in `.env`, and `TAVILY_API_KEY` for web
search.

At the competitor checkpoint, `approve` also takes `--remove NAME` and
`--add competitors.json`. `--fake-stages` runs every stage as a stand-in, with
no outside request.

## Test

From `backend/`:

```
py -m uv run ruff check . && py -m uv run pytest
```

Tests never call an LLM provider, a review source or GitHub. They use recorded
responses.

## Privacy

ProductFoundry sends your product idea and review text to third-party LLM
providers on their free tiers. Google's Gemini free tier may use prompts to
improve Google's products. Do not enter an idea you need to keep confidential
until the bring-your-own-key option exists.
