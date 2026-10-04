# ProductFoundry: Build Spec

This file defines the order in which ProductFoundry is built and what "done"
means at each step. The design itself is in [ARCHITECTURE.md](ARCHITECTURE.md);
this file only says how to get there.

Each phase below is a self-contained prompt. To build the project, run the
phases in order, one at a time: "Do phase N of docs/BUILD_SPEC.md".

The phases are grouped into the four milestones of the roadmap. Each milestone
ends with a gate. The next milestone starts only after the gate passes.

| Milestone | Phases | Gate |
|---|---|---|
| 1. Pain-point engine (MVP) | 0-8 | Finds known pain points on 3 test products |
| 2. PRD and tasks | 9-16 | Every requirement cites evidence |
| 3. Market panel | 17-23 | Pricing parsed correctly for 10 products |
| 4. Prioritize and track | 24-28 | A full run passes the evaluation set |

## Rules for every phase

1. **One phase at a time.** Do not start work that belongs to a later phase,
   even if it looks convenient.
2. **Read first.** Read this file, ARCHITECTURE.md and the code the phase
   touches before writing anything.
3. **Stay in scope.** Build only the phase's deliverables. Anything else worth
   doing goes in "Notes for later" at the bottom of this file.
4. **Verify before reporting.** A phase is done only when its acceptance checks
   pass, including, from `backend/`:
   ```
   py -m uv run ruff check . && py -m uv run pytest
   ```
   If a check cannot be run (for example it needs a key the owner has not
   created), say so explicitly.
5. **No live calls in tests.** Tests never reach an LLM provider, a review
   source, a search API or GitHub. They use recorded responses in
   `backend/tests/fixtures/` and fake adapters. A live call is made only when a
   phase lists it under "Live budget", and the count is reported.
6. **Keys are never printed, logged or committed.** They live in `.env` only.
   `.env.example` lists the names with empty values.
7. **Respect the seams.** Stages never call each other and never touch run
   state. Only `sources/<name>` knows that source's URLs or payloads. Only `llm`
   imports LiteLLM. Stages name an LLM *task*, never a provider or model.
8. **Schemas are the contract.** Every stage output is a pydantic model in
   `core`. Changing one bumps its schema version and is called out in the
   report.
9. **Evidence or nothing.** Any generated claim, requirement or score that
   cannot point to stored evidence fails validation. Do not loosen a validator
   to make a run pass.
10. **Compliance.** Check robots.txt before fetching a page. The only
    exceptions are the ones ARCHITECTURE.md section 8 states (store reviews,
    decision D8). Drop usernames before storing a review. Never add a source
    that ARCHITECTURE.md section 8 excludes.
11. **Update the tracker.** When a phase passes, mark it in the progress table
    and record anything the next phase needs to know.
12. **Stop and report.** After each phase: what was built, what was verified and
    how, what was not verified, and any deviation from ARCHITECTURE.md. Commit
    the phase. Stop when a phase needs a decision or a key from the owner; do
    not guess past a "Needs from the owner" item.
13. **Design changes go in the doc.** If a phase shows the architecture is
    wrong, update ARCHITECTURE.md in the same phase and call it out.

## Conventions

- Python 3.11 or newer, managed with `uv`, run as `py -m uv` on the
  development machine. One package, `productfoundry`, under
  `backend/src/`. Dependencies and tool settings live in
  `backend/pyproject.toml` only.
- pydantic v2 for every schema; SQLAlchemy 2 and Alembic for storage; pytest;
  ruff for lint and format.
- Pure logic (state machine, validators, scoring, clustering post-processing,
  trend maths) has no I/O and is unit tested.
- Adapters are a `Protocol` in the package's `__init__` plus one real
  implementation and one fake for tests.
- Ids are strings with a prefix: `run_`, `prod_`, `rev_`, `cl_`, `req_`,
  `task_`. Evidence is always a list of ids, never free text.
- Times are timezone-aware UTC in storage. Review dates keep the source's date.
- Randomness is seeded. The seed is part of the run record.
- Prompts live in `backend/src/productfoundry/stages/prompts/` as files, one
  per task, with a version in the filename.
- Windows is the development machine. Anything that does not run natively on
  Windows (the queue worker) runs in Docker.
- The Docker database listens on `127.0.0.1:5433` (`POSTGRES_PORT`). Port 5432
  belongs to a native PostgreSQL on the development machine and is not used by
  this project.

## Decisions needed from the owner

A phase that depends on an open item stops and asks. Defaults are used only
where the table says so.

| # | Question | Needed by | Default if not answered |
|---|---|---|---|
| D1 | Which web search API for competitor discovery? It must be an official API with a free tier | Phase 4 | **Answered 2026-10-04:** Tavily (1,000 free credits a month, no card; one basic search is one credit). Key: `TAVILY_API_KEY` |
| D2 | Which 3 products form the first test set? Mobile apps with many store reviews work best | Phase 8 | None: phase 8 asks |
| D3 | Which review languages are analysed in the MVP? | Phase 5 | **Answered 2026-10-04:** English and Hinglish (Hindi-English in Latin script). Other languages are stored and flagged, not analysed |
| D4 | Personal tool, portfolio project or product to sell? | Phase 14 | Single user, no accounts, no login |
| D5 | Which 10 products form the evaluation set? | Phase 23 | None: phase 23 asks |
| D6 | A paid traffic-estimate provider? | Phase 20 | None. Traction is built without traffic data |
| D7 | Which half to polish first: research or PRD-to-execution? | After gate 2 | Follow the roadmap order |
| D8 | Store reviews and robots.txt. Google Play's robots.txt disallows `/_/` and `/store/getreviews`, the paths the scraper library uses to fetch reviews. Apple's disallows `/*/rss/*`, the customer-reviews feed. Rule 10 says a disallowed page is skipped, not worked around. How are reviews collected? | Phase 5 | **Answered 2026-10-04:** allowed as a stated exception. Reviews are fetched with the scraper library (Play) and the RSS feed (Apple), rate limited, capped per product, with no usernames stored. See ARCHITECTURE.md section 8 |

## Progress

| Phase | Title | Status |
|---|---|---|
| 0 | Toolchain and first green build | Done 2026-10-04. Lint and 1 test pass; `productfoundry --version` prints 0.1.0; the database container starts healthy and `CREATE EXTENSION vector` succeeds (pgvector 0.8.7 on PostgreSQL 17). uv is run as `py -m uv` because pip installed it outside PATH. The database is on host port 5433 because a native PostgreSQL already uses 5432 on this machine |
| 1 | Schemas and the run state machine | Done 2026-10-04. Lint and 96 tests pass. All four schemas are v1. `approve` only records the approval; `resume` continues the run (the CLI does both). A stage that raises `QuotaExhausted` goes back to `pending`, so `resume` retries it. The CLI keeps runs in `.productfoundry/runs.json` (`--state-file`), a JSON dump of the in-memory store, so `status`, `approve` and `resume` work across commands until phase 2. `RunStore` contract tests are in `tests/test_run_store.py`, parametrised by store: phase 2 adds Postgres to the `store` fixture. `CompetitorList` already has `rejected` (needed in phase 4) and `ReviewSourceName` already lists Reddit and Product Hunt, to avoid a version bump later |
| 2 | Storage | Done 2026-10-04. Lint and 114 tests pass with the database up (100 pass, 14 skip with a clear message when it is down). Migration `0001` matches the models (checked by a test). Run `productfoundry db upgrade` once per database; the CLI says so when tables are missing. Database tests use a separate `productfoundry_test` database on the same server and truncate it per test (`sessions` fixture in `tests/conftest.py`). `reviews.embedding` is `vector` with no dimension: phase 6 fixes the dimension when it pins the model. Review ids are derived from (source, source review id) by `core.ids.review_id`. An upsert of a known review refreshes text, rating, URL and date but keeps language, sentiment and embedding. `clusters` holds only id, run, size and negative share; phases 6 and 7 add their columns by migration. There is no product repository yet (phase 4 or 5 adds it). Settings come from `productfoundry.settings.Settings` (`DATABASE_URL` or `POSTGRES_*`, read from `.env`) |
| 3 | LLM gateway | Done 2026-10-04. `productfoundry llm check` passed for all three providers (3 live calls: Gemini 20.1 s, Groq 0.9 s, OpenRouter 1.0 s). Models pinned in `llm/routing.toml` after looking them up: Gemini `gemini-3.8-flash`, Groq `openai/gpt-oss-120b` (free plan: 1,000 requests and 200K tokens a day, 8K tokens a minute), OpenRouter `nvidia/nemotron-3-super-120b-a12b:free` (50 requests a day until $10 of credit is bought). Google no longer publishes Gemini free-tier limits, so `requests_per_day = 250` for Gemini is a placeholder to correct from the AI Studio rate-limit page. Providers are asked for JSON mode with the JSON Schema appended to the prompt, and the gateway validates. The cache is the `response` column of `llm_calls` (migration `0002`); it is checked for every provider in the chain before any call. A provider with no key is passed over. `QuotaExhausted` is raised only when every provider was skipped for quota or answered 429; other failures raise `LlmFailed`, which fails the stage. A non-retryable error (401, 400) moves on without a retry. Tests build a `Gateway` with `FakeProvider` and in-memory stores from `llm/fakes.py`; `RecordingProvider` and `ReplayProvider` save and serve fixtures by task and prompt hash. A new task must be added to a group in `routing.toml` |
| 4 | Stage 1: competitor research | Done 2026-10-04. Lint and 209 tests pass. Live run for Splitwise (region IN) returned 9 competitors, incumbent first; the list was shown to the owner. Live totals: 10 Tavily searches (the budget) and 6 LLM calls (one over the budget of 5: Gemini answered 503 "high demand" on all 4 attempts, Groq rejected one with HTTP 400 and answered the next), over two attempts, plus 2 requests to record store fixtures. The first attempt hung: OpenRouter keeps a queued request open with keep-alive bytes, which defeats the HTTP timeout, so `LiteLLMProvider` now enforces a total deadline. Provider errors now keep the start of the provider's message with the key removed. The live run also showed the Play lookup missing the right app when it was third in the results; it now tries results whose title matches first. The recorded search and LLM responses are fixtures and replay in a test; the hand-written fixtures stay for the merge and URL-check cases and say they are hand-written. The second attempt capped store lookups at 3 to hold the search budget, so some competitors in that run have no store ids. Google Play's robots.txt disallows `/store/search`, so a Play app is found by name through Tavily restricted to play.google.com and only its details page is read (see ARCHITECTURE.md section 12). A run makes at most 3 discovery searches plus one Play search per competitor looked up (incumbent and up to 6 others): 10 at most. Every kept competitor must cite a real search result, except the incumbent and the user's known competitors; a URL no result shows is dropped. Product ids are derived from the store id or the name (`core.ids.product_id`), so they are stable across runs. The CLI runs the real stage 1 and stand-ins for stages 2 and 3; `--fake-stages` makes every stage a stand-in. `approve` takes `--remove NAME`, `--add FILE` and `--edit FILE`. Stages get adapters through `Services` (`llm`, `search`, `app_lookups`). Phase 5 needed D8, now answered |
| 5 | Stage 2: review collection | Done 2026-10-04. Lint and 264 tests pass. Live fetch for Splitwise (region IN, cap 200 per store): 192 reviews stored (96 Google Play, 96 App Store), 16 dropped as too short; 189 English, 2 Hinglish, 1 Devanagari Hindi (stored, not analysed); sentiment 99 negative, 53 positive, 29 mixed, 10 neutral. Requests: 2 to Google Play, 3 to the App Store feed (plus 4 earlier to check payload shapes), 6 LLM calls (5 answered by Groq, one 429 retried). They are in the database under product `prod_splitwise` for phase 7. Language detection on the 74-review fixture: 72 decided, all 72 correct, 2 borderline (sent to the LLM), no Hinglish labelled as another language. The fixture and the lexicon were written by the same hand, so this is optimistic; gate 1 is the real test. Splitwise has almost no Hinglish reviews, so D2 should include a product that does. Borderline reviews get their language from the same LLM call that gives the sentiment, so language costs no extra calls. Store fixtures keep the real field names but invented names and texts: no real review or reviewer is in the repository, so there is no recorded live sentiment fixture either. The gateway now paces calls to per-minute limits (`requests_per_minute`, `tokens_per_minute` in `routing.toml`). `Services` gained `reviews` (a `ReviewStore`) and `review_sources`. The cap is `--review-cap` on the CLI (default 2,000). Sentiment batches are 40 reviews. With `--memory`, reviews do not outlive the command |
| 6 | Embeddings and clustering | Done 2026-10-04. Lint and 284 tests pass. No live calls. **Model pinned: `intfloat/multilingual-e5-small`** (384 dimensions, prefix `query: `), with **per-language centring**, which the comparison showed is needed (see "Embedding model comparison" below, and ARCHITECTURE.md section 6.1). On the hand-written theme fixture (3 themes x 10 English + 10 Hinglish, 10 noise) the pinned setup gives exactly three clusters, each holding both languages; without centring every model split themes by language. Clustering the 192 live Splitwise reviews: 128 negative or mixed reviews, 4 clusters (71, 31, 18, 8), no noise, identical on a second run; the largest is the daily-expense paywall. Entry point for phase 7: `ml.pipeline.cluster_run(run_id, product_ids, reviews=, clusters=, embedder=, config=, seed=)`, which embeds what is missing, centres, clusters negative and mixed reviews, saves the clusters for the run and returns a `ClusteringResult` (`core/clusters.py`). Settings are in `ml/config.toml`. Migration `0003` fixes `reviews.embedding` at 384 dimensions, adds `reviews.embedding_model`, and replaces `cluster_reviews.representative` with `representative_rank`. Raw vectors are stored; centring is done per run. Models download once into the Hugging Face cache in the user profile; tests set `HF_HUB_OFFLINE=1` and skip the real-model tests when the model is not cached. Downloads through Python stalled on this network until `HF_HUB_DISABLE_XET=1` was set. `Services` does not yet carry the embedder or the cluster store: phase 7 adds them when stage 3 uses them |
| 7 | Stage 3: pain-point report | Done 2026-10-04. Lint and 323 tests pass. Live labelling run on the 192 Splitwise reviews of phase 5 (run `run_3acb37dc630f4f8d`, left at the pain-point checkpoint for the owner): 4 clusters gave 3 pain points and 1 junk cluster. Ranked: "Daily limit on adding expenses" (71 reviews, severity 4, score 47.2), the same label again for a second cluster (18 reviews, 12.4), "Incorrect expense calculation and totals" (8 reviews, 6.4); the junk cluster holds 31 vague reviews ("Not good anymore"). LLM calls: 9 attempts for 4 labels (budget 30): Gemini answered 2 and returned 503 on 5 attempts, Groq answered 2. **`PainPointReport` is now schema version 2**: a pain point gained `merged_cluster_ids` and `quote_glosses` (English translation of a Hinglish quote, by review id), the report gained `clustered_reviews` and `noise_reviews`. Ranking: `score = review_count x (0.5 + 0.5 x negative_share) x severity / 5` (`core.pain_points.pain_point_score`), written in every report. One LLM call per cluster, sending only its representative reviews (5), numbered; the answer cites reviews by number, and a number outside the sample or a Hinglish quote with no gloss fails the schema, so the gateway retries and falls back. `stages/s3_pain_points/validation.check_report` checks every quote, count, share, product list, score and language count against the saved clusters and stored reviews; the stage runs it before returning and the CLI runs it on every checkpoint edit (order by score is required of the stage, not of an edit). Checkpoint: `approve --rename N=LABEL`, `--merge N,M`, `--drop N`, `--rank N,M`, where N is a rank in the stage's output or a cluster id. A merge keeps the first pain point's label, sums the reviews, takes the highest severity and quotes each part in turn. `productfoundry report RUN_ID [--format md\|json] [--out FILE]` exports the report with each quote's text (at most 280 characters), source URL and date; it needs the database. `Services` gained `run_id`, `clusters` and `embedder`. The labelling fixture is hand-written: the live answers were not recorded, because replaying them needs the real reviews, which stay out of the repository. An edited report cannot be checked on a `--memory` run, so it is refused there |
| 8 | Gate 1: three test products | In progress 2026-10-04: **waiting for D2**. Built and tested (lint and 329 tests pass): `backend/eval/gate1/` with a README, `expected/TEMPLATE.json` for the owner's lists, and `line_up.py` (`input`, `propose`, `summary`). `propose` embeds the expected themes and the reported pain points, offers the 3 nearest pain points per theme and leaves `confirmed` null for the owner; `summary` prints themes found, junk clusters, reviews by language, LLM attempts and tokens (`PostgresCallStore.run_totals`), wall time and unresolved quotes. Smoke-tested on the Splitwise run of phase 7 with a throwaway file. Not done: the three expected lists, the three runs, the owner's verdict. Splitwise's output has already been shown to the owner, so a Splitwise list written now is not blind |
| 9 | Stage 4: PRD with citations | Done 2026-10-04, **ahead of gate 1** on the owner's instruction (see "Order change" below). Lint and 351 tests pass. Live run on the approved Splitwise run (`run_3acb37dc630f4f8d`, pain points 1 and 2 merged by the owner): 4 requirements, grounding 100% (2 cite a pain-point cluster, 2 cite the Splitwise market gap). LLM calls: 3 attempts for 1 PRD (budget 6): Gemini 503 twice, OpenRouter answered. New schema `Prd` v1 (`core/prd.py`): problem, users, goals, non-goals, requirements (`req_001`, ... with statement, priority `must`/`should`/`could`, evidence), success metrics, and the market gaps cited. A market gap is one fact per competitor from stage 1 (its positioning and target users) with id `gap_<hash of the product id>` (`core.prd.market_gaps`). A requirement may cite the `cluster_id` of an approved pain point or a gap id; a cluster merged into another is cited through the pain point that holds it. The LLM answer is validated against the run's evidence ids, so an invented or missing citation retries and falls back; `stages/s4_prd/validation.check_prd` checks the finished PRD again. `productfoundry prd RUN_ID [--format md\|json] [--out FILE]` exports it: each requirement shows its pain points with counts and one quote, and links to an evidence section. The pipeline now has 4 stages; a run started earlier gets the new stage on `resume --from-stage s4_prd`. Tests cannot reach a provider: `conftest.py` replaces `llm.live_providers` |
| 10 | Stage 5: task breakdown | Done 2026-10-04, ahead of gate 1 (see "Order change"). Lint and 370 tests pass. Live run on the Splitwise PRD of phase 9: 4 epics and 9 tasks covering all 4 requirements, no cycle; efforts S to L. LLM calls: 3 attempts for 1 plan (budget 6): Gemini 503 twice, OpenRouter answered. The run input had no tech stack, so that path is covered by tests only. New schema `TaskPlan` v1 (`core/tasks.py`): epics (`epic_01`, ...) holding tasks (`task_001`, ...) with title, description, `requirement_ids`, `depends_on` and an effort on the fixed scale XS, S, M, L, XL (half a day to about two weeks for one developer). The schema itself rejects a duplicate id, an unknown or self dependency and a cycle (`core.tasks.dependency_order`, which also gives the build order). The LLM names tasks by key (`T1`, ...); its answer is validated for orphan tasks, uncovered requirements and cycles, so a bad plan retries and falls back; the stage then assigns ids and checks coverage against the run's PRD again (`stages/s5_tasks.coverage_problems`). `productfoundry tasks RUN_ID [--format md\|json] [--out FILE]` exports the plan: JSON with a `build_order`, Markdown with every task after the tasks it depends on. It needs no stored reviews, so it works with `--memory`. The pipeline has 5 stages |
| 11 | Stage 7: acceptance criteria | Done 2026-10-04, ahead of gate 1 (see "Order change"). Lint and 393 tests pass. Live run of stages 4 to 7 on the Splitwise run: stages 4 and 5 came from the gateway's cache (same prompts, no provider call), stage 6 passed PRD order through, stage 7 wrote 28 criteria for the 9 tasks (9 happy path, 10 edge case, 9 failure), one call per epic. LLM calls: 4, all answered by Groq (budget 10). **Gemini was left out of that run**: it had answered 503 on 9 of its 11 attempts that day, and two wasted attempts per epic would have broken the budget; the run was started from a script with the same stages and gateway minus that provider. New schemas: `AcceptanceCriteria` v1 (`core/acceptance.py`: per task a list of criteria with `kind` `happy_path`/`edge_case`/`failure_state`, `given`, `when`, `then`; the schema requires one happy path and one edge or failure per task and no empty part) and `Roadmap` v1 (`core/roadmap.py`: `ordering: prd_order`, one item per requirement with its tasks). Phase 24 replaces the pass-through and bumps `Roadmap`. `StageSpec` gained `pass_through`, which skips the stage's checkpoint, so `CHECKPOINT_STAGES` still says 1, 3 and 6. The LLM task `acceptance_criteria` is in the medium group (Gemini, Groq, OpenRouter). An answer that skips a task, adds one, or lacks a kind fails the schema and is asked for again. `productfoundry tasks` now carries each task's criteria in JSON and Markdown. The pipeline has 7 stages and stops only at the two checkpoints |
| 12 | Reddit and Product Hunt | **Waiting for the owner**: the phase needs API credentials for Reddit and Product Hunt. Nothing is built. Skipped for now on the owner's instruction to keep building (see "Order change") |
| 13 | API and job queue | Done 2026-10-04, ahead of gate 1 and of phase 12 (see "Order change"). Lint and 412 tests pass. Checked with Docker running and stand-in stages (`FAKE_STAGES=true`, no outside request): a run created with `POST /runs` was picked up by the worker container and stopped at checkpoint 1; `POST /approve` with a competitor removed queued it again, the worker stopped it at the pain-point checkpoint, and after a second approval it completed all 7 stages; the task export was then downloaded over HTTP. The app is `productfoundry.api.create_app` (FastAPI): `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/stages/{key}`, `POST /runs/{id}/approve`, `POST /runs/{id}/resume`, `GET /runs/{id}/exports/{report,prd,tasks}?format=md\|json`, `GET /health`. It creates and approves runs and enqueues; it never calls `resume`, so no stage runs in the API process. `approve` takes a whole `edited_output` or the checkpoint's own edits (`competitors.remove/add`, `pain_points.rename/merge/drop/rank`); an edited pain-point report is checked against the stored evidence as in the CLI. Errors: 404 unknown run or stage, 409 wrong state, 422 invalid output, 400 other. Queue: `jobs.JobQueue` protocol, `RqQueue` (RQ on Redis, queue `productfoundry`) and `RecordingQueue` for tests; the job is `jobs.worker.advance_run`. The worker (`python -m productfoundry.jobs.worker`, the `worker` service) runs with RQ's scheduler and keeps one job scheduled for five minutes after the next UTC midnight that re-queues every `paused_quota` run. New settings: `REDIS_URL` (host port 6380) and `FAKE_STAGES` (worker and API use stand-in stages). `productfoundry serve` runs the API with uvicorn; `productfoundry openapi --out openapi.json` writes the schema, committed at `backend/openapi.json` and checked by a test. CLI, API and worker share `productfoundry/runtime.py` (orchestrator wiring, checkpoint edits, exports). `backend/Dockerfile` builds the worker; on Linux torch comes from PyTorch's CPU index (`tool.uv.sources`), which removed the CUDA packages from `uv.lock`. Not verified: a real (non stand-in) stage in the worker container, and the re-queue job firing at midnight |
| 14 | Frontend: input and checkpoint screens | Done 2026-10-04, ahead of gate 1 (see "Order change"); D4 took its default (single user, no login). `npm run build`, `npm run lint`, `npm run typecheck` and 21 frontend tests pass; backend lint and 412 tests pass. **Full live run in the browser** (`run_9a2e1f9618eb42c9`, Splitwise, review cap 200, real stages in the worker container): the form started the run; stage 1 found 10 competitors and rejected 14; at the competitor checkpoint the 9 others were removed in the browser (to hold the LLM quota) and the list approved; stages 2 and 3 ran; at the pain-point checkpoint pain points 2 and 5 were merged into 1 in the browser (44 reviews after the merge) and approved; stages 4 to 7 completed: a PRD, 10 tasks in 4 epics using the tech stack from the form, and their acceptance criteria. LLM attempts: 38, of which 14 answered (Gemini 3, Groq 9, OpenRouter 2), 1 cache hit, and 24 failed (Gemini 503 or 429, one OpenRouter timeout); stage 1 makes at most 10 searches. Seven screenshots are in `.productfoundry/screenshots/` (not committed: they show the run's data). The app is `frontend/` (Next.js 16, TypeScript, no CSS framework): `/` lists runs, `/new` is the input form with the privacy note, `/runs/[id]` shows progress (polling every 2 s while the worker is busy), says why a run is paused or failed, and shows each output once it exists: competitor checkpoint (remove, add, approve), pain-point checkpoint (rename, merge, drop, approve; counts and quotes that open their source), PRD with citations, tasks in build order with acceptance criteria, and export links. Types are generated from `backend/openapi.json` (`npm run gen:api`), which now also carries the stage output contracts; the three export shapes are typed by hand in `lib/exports.ts`. The browser calls `/api`, which `next.config.ts` forwards to the backend (`BACKEND_URL`), so no CORS is needed. The worker takes `REVIEW_CAP`. **Finding:** on the same 192 reviews and seed the Linux worker found 8 clusters with 21 noise reviews where Windows found 4 with none, so clustering is repeatable on one platform only. Not done: a re-rank control in the browser, and a screenshot of the run list |
| 15 | Review trends and switching intent | Not started |
| 16 | Gate 2: evidence grounding | Not started |
| 17 | Pricing snapshots | Not started |
| 18 | Weekly snapshots and price-change alerts | Not started |
| 19 | Changelog and release tracking | Not started |
| 20 | Traction trend score | Not started |
| 21 | Opportunity panel | Not started |
| 22 | Pricing recommendation | Not started |
| 23 | Gate 3: pricing for 10 products | Not started |
| 24 | Stage 6: RICE prioritization | Not started |
| 25 | Stage 8: GitHub issue sync | Not started |
| 26 | Revenue ranges (experimental) | Not started |
| 27 | Evaluation harness | Not started |
| 28 | Gate 4: full run on the evaluation set | Not started |

### Order change (owner's instruction, 2026-10-04)

Gate 1 needs D2: three products and, for each, the pain points the owner already
knows. Asked for them, the owner instead said to merge and approve the Splitwise
run and to continue building. So the phases after 8 are built while gate 1 is
open. This departs from "the next milestone starts only after the gate passes".
The risk: stages 4 and later are built on pain points whose quality no gate has
confirmed. Gate 1 stays open and is run as soon as D2 is given; live runs of later
phases use the Splitwise run in the meantime.

### Embedding model comparison (phase 6, 2026-10-04)

16 hand-written pairs, each the same complaint in English and in Hinglish
(`tests/fixtures/paired_reviews.json`), scored by
`backend/eval/embedding_models.py`. "Match" is the mean cosine similarity of a
Hinglish review to its English twin; "unrelated" is to the other English
reviews; "top-1" is how often the twin is the nearest English review; "margin"
is the weakest twin minus the strongest wrong match.

| Model | Vectors | Match | Unrelated | Separation | Top-1 | Margin |
|---|---|---|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 (384) | raw | 0.368 | 0.059 | 0.310 | 0.94 | -0.369 |
| | centred | 0.462 | -0.031 | 0.493 | 1.00 | -0.155 |
| **multilingual-e5-small (384)** | raw | 0.849 | 0.785 | 0.063 | 1.00 | -0.053 |
| | **centred** | 0.439 | -0.029 | 0.468 | 1.00 | **-0.046** |
| paraphrase-multilingual-mpnet-base-v2 (768) | raw | 0.411 | 0.120 | 0.291 | 1.00 | -0.125 |
| | centred | 0.469 | -0.031 | 0.500 | 1.00 | -0.058 |

On raw vectors all three models clustered the theme fixture by language first.
After centring, e5-small and mpnet-base recovered the three themes exactly, on
every seed and parameter set tried; MiniLM still split one theme. e5-small was
chosen over mpnet-base: the same result with the best worst-case margin, half
the dimensions, and under half the download (about 470 MB against 1.1 GB).

---

# Milestone 1: Pain-point engine

Outcome: from one command, an incumbent's name and store ids produce a ranked
pain-point report with counts and linked quotes.

## Phase 0: Toolchain and first green build

**Goal.** An empty but working project: tests run, lint passes, the database
starts.

**Starting state.** The repository holds only `CLAUDE.md` and `docs/`. On the
machine: Python 3.11 (`py`), Node 20, Docker and git. `uv` is not installed.

**Do.**
- Install `uv` (confirm with the owner first).
- Create the layout in ARCHITECTURE.md section 4 with empty packages. Do not
  create `frontend/` yet.
- `backend/pyproject.toml` with pydantic, pytest and ruff only. Later phases
  add their own dependencies.
- `docker-compose.yml` with one service: PostgreSQL with the pgvector image.
- `.gitignore` (Python, Node, `.env`, model caches), `.env.example`, a short
  `README.md` with setup and the Gemini free-tier privacy note.
- One trivial test so the test run is not empty.
- `productfoundry --version` works through `uv run`.

**Acceptance.**
- `py -m uv run ruff check . && py -m uv run pytest` passes from `backend/`.
- `docker compose up -d db` starts and `CREATE EXTENSION vector` succeeds.
- `.env` is ignored by git.

**Out of scope.** Any schema, stage or adapter.

## Phase 1: Schemas and the run state machine

**Goal.** The pipeline's contracts and its control flow exist and are proven by
tests, with no database and no LLM.

**Do.**
- In `core`: `RunInput`, and the output models for stages 1 to 3
  (`CompetitorList`, `ReviewSet`, `PainPointReport`) as in ARCHITECTURE.md
  sections 5 and 6. Each has a `schema_version`.
- `RunInput` validation: mode 2 requires an incumbent; platforms and region are
  constrained values.
- In `orchestrator`: the state machine. Run statuses and stage statuses as in
  section 5; a `Stage` protocol; a `RunStore` protocol with an in-memory
  implementation; checkpoints after stages 1 and 3 (and 6, declared now).
- Behaviours: validate each output before saving; stop at a checkpoint;
  approve; approve with an edited output (validated, original kept); resume;
  re-running a stage invalidates later stages; a stage raising
  `QuotaExhausted` puts the run in `paused_quota`; any other error fails the
  stage and the run with the error recorded.
- CLI: `productfoundry run --input file.json`, `status`, `approve`, `resume`,
  wired to fake stages for now.

**Acceptance.**
- Unit tests cover every behaviour above using fake stages.
- A test proves a stage cannot write run state (it only returns a value).
- An output that fails its schema fails the stage and is not saved.
- JSON Schema for each model can be exported with a CLI command.

**Out of scope.** Real stages, storage, LLM calls. Models for stages 4 to 8.

## Phase 2: Storage

**Goal.** Runs survive a restart, and reviews have a home.

**Do.**
- SQLAlchemy models and the first Alembic migration for `runs`,
  `stage_outputs`, `products`, `reviews` (with a pgvector column), `clusters`,
  `cluster_reviews`, `llm_calls`, `provider_usage`.
- A Postgres `RunStore` that passes the same test suite as the in-memory one.
- `ReviewRepository`: upsert by (source, source review id), fetch by product,
  fetch newer than a date. No username column exists.
- CLI uses the Postgres store by default; `--memory` keeps the in-memory one.

**Acceptance.**
- The `RunStore` contract tests pass against both implementations.
- A run paused at a checkpoint is resumed by a second process.
- Inserting the same review twice leaves one row.
- Database tests run against the Docker database and are skipped with a clear
  message when it is not running.

**Out of scope.** Market tables (`pricing_snapshots` and others) and
`tracked_issues`; they arrive with their phases.

## Phase 3: LLM gateway

**Goal.** One function that returns a schema-valid object from whichever free
provider is available, and records who produced it.

**Needs from the owner.** API keys for Gemini, Groq and OpenRouter in `.env`.
The phase can be built and tested without them; only the live check needs them.

**Do.**
- `llm.complete(task, messages, schema)` over LiteLLM.
- A routing table in one config file: the three task groups of ARCHITECTURE.md
  section 7, each with its provider chain and a pinned model id per provider.
  Look up current free-tier model ids and limits before pinning; do not rely on
  memory.
- Failure handling exactly as section 7: 429, timeout, server error and schema
  failure all count; retry once with backoff, then the next provider.
- Quota tracker: daily requests and tokens per provider in `provider_usage`;
  skip a provider near its limit; raise `QuotaExhausted` when all are out.
- Provenance row in `llm_calls` for every call, including cache hits.
- Cache keyed on (task, model, prompt hash).
- A `FakeProvider` for tests and a record/replay mode that saves a live
  response as a fixture.

**Acceptance.**
- Unit tests with fake providers: fallback order per task group; one retry
  before moving on; schema-invalid output triggers fallback; a provider near
  its limit is skipped without being called; all exhausted raises
  `QuotaExhausted`; a repeated call is a cache hit and makes no provider call.
- `productfoundry llm check` makes one small live call per provider and prints
  provider, model and latency, never the key.

**Live budget.** At most 6 LLM calls (2 per provider).

**Out of scope.** Bring-your-own-key, streaming, paid models.

## Phase 4: Stage 1: competitor research

**Goal.** An incumbent or an idea becomes a reviewed competitor list.

**Needs from the owner.** D1: the search API and its key.

**Do.**
- `sources/search`: a `SearchProvider` protocol, the chosen implementation and
  a fake.
- `sources/google_play` and `sources/app_store`: lookup only (find an app's
  store id and metadata by name). Review fetching is phase 5.
- `stages/s1_competitors`: build search queries from `RunInput`, gather
  candidates, then use the gateway (task: competitor filtering) to separate
  true competitors from loosely related products. Each kept competitor has a
  URL, positioning, target users, store ids where found, and the reason it was
  kept. In mode 2 the incumbent is always first in the list.
- The stage plugs into the orchestrator and stops at the checkpoint. The CLI
  prints the list and accepts approve, remove, add, and edit from a JSON file.

**Acceptance.**
- Tests with recorded search results and a recorded LLM response: the output
  validates; rejected candidates are listed with a reason; duplicates of the
  same product are merged; the incumbent is present in mode 2.
- An edited list submitted at the checkpoint is what stage 2 receives.
- One live run for one incumbent, with the result shown to the owner.

**Live budget.** At most 10 search requests and 5 LLM calls.

**Out of scope.** Modes 1 and 3 beyond accepting their input. Market landscape
output.

## Phase 5: Stage 2: review collection

**Goal.** Clean, deduplicated, sentiment-scored reviews for every approved
product are in the database.

**Languages.** English and Hinglish are analysed (ARCHITECTURE.md section
6.1).

**Do.**
- Review fetching in `sources/google_play` and `sources/app_store`, behind a
  `ReviewSource` protocol: paged, rate limited, with a per-product cap set in
  config (start at 2,000 per product per store).
- Cleaning: strip markup, normalise whitespace, drop empty and very short
  reviews, drop usernames and profile links.
- Deduplication within and across sources.
- Language detection stored on every review as `en`, `hinglish` or the
  detected code: script first (Devanagari is `hi`), then a detector, then a
  Hinglish check using a lexicon of Hindi function words in Latin script. Only
  borderline reviews go to the gateway. Reviews outside English and Hinglish
  are stored but flagged as not analysed.
- Cleaning and deduplication must not damage Hinglish text: no spell
  correction, no stop-word removal, no dropping of non-dictionary words.
- Sentiment through the gateway (task: review sentiment), batched, with the
  batch size in config. The prompt states that reviews may be Hinglish. Star
  rating is stored separately.
- Incremental: a second run fetches only reviews newer than the latest stored.
- Stage output `ReviewSet`: counts per product, source, language and
  sentiment, and how many were dropped at each cleaning step.

**Acceptance.**
- Tests against recorded store responses: cleaning, dedup, language flags,
  incremental fetch, and that no username reaches the repository.
- Language detection is tested on a hand-written fixture of at least 60
  reviews (English, Hinglish, Devanagari Hindi, other languages, and very short
  reviews). Hinglish is not labelled as an unrelated language, and the accuracy
  on the fixture is reported.
- The recorded sentiment fixture includes Hinglish reviews.
- Sentiment batching test with a fake provider: N reviews make
  ceil(N / batch) calls; a batch with a malformed answer is retried, not
  silently dropped.
- One live fetch for one product, capped at 200 reviews per store.

**Live budget.** One product, 200 reviews per store, at most 10 LLM calls.

**Out of scope.** Reddit and Product Hunt (phase 12). Switching intent
(phase 15).

## Phase 6: Embeddings and clustering

**Goal.** Stored reviews become stable clusters, with no LLM involved.

**Do.**
- `ml/embeddings`: sentence-transformers, model name pinned in config, batched,
  vectors written to the `reviews` table. Already-embedded reviews are skipped.
- The model must be multilingual. Before pinning it, compare two or three
  candidates on a fixture of paired reviews (the same complaint in English and
  in Hinglish, plus unrelated pairs) and pick the one that separates matching
  from unrelated pairs best. Record the candidates and scores in this file.
- `ml/clustering`: UMAP then HDBSCAN, parameters in config, fixed seed.
  Input is the negative and mixed reviews of one run's products; positive
  reviews are counted but not clustered.
- Post-processing as pure functions: cluster size, share of negative reviews,
  noise share, and representative reviews (closest to the centroid, varied by
  product and date).
- Clusters and memberships saved per run.

**Acceptance.**
- A labelled synthetic fixture (three obvious themes plus noise) is recovered:
  each theme lands mostly in one cluster.
- In a mixed fixture, English and Hinglish reviews about the same complaint
  land in the same cluster; clusters do not split by language.
- The same input and seed give the same clusters twice.
- Fewer reviews than the minimum cluster size gives a clear "not enough
  reviews" result, not an exception.
- The embedding model is downloaded once and cached outside git.

**Live budget.** None. Embeddings are local.

**Out of scope.** Labels, severity, trends.

## Phase 7: Stage 3: pain-point report

**Goal.** Clusters become a ranked, readable, fully cited pain-point report.

**Do.**
- `stages/s3_pain_points`: for each cluster send only its representative
  reviews to the gateway (task: cluster labelling) for a label, a one-line
  description and a severity score with a reason. Junk clusters can be marked
  as such and are listed separately.
- Ranking from cluster size, share of negative reviews and severity. The
  formula is a pure function and is written in the report.
- `PainPointReport`: per pain point the label, severity, review count, share of
  negative reviews, products affected, and 3 to 5 sample quotes as review ids.
- Validator: every quoted review id exists and belongs to the cluster; every
  count matches the database. A report that fails is rejected.
- Labels and descriptions are in English whatever the language of the reviews.
- Export to Markdown and JSON. Quotes are short, each with its source URL and
  date. A Hinglish quote keeps its original words and adds a short English
  gloss marked as a translation.
- The report states how many reviews were English, Hinglish and not analysed.
- Checkpoint: the CLI lets the owner rename, merge, drop or re-rank pain points.
  Merged clusters keep all their reviews.

**Acceptance.**
- Tests with recorded LLM responses: the report validates; a response citing a
  review outside the cluster is rejected; merge and drop at the checkpoint keep
  counts correct.
- The Markdown report shows counts and linked quotes for every theme.
- One live labelling run on the reviews fetched in phase 5.

**Live budget.** At most 30 LLM calls.

**Out of scope.** Trends over time, switching intent, any UI.

## Phase 8: Gate 1: three test products

**Goal.** Show the engine finds what users are known to complain about.

**Needs from the owner.** D2: three products, and for each a short list of
pain points the owner already knows are real (5 to 10 per product). The owner
writes these before seeing the agent's output. At least one product should
have many Hinglish reviews, so the gate tests both languages.

**Do.**
- `eval/gate1/`: the three expected lists as files.
- Run the pipeline through stage 3 for each product from the CLI.
- A small script that lines up expected themes against reported pain points
  (embedding similarity to propose matches; the owner confirms each).
- Record per product: expected themes found, junk clusters, reviews used
  (split by English, Hinglish and not analysed), LLM calls, tokens and wall
  time.
- Tune clustering parameters only if the gate fails, and record every change.

**Acceptance (the gate).**
- For each product, most expected pain points appear in the report, and the
  owner judges the report useful. The numbers and the owner's verdict are
  written in the progress table.
- No report contains a quote that fails to resolve to a stored review.

**Live budget.** Three full runs through stage 3. Report the totals; stop and
ask if a provider's daily quota would be exceeded.

**Out of scope.** The full evaluation harness (phase 27).

---

# Milestone 2: PRD and tasks

Outcome: an approved pain-point report becomes a PRD, a task backlog and
acceptance criteria, reviewed in a browser.

## Phase 9: Stage 4: PRD with citations

**Goal.** A PRD in which every requirement traces to evidence.

**Do.**
- `Prd` model: problem, users, goals, non-goals, requirements, success
  metrics. Each requirement has an id, a statement, a priority hint and
  `evidence`: at least one pain-point cluster id or market gap id.
- `stages/s4_prd` through the gateway (task: PRD). The prompt receives the
  approved pain points with their ids, the competitor list and the full
  `RunInput`, including region.
- Grounding validator: every evidence id exists in this run. A requirement with
  no valid evidence fails the output, which triggers the gateway's fallback.
- Market gaps: until the market panel exists, a gap may only be a competitor
  fact from stage 1, with its own id.
- Markdown export in which each requirement shows its pain points, counts and
  one quote.

**Acceptance.**
- Tests: an invented cluster id is rejected; a requirement with empty evidence
  is rejected; a valid PRD round-trips to Markdown with working citations.
- Grounding is 100% on one live run from a gate 1 product.

**Live budget.** At most 6 LLM calls.

**Out of scope.** Feature-level PRDs for mode 3.

## Phase 10: Stage 5: task breakdown

**Goal.** The PRD becomes epics and tasks as structured JSON.

**Do.**
- `TaskPlan` model: epics; tasks with title, description, requirement ids,
  dependencies (task ids) and an effort estimate in a fixed scale.
- `stages/s5_tasks` through the gateway (task: task breakdown), aware of
  `RunInput.tech_stack` when given.
- Validators: every task maps to at least one requirement; every requirement
  has at least one task; dependencies exist and form no cycle.
- JSON export and a Markdown view ordered by dependencies.

**Acceptance.**
- Tests: a dependency cycle is rejected; an orphan task is rejected; an
  uncovered requirement is rejected.
- One live run on the PRD from phase 9.

**Live budget.** At most 6 LLM calls.

**Out of scope.** Prioritization, GitHub.

## Phase 11: Stage 7: acceptance criteria

**Goal.** Every task has testable Given/When/Then criteria.

**Do.**
- `AcceptanceCriteria` model: per task, a list of criteria with given, when,
  then and a kind (happy path, edge case, failure state).
- `stages/s7_acceptance` through the gateway, batched by epic.
- Validators: every task has at least one happy path and one edge or failure
  criterion; no criterion has an empty part.
- Stage 6 as a pass-through that keeps PRD order, so the pipeline runs end to
  end from stage 1 to stage 7. Its checkpoint is skipped while it is a
  pass-through.
- Criteria added to the task JSON and Markdown exports.

**Acceptance.**
- Tests for the validators and the pass-through.
- One live end-to-end run from stage 4 to stage 7 on a gate 1 product.

**Live budget.** At most 10 LLM calls.

**Out of scope.** RICE (phase 24).

## Phase 12: Reddit and Product Hunt

**Goal.** Products without store reviews still have evidence.

**Needs from the owner.** API credentials for Reddit and Product Hunt.

**Do.**
- `sources/reddit` and `sources/product_hunt` behind `ReviewSource`, using the
  official APIs only, within their rate limits and terms.
- Posts and comments are stored as reviews with source, URL and date, and no
  username.
- A relevance filter (keyword and embedding, then the gateway only for
  borderline items) so off-topic threads are not clustered.
- Stage 2 uses every source that has data for a product.

**Acceptance.**
- Tests against recorded API responses for both sources, including the
  relevance filter and username removal.
- One live fetch per source for one product.

**Live budget.** At most 20 API requests per source and 10 LLM calls.

**Out of scope.** G2, Capterra, Amazon. They are excluded for good.

## Phase 13: API and job queue

**Goal.** Runs can be started, watched and approved over HTTP, and long stages
run in a worker.

**Do.**
- FastAPI app: create a run, get a run with its stage statuses, get a stage
  output, approve or edit at a checkpoint, resume, list runs, download exports.
- RQ with Redis. `docker-compose.yml` gains `redis` and a `worker` service
  that runs the same orchestrator. The API only enqueues and reads.
- `paused_quota` runs are re-queued by a scheduled job after the quota resets.
- The CLI keeps working in-process.
- OpenAPI schema committed so the frontend can generate its types.

**Acceptance.**
- API tests with the in-memory store and fake stages cover every endpoint.
- With Docker running: a run started over HTTP stops at checkpoint 1, is
  approved over HTTP and continues in the worker.
- A run in `paused_quota` shows as paused, not failed.

**Out of scope.** Authentication, multiple users.

## Phase 14: Frontend: input and checkpoint screens

**Goal.** The owner can do a whole run in a browser.

**Needs from the owner.** D4 (default: single user, no login).

**Do.**
- `frontend/`: Next.js with TypeScript; API types generated from the OpenAPI
  schema.
- Screens: structured input form (mode, idea, users, platforms, region,
  incumbent, optional competitors and tech stack); run page with stage
  progress; competitor checkpoint (remove, add, edit, approve); pain-point
  checkpoint (rename, merge, drop, approve) with counts and quotes that open
  their source; PRD view with citations; task list with acceptance criteria;
  export buttons.
- Paused and failed runs say why.
- The privacy note about free-tier providers is visible on the input form.

**Acceptance.**
- `npm run build` and the frontend's tests pass.
- A full run from the form to the task list is done in the browser against the
  local backend, and screenshots of each screen are shown to the owner.

**Live budget.** One full run.

**Out of scope.** Market panel, roadmap view, accounts.

## Phase 15: Review trends and switching intent

**Goal.** The report shows which complaints are growing and why users leave.

**Do.**
- `ml/trends` as pure functions: monthly share of all reviews per cluster;
  growth = share over the last three months versus the three before; rising
  clusters flagged. Months with too few reviews are marked, not plotted as
  zero.
- Switching intent: a keyword and embedding filter finds candidates; the
  gateway (task: switching intent) classifies each as leaving, switched to,
  switched from or considering, and extracts the products named and the reason.
- Outputs added to `PainPointReport` (new schema version): trend series and
  growth per cluster; a switching table (from, to, count, top reasons); and the
  switching reviews of each cluster.
- Frontend: a line chart of the top clusters by month, and a switching filter
  on each pain point.

**Acceptance.**
- Trend maths tested on a hand-built series, including a release spike that
  raises raw counts but not share.
- Switching classification tested with recorded responses.
- A file of 200 reviews from the gate 1 products is prepared for the owner to
  label; precision is reported once it is labelled.

**Live budget.** At most 40 LLM calls.

**Needs from the owner.** Labelling the 200 reviews (can follow the phase).

**Out of scope.** Feeding growth into RICE (phase 24).

## Phase 16: Gate 2: evidence grounding

**Goal.** Prove that requirements cite real evidence, on real runs.

**Do.**
- Run stages 1 to 7 for the three gate 1 products.
- A grounding check that walks every requirement to its clusters and every
  cluster to its reviews, and reports any broken link.
- The owner rates task quality (1 to 5) and marks untestable criteria on a
  sample; results recorded.

**Acceptance (the gate).**
- 100% of requirements cite at least one existing cluster or market gap, on all
  three runs.
- No broken link from requirement to review.
- Owner ratings recorded, and D7 asked.

**Live budget.** Three runs from stage 4 to stage 7, reusing stored reviews
and clusters.

---

# Milestone 3: Market panel

Outcome: in mode 2, the run shows what the incumbent charges, whether it is
growing, what it ships, and where the opening is.

## Phase 17: Pricing snapshots

**Goal.** A public pricing page becomes structured plans.

**Do.**
- `sources/pricing`: check robots.txt, fetch with Playwright, and skip with a
  report when fetching is disallowed.
- Extraction through the gateway from the rendered page text into
  `PricingSnapshot`: plans with name, price, currency, billing period, limits
  and features; free tier and trial flags.
- INR and USD captured separately where the site shows both.
- Validator: every extracted price must appear in the page text. An extracted
  number that is not on the page is rejected.
- `pricing_snapshots` table with the page text hash and fetch time.

**Acceptance.**
- Tests against saved page fixtures for at least three different layouts
  (monthly/annual toggle, per-seat pricing, "contact us" tier).
- A hallucinated price is rejected by the validator.
- A page disallowed by robots.txt is not fetched.
- One live snapshot for one product.

**Live budget.** At most 5 page fetches and 5 LLM calls.

**Out of scope.** Scheduling, change detection.

## Phase 18: Weekly snapshots and price-change alerts

**Goal.** Pricing is tracked over time and changes are flagged.

**Do.**
- A scheduled job in the worker that snapshots every tracked product weekly.
- Diff as a pure function: price up or down, plan added or removed, limit
  changed. An unchanged page hash skips extraction.
- Alerts stored and shown through the API.

**Acceptance.**
- Diff tests on fixture pairs for each change type.
- A changed fixture produces one alert; an unchanged one makes no LLM call.

**Out of scope.** Email or push delivery.

## Phase 19: Changelog and release tracking

**Goal.** A planned differentiator is never one the incumbent already shipped.

**Do.**
- `sources/changelog`: public changelog pages, release-note feeds, app store
  "What's new" notes, and GitHub releases for open-source competitors.
- Weekly, with the pricing job. Items stored in `changelog_items`.
- Matching through the gateway (task: changelog matching): each item mapped to
  pain-point clusters and PRD requirements, or to nothing.
- Alerts: "incumbent shipped a fix for cluster X" and "new feature with no
  match in our roadmap".
- Follow-up: the cluster's trend after the release date, from phase 15.

**Acceptance.**
- Tests with recorded sources and LLM responses for both alert types.
- A match must cite the release item and the cluster; an uncited match is
  rejected.

**Live budget.** At most 10 fetches and 10 LLM calls.

## Phase 20: Traction trend score

**Goal.** A growing, flat or declining label with its reasons.

**Needs from the owner.** D6 (default: no traffic provider).

**Do.**
- `sources/trends` for Google Trends (relative values only), plus signals
  already in the database: download ranges and review-count growth.
- Each signal stored in `traction_signals` with its source and date.
- A pure scoring function that combines the available signals into a trend
  with a medium-confidence label, and says which signals were missing.

**Acceptance.**
- Scoring tests: agreeing signals, conflicting signals, and one signal only
  (which lowers confidence).
- The output always lists its sources.

**Live budget.** At most 10 requests.

**Out of scope.** Traffic estimates without a paid provider. Job postings and
community mentions go to "Notes for later" unless trivial.

## Phase 21: Opportunity panel

**Goal.** One screen answers: is there money here, and is there a gap?

**Do.**
- `MarketPanel` model combining pricing, price history, traction, switching
  table and changelog alerts, each with source and confidence.
- Combined signal: a price increase followed by a rise in the "too expensive"
  cluster's share is flagged, with both pieces of evidence.
- Market gaps get ids so the PRD can cite them (stage 4 now receives the
  panel).
- Differentiation analysis for mode 2: where the incumbent is weak, backed by
  clusters and gaps.
- Frontend panel.

**Acceptance.**
- Tests for the combined-signal rule on fixture data, including the case where
  the complaint rose before the price change (no flag).
- A PRD requirement can cite a market gap id and passes grounding.

**Live budget.** One run.

## Phase 22: Pricing recommendation

**Goal.** A suggested pricing structure with the evidence for each choice.

**Do.**
- Through the gateway (task: pricing recommendation), from competitor
  snapshots, the pricing and plan-limit clusters, target segment and region.
- Output: tiers with price points and limits, free tier or trial, annual
  discount, each with its evidence ids, and where each tier sits against
  competitor tiers.
- For region IN: INR price points, GST-inclusive display and UPI AutoPay
  noted.
- Shown with the line "a starting point, not financial advice".

**Acceptance.**
- Tests: a tier with no evidence is rejected; the positioning view is computed
  from snapshots, not from the LLM.

**Live budget.** At most 6 LLM calls.

## Phase 23: Gate 3: pricing for 10 products

**Goal.** Prove pricing extraction is accurate.

**Needs from the owner.** D5: the 10 products, and their pricing recorded by
hand (plans, prices, currency, period, key limits).

**Do.**
- `eval/pricing/`: the hand-recorded files.
- A field-accuracy script comparing snapshots to them.
- Fix extraction failures found, with a fixture added for each.

**Acceptance (the gate).**
- Field accuracy is near exact across the 10 products; every miss is listed
  with its cause.

**Live budget.** At most 15 page fetches and 15 LLM calls.

---

# Milestone 4: Prioritize and track

Outcome: a prioritized roadmap whose tasks live in GitHub, and a harness that
scores the whole agent.

## Phase 24: Stage 6: RICE prioritization

**Goal.** A transparent, editable score for every roadmap item.

**Do.**
- `Roadmap` model: per item Reach, Impact, Confidence, Effort, the score, the
  source of each number and an explanation.
- Pure scoring: Reach from cluster sizes; Impact from severity times growth
  (phase 15); Confidence from evidence strength (review count, number of
  sources); Effort from task estimates.
- The gateway writes the explanation only. It cannot change a number.
- Checkpoint 3: the user overrides any number; the override is stored beside
  the computed value and the score is recomputed.
- Replaces the pass-through from phase 11. Frontend roadmap view.

**Acceptance.**
- Scoring tests, including overrides and a requirement backed by two clusters.
- An explanation that states a number different from the computed one is
  rejected.

**Live budget.** At most 6 LLM calls.

## Phase 25: Stage 8: GitHub issue sync

**Goal.** Tasks become GitHub issues, and code activity updates the roadmap.

**Needs from the owner.** A GitHub token with access to one test repository.

**Do.**
- `tracking/github` behind an `IssueTracker` protocol, so Jira or Linear can
  follow.
- Create one issue per task with description, acceptance criteria, labels for
  epic and priority, and a link back to the evidence. Idempotent: syncing twice
  creates nothing new.
- A dry-run mode that prints what would be created. It is the default.
- Read linked pull requests and commits; update task status; roll up progress
  per epic and for the roadmap.
- `tracked_issues` table. Frontend progress view.

**Acceptance.**
- Tests against recorded GitHub responses: create, idempotent re-sync, status
  from an open PR, a merged PR and a closed issue.
- One live sync of a small plan to the test repository, after the owner
  confirms the dry run.

**Live budget.** One sync to the test repository only.

**Out of scope.** Jira, Linear, webhooks.

## Phase 26: Revenue ranges (experimental)

**Goal.** A revenue range that never pretends to be precise.

**Do.**
- Sources in order: public filings, founder self-reported figures, marketplace
  listings; otherwise customers times average plan price from snapshots.
- Output is always a range, a confidence label and its basis, for example
  "$20K-80K MRR, low confidence, estimated from downloads and pricing".
- The model cannot hold a single number; the type has low and high only.
- Tagged experimental wherever it is shown.

**Acceptance.**
- Tests: a point estimate cannot be constructed; a range with no basis is
  rejected; missing inputs give "not enough data", not a guess.

**Live budget.** At most 6 LLM calls.

## Phase 27: Evaluation harness

**Goal.** One command scores the agent on everything in ARCHITECTURE.md
section 11.

**Needs from the owner.** For the 10 products of D5: a competitor list each,
and hand-labelled themes for 300 reviews each. This is the largest manual job
in the project; the phase prepares the files and tools for it.

**Do.**
- `eval/` runner: competitor recall, pain-point overlap and junk clusters,
  grounding, pricing field accuracy, switching precision, and tokens, cost and
  wall time per run.
- Results written as JSON and a Markdown summary, compared with the previous
  run.
- Per-provider comparison from `llm_calls`.
- An LLM judge for PRD quality, calibrated against a small set of the owner's
  ratings. It reports its agreement with those ratings.
- Runs from stored data by default, so repeating it costs no source requests.

**Acceptance.**
- The harness runs offline on fixtures in the test suite.
- It runs on whatever labelled products exist and says which are missing.

**Live budget.** Reported before running; the owner approves it.

## Phase 28: Gate 4: full run on the evaluation set

**Goal.** A full run, from input to GitHub issues, passes the evaluation set.

**Do.**
- Run the harness on the 10 products.
- Record the baseline in this file and set the first real targets.
- List the failures and what would fix each, in "Notes for later".

**Acceptance (the gate).**
- Grounding is 100%. The other measures have a recorded baseline and the owner
  accepts them.

---

## After gate 4

Not scheduled. Each becomes a phase when the owner asks for it.

- Mode 1 (new idea): competitor discovery from scratch, market landscape.
- Mode 3 (new feature): the user's product as input, feature-level PRD.
- Bring-your-own-key per user, and paid models.
- Accounts and multiple users (depends on D4).
- Jira and Linear behind `IssueTracker`.
- Hindi in Devanagari and other Indian-language reviews.
- A paid traffic-estimate provider (depends on D6).

## Notes for later

Phases add items here instead of building them.

- Clustering: UMAP and HDBSCAN gave different clusters for the same vectors and seed
  on Windows (CLI) and Linux (worker container). Gate 1 must be run on one of them,
  and the worker is the one the product uses. Pinning the numeric libraries or
  clustering in a fixed container would make runs comparable (noted in phase 14).
- Stage 3: the browser run produced three clusters about the same complaint (the
  daily expense limit), merged by hand. The case for proposing merges is stronger
  (noted in phase 14).
- Gateway: Gemini now also answers 429 within a run, so its real free limit is below
  the placeholder in `routing.toml` (noted in phase 14).
- Frontend: pain points cannot be re-ranked in the browser, although the API takes
  `rank`. Screenshots in the browser pane time out right after a scroll or click and
  succeed on a second try (noted in phase 14).
- Worker: with real stages the container downloads the embedding model on first
  use into the `model-cache` volume; that path has not been exercised (noted in
  phase 13).
- Gateway: Google lists `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash` and
  two Flash-Lite models as stable beside the pinned `gemini-3.8-flash` (models page,
  2026-10-04). Its free-tier availability per model is not published. Since the
  pinned model answered 503 on 9 of 11 attempts on that day, the owner should check
  the AI Studio rate-limit page and pin one that answers. A provider that fails
  twice in a row could also be skipped for the rest of the stage (noted in phase 11).
- Stage 7: some criteria start their parts with a capital ("Given The engine...")
  because the model writes each part as a sentence. Cosmetic (noted in phase 11).
- Stage 4: with one competitor in the run, the only market gap is the incumbent's
  own positioning, and the live PRD cited it for two generic requirements
  ("simple interface", "summary view"). The citation exists, so grounding
  passes, but it is weak evidence. The market panel (phase 21) gives real gaps;
  until then consider not offering the incumbent's own fact as a gap (noted in
  phase 9).
- Stage 3: two Splitwise clusters (71 and 18 reviews) got the same label, the
  daily expense limit. The owner can merge them at the checkpoint; the stage
  could propose the merge itself when two labels match or their centres are
  close (noted in phase 7).
- Stage 3: the junk cluster held 31 of 128 clustered reviews (24%), all vague
  ("Not good anymore"). Judge at gate 1 whether such reviews should be dropped
  before clustering, for example by a minimum length for clustering (noted in
  phase 7).
- Stage 3: App Store quotes link to the app's review page, not to the single
  review, because the feed gives no per-review URL (noted in phase 7).
- Stage 3: a dropped pain point leaves the report; only the kept original
  output shows it existed. A "dropped by the user" list would make the edit
  visible in the export (noted in phase 7).
- Gateway: Gemini answered 503 on 5 of 7 attempts again in phase 7. Cluster
  labelling fell back to Groq twice (noted in phase 7).
- Stage 2: Groq's 200K tokens a day covers about 60 sentiment batches (2,400
  reviews) with `gpt-oss-120b`, which spends about 1,800 output tokens a batch
  on reasoning. Three products at the 2,000 cap need more: try the smaller
  `gpt-oss-20b` for sentiment, or lower the cap (noted in phase 5).
- Stage 2: Apple's feed for the IN store returned no reviews on one request
  and 96 on the next, and they reach back to 2016. Treat App Store counts for
  small storefronts as unreliable (noted in phase 5).
- Tests: the suite takes about 7 minutes on the development machine. UMAP
  compiles for about 3 minutes in every new process (its parallel functions
  cannot be cached), loading the embedding model takes about 1 minute, and
  importing LiteLLM is slow. A `slow` marker would let the quick tests run
  alone (noted in phase 6).
- Clustering: a language with fewer than 10 reviews in a run is centred on the
  overall mean, so a handful of Hinglish reviews among English ones can still
  sit apart. A fixed language direction learned once from a larger sample
  would cover that case (noted in phase 6).
- Clustering: on the theme fixture the unrelated "noise" reviews were absorbed
  into the nearest theme rather than left as noise. Watch the junk share at
  gate 1 (noted in phase 6).
- Stage 2: a store that fails stops the stage. A per-store warning in
  `ReviewSet` would let the run continue (noted in phase 5).
- Gateway: daily usage is counted per UTC day, but Gemini's quota resets at
  midnight Pacific time (noted in phase 3).
- Gateway: `gemini-3.8-flash` answered 503 "high demand" on 4 of 5 live calls on
  2026-10-04. If that continues, pin a less loaded Flash model in
  `routing.toml` (noted in phase 4).
- Stage 1: a competitor with no website in the results and no store page gets
  the URL of the article that mentioned it. Better: look up its homepage
  (noted in phase 4).
- Sources: `google_play_scraper` has no request timeout (noted in phase 4).
- CLI: database commands take about 4 seconds to start on the development
  machine, mostly importing SQLAlchemy (noted in phase 2).
