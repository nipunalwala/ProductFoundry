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

From `backend/`. Stages 1 to 5 and 7 are real. Stage 6 keeps PRD order until prioritization is
built, and stage 8 arrives with its phase.

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
`--add competitors.json`. At the pain-point checkpoint it takes
`--rename N=LABEL`, `--merge N,M`, `--drop N` and `--rank N,M`, where N is the
pain point's rank or cluster id. `--fake-stages` runs every stage as a
stand-in, with no outside request.

```
py -m uv run productfoundry report RUN_ID [--format md|json] [--out FILE]
```

exports the pain-point report with its quotes, each linked to its source, each
pain point's trend (its share of all reviews over the last three months against
the three before) and a table of the reviews that talk about switching products.
`productfoundry prd RUN_ID` takes the same options and exports the PRD, each
requirement with the pain points or market gaps it cites, and
`productfoundry tasks RUN_ID` exports the task plan in build order, each task
with its acceptance criteria.

A report saved before trends and switching were added (schema version 2) cannot
be exported; re-run it with `resume RUN_ID --from-stage s3_pain_points`.

### Checking switching intent

```
py -m uv run python eval/switching/label.py prepare RUN_ID [--limit 200]
py -m uv run python eval/switching/label.py precision
```

`prepare` writes `eval/switching/to_label.csv`: up to 200 of the run's reviews
in a shuffled order, without the model's answer. Fill the `owner_label` column
with `leaving`, `switched_from`, `switched_to`, `considering` or `none`, then
run `precision` to see how often the model's switching labels agree with yours.
The file holds review text and is not committed. Neither command calls an LLM.

### Pricing snapshots

```
py -m uv run productfoundry pricing snapshot --product NAME --url URL [--run RUN_ID]
py -m uv run productfoundry pricing show --product NAME [--history]
```

`snapshot` reads one public pricing page (after checking its robots.txt), extracts
the plans and stores them. A price that is not written on the page is rejected.
`--run` files the snapshot under the product id that run uses, so it sits with
the run's reviews. The page is read with a headless browser: set
`PRICING_BROWSER_CHANNEL=msedge` (or `chrome`) in `.env` to use an installed
one, or run `py -m uv run playwright install chromium` once.

```
py -m uv run productfoundry pricing refresh             # read every tracked page again
py -m uv run productfoundry pricing alerts [--product NAME]
```

A page that was snapshotted once is tracked: the worker reads it again every
Monday at 03:00 UTC, and `refresh` does the same on demand. An unchanged page
costs no LLM call. A change in a price, a plan or a limit is stored as an alert,
shown by `pricing alerts` and by `GET /pricing/alerts`.

### Changelog tracking

```
py -m uv run productfoundry changelog track --product NAME [--run RUN_ID] \
    [--github OWNER/REPO] [--feed URL] [--page URL] [--app-store ID] [--google-play ID]
py -m uv run productfoundry changelog fetch            # read every tracked source
py -m uv run productfoundry changelog match RUN_ID     # match unseen items to the run (LLM calls)
py -m uv run productfoundry changelog alerts RUN_ID [--format json]
```

`track` names where a product publishes what it ships; the worker reads every
tracked source each Monday with the pricing pages, and `fetch` does it on
demand. `match` compares the release items of a run's products with the run's
pain points and PRD, and prints two kinds of alert: a competitor shipped a fix
for a pain point the run targets, or shipped a feature the PRD does not cover.
`GET /runs/{id}/changelog` returns the same alerts.

### Traction

```
py -m uv run productfoundry traction collect --product NAME [--run RUN_ID] [--term TERM] \
    [--region IN] [--google-play ID] [--app-store ID] [--no-trends]
py -m uv run productfoundry traction score --product NAME [--run RUN_ID] [--format json]
```

`collect` reads Google Trends (relative search interest) and the stores' install
range and rating counts, stores each with its source and date, and prints the
score. `score` prints it again from what is stored: growing, flat or declining,
a confidence that is never above medium, the basis of each signal, and the
signals that are missing. The store signals need a few weeks of history; the
worker collects them again every Monday.

## API and worker

```
docker compose up -d db redis worker        # the worker runs queued stages
cd backend
py -m uv run productfoundry serve            # the API on http://127.0.0.1:8000
```

Interactive docs are at `/docs`; the schema is `backend/openapi.json`
(`productfoundry openapi --out openapi.json` rewrites it). The API only creates,
approves and reads runs; the worker runs the stages. Set `FAKE_STAGES=true` for
both to try the flow with stand-in stages and no outside request. Redis listens
on `127.0.0.1:6380`.

## Frontend

Needs Node 20. With the API and the worker running:

```
cd frontend
npm install
npm run dev          # http://localhost:3000
```

The browser calls `/api`, which the frontend forwards to the backend at
`BACKEND_URL` (default `http://127.0.0.1:8000`). `npm run gen:api` regenerates the
API types from `backend/openapi.json`; `npm test`, `npm run lint` and
`npm run build` check the app.

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
