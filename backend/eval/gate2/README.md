# Gate 2: evidence grounding

The gate asks whether every PRD requirement cites real evidence, on real runs
(docs/BUILD_SPEC.md, phase 16). It uses the three gate 1 products.

## 1. Run stages 4 to 7

For each gate 1 product, approve its pain-point checkpoint. The run continues to
the acceptance criteria, reusing the stored reviews and clusters:

```
py -m uv run productfoundry approve RUN_ID
```

## 2. Walk the citations

```
py -m uv run python eval/gate2/check.py grounding <slug> RUN_ID
```

walks every requirement to the pain points and market gaps it cites, every pain
point to its saved clusters, and every cluster and quote to the stored reviews.
It writes `results/<slug>.json` and lists every broken link. The command fails
when a requirement cites nothing that exists or any link is broken.

## 3. Rate the tasks

```
py -m uv run python eval/gate2/check.py sample <slug> RUN_ID [--tasks 10]
```

writes `ratings/<slug>.json`: a sample of tasks with their acceptance criteria.
For each task set `rating` from 1 (unusable) to 5 (ready to build), judging
clarity, size and dependencies. For each criterion set `testable` to `true` or
`false`. `owner_notes` is free text.

```
py -m uv run python eval/gate2/check.py summary
```

prints, per product: requirements grounded, broken links, the average rating and
the number of untestable criteria. The gate needs 100% grounding and no broken
link on all three runs.

Result and rating files hold no review text, so they are committed.
