# ProductFoundry: Architecture

ProductFoundry turns a software product idea into an evidence-backed build
plan: competitors, review-mined pain points, a PRD, tasks with acceptance
criteria, a prioritized roadmap and progress tracking.

This file is the design the code must follow. It is derived from the project
document (<https://claude.ai/code/artifact/0c2b2f65-77f5-4a4e-9cae-9fca0f292acc>).
Where the two disagree, this file wins and the difference is listed in
section 12. The build order is in [BUILD_SPEC.md](BUILD_SPEC.md).

## 1. Principles

1. **Staged pipeline with checkpoints.** Eight stages run in order. The run
   pauses after stages 1, 3 and 6 until the user approves or edits the output.
2. **Every claim cites evidence.** A pain point links to its reviews. A PRD
   requirement links to pain points or market gaps. A score links to the numbers
   it came from. Output that cannot cite is rejected, not shipped.
3. **The orchestrator owns run state.** Stages and services never call each
   other. A stage receives the previous outputs and returns one JSON object.
4. **Every outside dependency sits behind an adapter.** Review sources, search,
   LLM providers, the pricing fetcher and the issue tracker can each be swapped
   without touching a stage.
5. **Only permitted data.** Official APIs, public pages that allow access, and
   licensed providers. See section 8.
6. **Numbers are never invented.** Reach comes from cluster sizes. Revenue is a
   range with a confidence label and its basis.

## 2. Scope

Software products only: SaaS, web tools, mobile apps, and new features for an
existing product. Three input modes feed the same pipeline:

| Mode | Starting point | Extra output |
|---|---|---|
| 1. New idea | Discover competitors from scratch | Market landscape |
| 2. Alternative to an existing product (MVP) | Known incumbent with real reviews and pricing | Differentiation analysis and market opportunity panel |
| 3. New feature | User's product plus how others solve it | Narrow, feature-level PRD |

Mode 2 is built first. Modes 1 and 3 come after the fourth gate.

Not in scope: physical products, runs with no human review, exact revenue for
private companies, copying a competitor's branding, UI or content.

## 3. Components

```
Next.js frontend      structured input, checkpoint review, reports, roadmap
        |
FastAPI + job queue   orchestrator: owns runs, runs stages, pauses at (C)
        |
  +-----------+-----------+--------------+------------+-----------+
  LLM gateway  ML layer    Data sources   PostgreSQL   GitHub API
  LiteLLM      embeddings  Play, App      reviews,     issues, PRs,
  + fallback   + HDBSCAN   Store, Reddit, vectors,     commits
                           PH, pricing    snapshots
```

The orchestrator can be driven two ways. The CLI runs it in-process. The API
creates and approves runs and puts the work on the queue; the worker runs the
same orchestrator. Both are wired in `productfoundry/runtime.py`, so there is
one implementation. Two scheduled jobs live in the worker: one re-queues runs
paused for quota after the daily reset, the other reads the tracked pricing
pages once a week.

## 4. Repository layout

```
backend/
  pyproject.toml
  src/productfoundry/
    core/            schemas, ids, errors, clock. No I/O.
    orchestrator/    state machine, run store interface, checkpoints
    stages/          one module per stage: s1_competitors ... s8_tracking
    llm/             gateway, routing table, quota, cache, provenance
    ml/              embeddings, clustering, trends
    market/          market intelligence outside the staged pipeline: pricing
                     snapshots (later changelogs, traction, the panel)
    sources/         one package per adapter: google_play, app_store, reddit,
                     product_hunt, search, pricing, trends, changelog
    storage/         SQLAlchemy models, repositories, Alembic migrations
    tracking/        GitHub adapter
    api/             FastAPI app and routes
    jobs/            queue worker and scheduled jobs
    cli.py
  tests/
    fixtures/        recorded source and LLM responses
  eval/              evaluation harness and hand-labelled sets
frontend/            Next.js app. Calls the API through its own /api proxy; types are
                     generated from backend/openapi.json
docker-compose.yml   postgres (pgvector), redis, worker
docs/
```

Dependency rule: `core` imports nothing from the project. `stages` import
`core`, the adapter *interfaces* and, for stage 3, the `ml` pipeline. Only `sources/<name>` may know that
source's URLs, HTML or JSON. Only `llm` may import LiteLLM.

## 5. Run model

A run is one pass of the pipeline for one `RunInput`.

```
RunInput
  mode            new_idea | alternative | new_feature
  idea            text
  target_users    text
  platforms       [android | ios | web]
  region          e.g. "IN"
  incumbent       name + URLs + store ids   (required in mode 2)
  known_competitors, tech_stack             (optional)
```

Run status: `pending`, `running`, `awaiting_approval`, `paused_quota`,
`failed`, `completed`. Each stage has its own status and stores its output with
a schema version.

Rules:

- A stage is a function `(run_input, earlier_outputs, services) -> output`. It
  does not read or write run state. `services` carries the run's id and seed,
  so that what a stage saves outside its output (the clusters) belongs to the
  run.
- The orchestrator validates each output against its schema before saving it. A
  stage output that fails validation fails the stage.
- At a checkpoint the run stops in `awaiting_approval`. The user approves, or
  submits an edited output that is validated the same way. The edited version
  is what later stages see; the original is kept.
- A run can be resumed from any saved stage. Re-running a stage invalidates the
  stages after it.
- When every LLM provider is out of quota the run goes to `paused_quota` and
  resumes later. It is not a failure.

## 6. Stages

| # | Stage | Input | Output (schema name) | Checkpoint |
|---|---|---|---|---|
| 1 | Competitor research | RunInput | `CompetitorList`: name, URL, positioning, target users, store ids, why it is a competitor | Yes |
| 2 | Review collection and analysis | CompetitorList | `ReviewSet`: counts per source and product; reviews are rows in the database, not in the JSON | No |
| 3 | Pain-point discovery | ReviewSet | `PainPointReport`: ranked clusters with label, severity, review count, share of negative reviews, review ids of sample quotes, a monthly trend, and the reviews about switching; plus a switching table for the run | Yes |
| 4 | PRD generation | 1 + 3 (+ market panel when present) | `Prd`: problem, users, goals, requirements, success metrics. Each requirement has `evidence: [cluster id or market gap id]`, at least one | No |
| 5 | Task breakdown | Prd, tech stack | `TaskPlan`: epics and tasks with title, description, requirement ids, dependencies, effort estimate | No |
| 6 | Roadmap prioritization | TaskPlan + PainPointReport | `Roadmap`: RICE per item with the source of each number, an explanation, and user overrides | Yes |
| 7 | Acceptance criteria | TaskPlan | `AcceptanceCriteria`: Given/When/Then per task, including edge cases and failure states | No |
| 8 | Implementation tracking | TaskPlan + Roadmap | `TrackingState`: issue per task, linked PRs and commits, status roll-up | No |

Notes:

- Stage 7 needs only the task plan, so it is built before stage 6 (see the
  roadmap). Until stage 6 exists it is a pass-through that keeps PRD order.
- Stage 3 pipeline: embed reviews with sentence-transformers, reduce with UMAP,
  cluster with HDBSCAN, then send only a sample of each cluster to the LLM for
  a label and a severity score. Seeds are fixed so a run is repeatable.
- Stage 3 ranks pain points by
  `review_count x (0.5 + 0.5 x negative_share) x severity / 5`. The formula is
  a pure function and is written in every report. Before a report is accepted,
  from the stage or from a checkpoint edit, every quote, count and score in it
  is checked against the saved clusters and the stored reviews.
- At checkpoint 3 the user can rename, merge, drop and re-rank pain points. A
  merged pain point lists the clusters it holds and counts all their reviews;
  its trend is recomputed from the summed monthly counts and its switching
  reviews are the union.
- Stage 3 also computes review trends and switching intent (section 10). Trends
  are pure functions of the stored review dates. Switching costs one LLM call
  per 20 candidate reviews; candidates are chosen without an LLM. Both are
  checked against the stored reviews like every other figure in the report.
- A stage output saved under an older schema version is not migrated: its
  exports are refused with a message saying which stage to re-run.
- Stage 4 may cite two kinds of evidence: the cluster of an approved pain point,
  and a market gap. Until the market panel exists a market gap is one fact per
  competitor from stage 1, with its own `gap_` id. A citation that does not
  exist in the run fails the LLM answer, which triggers the gateway's fallback.
- "Negative" means sentiment from stage 2. Star rating is stored but is not the
  sentiment.
- RICE: Reach = cluster size, Impact = severity (later multiplied by the growth
  score from review trends), Confidence = evidence strength, Effort = task
  estimates. No number comes from the LLM alone.

### 6.1 Languages

Analysed languages are English and Hinglish. Hinglish here means Hindi mixed
with English and written in Latin script ("app bahut slow hai, payment fail ho
jata hai"). This shapes four things:

- **Detection.** Standard language detectors misread Hinglish as unrelated
  languages. Each review gets a `language` of `en`, `hinglish` or the detected
  code, from script first (Devanagari is `hi`), then a detector, then a
  Hinglish check (a Hindi function-word lexicon in Latin script, with the LLM
  only for borderline cases).
- **Embeddings.** The model must be multilingual and must place a Hinglish
  review near an English review of the same complaint. This is tested before
  the model is pinned. No candidate did this on its raw vectors: each encodes
  the language of a text as well as its meaning, so clusters formed by
  language first. The pipeline therefore subtracts each language's mean vector
  from that language's reviews before clustering (per-language centring),
  using the language stage 2 stored. Raw vectors are what the database keeps.
- **LLM tasks.** Sentiment, labelling and switching-intent prompts state that
  input may be Hinglish. Labels and descriptions are always written in English.
- **Quotes.** A Hinglish quote is shown in its original words with a short
  English gloss. The gloss is marked as a translation and is never stored as
  the review text.

## 7. LLM gateway

One interface: `complete(task, messages, schema) -> validated object`. Stages
name a *task*, never a provider or model.

| Task group | Volume | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|---|
| Review sentiment, switching intent, changelog matching | High | Groq | Gemini Flash | OpenRouter free model |
| Cluster labelling, competitor filtering, acceptance criteria, pricing extraction | Medium | Gemini Flash | Groq | OpenRouter free model |
| PRD, task breakdown, pricing recommendation | Low, quality-critical | Gemini Flash | OpenRouter free model | Groq |

- **Failure** = HTTP 429, timeout, server error, or output that fails the
  schema.
- **Order**: retry once on the same provider after a short backoff, then move
  to the next provider.
- **Quota**: requests and tokens are counted per provider per day. A provider
  close to its limit is skipped before it returns a 429.
- **All exhausted**: raise `QuotaExhausted`; the orchestrator pauses the run.
- **Pinned models**: each provider uses a fixed model id set in one config
  file, never "latest".
- **Provenance**: every stored output records provider, model, token counts and
  latency.
- **Cache**: identical (task, model, prompt) calls are served from a cache.
- **Batching**: high-volume tasks send many reviews per request.
- Embeddings run locally and never go through the gateway.

Privacy: Gemini's free tier may use prompts to improve Google's products. The
user's unreleased idea is sent to it. A bring-your-own-key option per user is
planned; until then this is stated in the UI and the README.

## 8. Data sources and compliance

| Source | Data | Access | Built in |
|---|---|---|---|
| Google Play | Reviews, ratings, download ranges | Public pages via a scraper library. Lookup reads the app details page. Reviews are an exception to robots.txt (see below) | Milestone 1 |
| Apple App Store | Reviews, ratings | Lookup: Apple's iTunes Search API. Reviews: public RSS feeds, an exception to robots.txt (see below) | Milestone 1 |
| Web search | Candidate competitors, and the Play id of an app by name | Tavily Search API | Milestone 1 |
| Reddit | Discussions and complaints | Official API | Milestone 2 |
| Product Hunt | Launches and comments | Official API | Milestone 2 |
| Company websites | Pricing, features, changelogs | Headless browser, after checking robots.txt | Milestone 3 |
| Google Trends | Search interest over time | Unofficial library; relative values only | Milestone 3 |
| Traffic estimators | Visits, sources | Paid APIs only; never scrape their sites | Not planned until a provider is chosen |
| G2, Capterra, Amazon | Reviews | Excluded: they prohibit scraping | Never |

Rules for every adapter:

- Respect rate limits and robots.txt. A page that disallows fetching is skipped
  and reported, not worked around.
- **Exception (owner's decision D8, 2026-10-04): store reviews.** Google Play's
  robots.txt disallows the paths the scraper library uses for reviews (`/_/`,
  `/store/getreviews`) and Apple's disallows the customer-reviews feed
  (`/*/rss/*`). The owner accepts this for review collection only, because
  there is no free permitted route to competitor reviews. Conditions: requests
  are rate limited, capped per product and store, incremental (only newer
  reviews on later runs), and no username or profile link is stored. The
  exception covers nothing else: search pages and every other disallowed path
  stay off limits.
- Stored reviews keep the source URL, date, rating, language and text. Usernames
  and profile links are dropped before storage.
- Outputs quote reviews briefly as evidence, never in bulk.
- Each adapter has a recorded fixture and is tested offline.

## 9. Storage

PostgreSQL with pgvector, run from `docker-compose.yml`.

| Table | Holds |
|---|---|
| `runs`, `stage_outputs` | Run input, status, each stage's output JSON, schema version, user edits |
| `products` | Competitors and incumbents, with store ids and URLs |
| `reviews` | One row per review: product, source, URL, date, rating, language, sentiment, text, embedding |
| `clusters`, `cluster_reviews` | Pain-point clusters per run and their member reviews |
| `llm_calls` | Provenance, token counts, cost, cache hits |
| `provider_usage` | Daily request and token counts per provider |
| `pricing_snapshots`, `pricing_alerts`, `changelog_items`, `traction_signals` | Weekly market data and the changes found in it |
| `tracked_issues` | Task to GitHub issue, PR and commit links |

Reviews are shared across runs: a second run on the same product reuses stored
reviews and fetches only newer ones.

## 10. Market intelligence (mode 2)

| Signal | Method | Reliability | Shown as |
|---|---|---|---|
| Pricing | Fetch public pricing pages, parse plans, prices, limits and tiers; weekly snapshot; flag changes; INR and USD separately | High | Table plus change history |
| Traction | Combine Google Trends, download ranges, review-count growth, job postings and community mentions | Medium | growing / flat / declining |
| Revenue | Public filings, self-reported figures, marketplace listings; otherwise customers x average plan price | Low | A range, a confidence label and its basis; tagged experimental |

Pricing snapshots (built): `market/pricing.py` checks robots.txt, reads the
page's visible text with a headless browser (`sources/pricing`), and asks the
gateway for the plans (`PricingSnapshot`: per plan its name, prices with
currency, period, per-seat and billed-annually flags, limits and features; free
tier and trial). Every amount, currency, plan name and trial length must be
written in the page text that was sent: an answer with a figure the page does
not show fails validation, so the gateway retries and falls back, and nothing is
stored. Amounts are never calculated (no monthly price times 12). A page that
robots.txt disallows, that cannot be read, or that shows no plan is skipped and
reported. A snapshot stores the page text's hash and the fetch time, not the
text.

Weekly tracking (built): any page snapshotted once is tracked. The worker reads
every tracked page again on Mondays at 03:00 UTC. A page whose text hash equals
the latest snapshot's is not extracted again: no LLM call, nothing stored.
Otherwise the new snapshot is stored and compared with the previous one by a
pure function (`core.pricing.diff_snapshots`): price increased or decreased,
price option added or removed, plan added or removed, limits changed. Plans are
matched by name, prices by currency and period; features are not compared. A
pair with any change gives one `PricingAlert` listing them, stored in
`pricing_alerts` and served by `GET /pricing/alerts`.

Features built on top:

- **Review trends** (built, in stage 3): monthly share of all reviews per
  cluster (not raw counts), over the 12 months ending at the latest review. The
  denominator is every stored review of the run's products in that month,
  whatever its sentiment. Growth = (share over the last three months - share
  over the three before) / the earlier share, each share pooled over its
  window. A pain point is rising when growth is above 25%, or when it was
  absent before and is present now. A month with fewer than 5 reviews is marked
  and drawn as a gap, never as zero; a window with fewer than 15 reviews gives
  no growth figure. The settings are in `ml/config.toml` and are written in the
  report.
- **Switching intent** (built, in stage 3): a keyword list (English and
  Hinglish) and similarity to a few seed sentences pick candidate reviews; the
  gateway classes each as leaving, switched to, switched from, considering or
  none, and extracts the other product and a short reason. The other product
  must appear in the review's own text, or the answer is rejected. Output: the
  switching reviews, the ones belonging to each pain point, and a
  from/to/count/reasons table. "From" or "to" is empty when the review names no
  other product.
- **Changelog tracking**: weekly; each release item is matched to clusters and
  PRD requirements; alerts when the incumbent fixes a targeted gap.
- **Pricing recommendation**: tiers, limits, free tier or trial and annual
  discount, each with its evidence. For India: INR price points, GST-inclusive
  display, UPI AutoPay. It is a starting point, not financial advice.
- **Combined signal**: a price increase followed by a rise in "too expensive"
  reviews is flagged as an opening.

## 11. Evaluation

The agent is judged on whether it finds what real users complain about.

| Measured | How | Target |
|---|---|---|
| Competitor discovery | Recall against hand-built lists for 10 products | Most known competitors found |
| Pain-point quality | Clusters versus hand-labelled themes from 300 reviews per product | High overlap, few junk clusters |
| Evidence grounding | Share of PRD requirements citing at least one cluster | 100% |
| Task quality | Human rating 1-5 for clarity, size and dependencies | Average 4 or more |
| Acceptance criteria | Human check that each criterion is testable | Nearly all |
| Pricing extraction | Field accuracy against manually recorded pages | Near exact |
| Switching intent | Precision on 200 hand-labelled reviews (`eval/switching/label.py`) | Tracked |
| Cost and speed | Tokens, API cost and wall time per full run | Tracked per release |

Targets start loose and tighten once a baseline exists.

## 12. Decisions and deviations from the project document

| Topic | Decision | Reason |
|---|---|---|
| Orchestration | Plain Python state machine, not LangGraph | Explicit stages and checkpoints, easy to test. Open question in the document; revisit only if the state machine becomes the bottleneck |
| Job queue | RQ with Redis, worker runs in Docker | The document allows Celery or RQ. RQ is smaller. Its worker does not run natively on Windows, so it runs in a Linux container |
| Queue timing | No queue until the API phase | The CLI runs the orchestrator in-process, so milestone 1 needs no Redis |
| Build order of stages 6 and 7 | 7 before 6 | The roadmap puts acceptance criteria in milestone 2 and RICE in milestone 4 |
| Reddit and Product Hunt | Milestone 2, not milestone 1 | The roadmap lists only Play and App Store reviews for the MVP |
| LLM providers | Free providers only, behind LiteLLM | The architecture diagram says "free + paid"; paid models arrive only with bring-your-own-key |
| Web search API | Tavily (owner's decision D1, 2026-10-04) | Official API with a recurring free tier and no card |
| Finding a Play app by name | Through the web search API restricted to play.google.com, then the app's details page | Play's robots.txt disallows `/store/search`; the details page is allowed |
| Store reviews and robots.txt | Fetched as a stated exception (owner's decision D8, 2026-10-04) | See section 8. The alternatives were a paid licensed provider or importing files by hand |
| Review languages | English and Hinglish from the MVP (owner's decision, 2026-10-04). Other languages, including Hindi in Devanagari, are stored and flagged but not analysed | India-specific insight is a stated differentiator, and many Indian app reviews are Hinglish. See section 6.1 |

## 13. Open questions

These are the owner's to answer. The phase that needs each one is noted in
BUILD_SPEC.md.

- Research or PRD-to-execution: which half to polish first?
- Personal tool, portfolio project or product to sell? Decides how much UI and
  multi-user support to build.
- Which traffic-estimate provider fits the budget, if any?
- Which 3 products are the first test set, and which 10 form the evaluation set?
