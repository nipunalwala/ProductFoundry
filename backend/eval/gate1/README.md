# Gate 1: three test products

The gate asks whether the pain-point engine finds what users are known to
complain about (docs/BUILD_SPEC.md, phase 8).

## 1. The owner writes what to expect

One file per product in `expected/`, written **before** looking at any output
for that product. Copy `expected/TEMPLATE.json` to `expected/<slug>.json`:

- `product`: the product's name.
- `run_input`: the `RunInput` the run is started with.
- `expected`: 5 to 10 pain points you already know are real, one short sentence
  each, in English.

At least one product should have many Hinglish reviews.

## 2. Run the pipeline through stage 3

From `backend/`, for each product:

```
py -m uv run python eval/gate1/line_up.py input <slug>
py -m uv run productfoundry run --input eval/gate1/inputs/<slug>.json
py -m uv run productfoundry approve RUN_ID     # the competitor checkpoint
```

The run stops at the pain-point checkpoint.

## 3. Line up and confirm

```
py -m uv run python eval/gate1/line_up.py propose <slug> RUN_ID
```

writes `results/<slug>.json`. For each expected theme it proposes the nearest
reported pain points by embedding similarity. Open the file, set every
`confirmed` to `true` or `false`, and write your verdict in `owner_verdict`.
The similarity numbers sit in a narrow band (an unrelated pair still scores about
0.8), so compare the candidates with each other rather than reading one number.

```
py -m uv run python eval/gate1/line_up.py summary
```

prints, per product: expected themes found, junk clusters, reviews used (English,
Hinglish, not analysed), LLM calls, tokens, wall time, and how many quotes fail
to resolve to a stored review (the gate needs zero).

Expected and result files hold no review text, so they are committed.
