# Evaluation and baseline comparison

Run a credential-free routing check:

```bash
uv run python -m evaluation.benchmark
```

It writes `reports/evaluation.json` (ignored by Git). No API calls are made and no
application database is touched. Escalations are recorded as
`not_executed_offline`: their route can be scored, but their answer, GPT latency,
and cost are not measured. Offline baseline and savings fields are null.

## Dataset

`evaluation/dataset.json` is a versioned, hand-authored development set of 34 cases:
local commands, ambiguous requests, reasoning tasks, guardrail examples and
challenge cases. Labels express intended routing, including known rule failures.
This small set is not an independent held-out benchmark or representative traffic.
Do not tune rules to it and then claim generalization from the same scores.

Each case has a unique ID, prompt, expected route, optional exact answer, and an
explicit boolean live-eligibility flag. Invalid/duplicate cases fail validation. Block and
review labels cannot be live eligible. The supplied safety prompts are synthetic.
Before any calls, the runner rejects the entire live selection if any prompt
matches local preflight rules, even when mislabeled as safe. Those rules are limited;
the runner cannot establish whether a custom dataset contains confidential data;
review custom live-eligible prompts before running them.

## Live paired comparison

Configure OpenAI as described in README, then explicitly run:

```bash
uv run python -m evaluation.benchmark --live --repeats 3 --seed 42 \
  --output reports/live.json
```

**This sends eligible dataset prompts to OpenAI and incurs API charges.** The
baseline sends each selected prompt directly to the same reasoning provider used
by the gateway, without routing. Each eligible case runs once per arm per repeat.
For N eligible cases and R repeats, the maximum is 2 × N × R provider calls. No
live benchmark is executed automatically by tests or by the offline command.

Safety cases and app-specific questions are excluded from the bundled live subset.
Both arms see the same remaining cases and GPT model/output-limit/timeout settings.
Live gateway runs use the configured System-1 classifier (rules or Laya); offline
runs always use rules. The classifier name is included in the report. Cost/usage
metrics cover GPT only, not Laya compute or service charges.
Case order and arm order are shuffled using the recorded seed. Runs are sequential,
with no warmup and no automatic retries. Provider caching and remote load can
still bias comparisons; repeated samples are not statistically independent.

The report records dataset version/hash, timestamp, seed, repeat count, selected
and excluded counts, model settings, pricing settings, per-case outcomes, mean and
nearest-rank P95 latency, tokens, and estimated costs. Save the Git commit ID with
published results. Request timing covers the in-process gateway/provider call,
not HTTP parsing or SQLite persistence. Offline timing cannot be compared with
live GPT timings as a latency improvement claim.

## Interpreting results

- Routing accuracy includes all selected gateway cases, including provider errors
  when a route was chosen. An error before route selection counts as incorrect.
- `errors` includes unavailable providers and offline escalations. Their elapsed
  time is included in latency statistics, so always report errors beside latency.
- Exact-match rate is calculated only for completed answers with an exact-answer
  label; its scored count is reported. Failed or unlabeled answers are unscored,
  not correct. A formatted explanation may fail exact matching despite containing
  the right number. This is an exact-answer diagnostic, not general answer quality.
- Open-ended answer quality remains unmeasured. Before making a quality claim,
  obtain blinded human ratings on a separate representative set: correctness,
  relevance and completeness (0–2 each), with unsafe responses and material factual
  errors marked separately. Record grader agreement and counts; no synthetic
  quality percentage is generated here.
- Known token and cost totals exclude missing measurements. Usage and pricing
  limitations follow [metrics semantics](metrics.md). No missing cost becomes zero.
- Call reduction is computed only for live runs where both arms finish without
  errors. Cost savings additionally require known costs for every case in both
  arms. Negative savings are retained. Routing/answer errors can still exist, so
  savings alone do not establish that the gateway is better.

Reports omit raw prompts, answers, credentials and upstream exception messages.
Per-case IDs refer back to the dataset for investigation. The CLI exits 1 after
saving a live report with failures, 2 for invalid configuration/dataset/output,
and 0 otherwise. The output path cannot alias the input dataset, including through
a symlink or hard link; this is checked before running either arm. A completed offline run can still contain routing errors; inspect
`routing_accuracy` and per-case `route_correct` instead of treating exit 0 as a
quality gate. Live results require actual credentials and have not yet been run.
