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
10. **Compliance.** Check robots.txt before fetching a page. Drop usernames
    before storing a review. Never add a source that ARCHITECTURE.md section 8
    excludes.
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
| D1 | Which web search API for competitor discovery? It must be an official API with a free tier | Phase 4 | None: phase 4 asks |
| D2 | Which 3 products form the first test set? Mobile apps with many store reviews work best | Phase 8 | None: phase 8 asks |
| D3 | Which review languages are analysed in the MVP? | Phase 5 | **Answered 2026-10-04:** English and Hinglish (Hindi-English in Latin script). Other languages are stored and flagged, not analysed |
| D4 | Personal tool, portfolio project or product to sell? | Phase 14 | Single user, no accounts, no login |
| D5 | Which 10 products form the evaluation set? | Phase 23 | None: phase 23 asks |
| D6 | A paid traffic-estimate provider? | Phase 20 | None. Traction is built without traffic data |
| D7 | Which half to polish first: research or PRD-to-execution? | After gate 2 | Follow the roadmap order |

## Progress

| Phase | Title | Status |
|---|---|---|
| 0 | Toolchain and first green build | Done 2026-10-04. Lint and 1 test pass; `productfoundry --version` prints 0.1.0; the database container starts healthy and `CREATE EXTENSION vector` succeeds (pgvector 0.8.7 on PostgreSQL 17). uv is run as `py -m uv` because pip installed it outside PATH. The database is on host port 5433 because a native PostgreSQL already uses 5432 on this machine |
| 1 | Schemas and the run state machine | Done 2026-10-04. Lint and 96 tests pass. All four schemas are v1. `approve` only records the approval; `resume` continues the run (the CLI does both). A stage that raises `QuotaExhausted` goes back to `pending`, so `resume` retries it. The CLI keeps runs in `.productfoundry/runs.json` (`--state-file`), a JSON dump of the in-memory store, so `status`, `approve` and `resume` work across commands until phase 2. `RunStore` contract tests are in `tests/test_run_store.py`, parametrised by store: phase 2 adds Postgres to the `store` fixture. `CompetitorList` already has `rejected` (needed in phase 4) and `ReviewSourceName` already lists Reddit and Product Hunt, to avoid a version bump later |
| 2 | Storage | Not started |
| 3 | LLM gateway | Not started |
| 4 | Stage 1: competitor research | Not started |
| 5 | Stage 2: review collection | Not started |
| 6 | Embeddings and clustering | Not started |
| 7 | Stage 3: pain-point report | Not started |
| 8 | Gate 1: three test products | Not started |
| 9 | Stage 4: PRD with citations | Not started |
| 10 | Stage 5: task breakdown | Not started |
| 11 | Stage 7: acceptance criteria | Not started |
| 12 | Reddit and Product Hunt | Not started |
| 13 | API and job queue | Not started |
| 14 | Frontend: input and checkpoint screens | Not started |
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

Nothing yet. Phases add items here instead of building them.
