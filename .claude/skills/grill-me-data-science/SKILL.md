---
name: grill-me-data-science
description: After a general destination grilling, run a data-science-specific second pass to clarify staged workflow, exploration budgets, metrics, and experiment logging. Use when the user has finished grill-destination on a data-science project and says "grill me on data science", or when the project's purpose involves model training, experimentation, or metric-driven iteration.
---

Second-pass grilling that complements `grill-destination`. Run this **after** the general destination interview is complete (or after confirming `autosprint/destination.md` already has the usual sections filled in). The goal is to add data-science-specific content to the **same** `autosprint/destination.md` under the existing `## Desired behavior` and `## Success criteria` sections, and — if the user agrees — a new `## Experiment log` reference to `autosprint/data_science_results.md`.

Data-science projects have a property normal code projects don't: **you don't know in advance what "done" looks like**. The correct model, the correct features, the correct preprocessing — all of those are discovered, not specified. `destination.md` must therefore describe the *process* as well as the outcome.

## Before starting

1. Read `autosprint/destination.md`. If it's missing or has only the seed content, stop and tell the user: "Run `grill-destination` first — this skill layers on top of that one."
2. Read `autosprint/adr.md` if it exists — some staged-workflow decisions may already be recorded there (e.g. "use MLflow for experiment tracking"). Don't re-ask those.
3. Quote the current `## Purpose` and `## Desired behavior` sections back to the user and confirm this is a data-science project before starting. A project that just happens to use numpy is not a data-science project — the skill is for projects where model/method/feature selection is part of the loop.

## Interview style

- **Hard on metrics.** A metric the planning phase can't compute has no ability to drive decisions. If the user says "better accuracy", push for "AUC on holdout set ≥ 0.85".
- **Hard on staged criteria.** "Enough exploration" is meaningless if it's not tied to a number of experiments, a time budget, or a plateau criterion.
- **Soft on stage boundaries being fuzzy.** It's legitimate to say "the planning phase decides when to move from exploration to selection" — the loop can make that call each replan.

## Topics (5)

### 1. Staged workflow — does this project follow one?
- The default staged shape is **explore → select → refine**:
  - **Explore**: try a wide range of approaches cheaply (simple baselines, different feature sets, small model classes).
  - **Select**: narrow to the top 2–3 candidates based on the metric.
  - **Refine**: squeeze remaining performance out of the survivor(s) via hyperparameter tuning, ensembling, more data.
- Not every project fits. If the user's project is "train one model against a fixed recipe", record that and skip stage questions. If it's open-ended discovery, capture the stages explicitly in `destination.md`.
- **Minimum**: either "staged workflow: explore / select / refine" confirmed, or explicit "not staged — single recipe" recorded.

### 2. What counts as "enough" exploration?
- Only ask if stage 1 landed on a staged workflow.
- Options to offer: (a) fixed experiment budget — "run 10 candidate approaches before selecting"; (b) time budget — "explore for 5 sprints, then select"; (c) plateau criterion — "stop exploring when the last 3 candidates don't improve the top score by ≥ 2%"; (d) planning-phase judgement — "the planning phase decides each sprint whether more exploration is warranted based on the experiment log".
- Lean toward (d) for open-ended projects — rigid counts often cut exploration too early on hard problems. But if the user is tight on budget, a fixed count is defensible.
- **Minimum**: one stopping rule chosen or explicit "not staged".

### 3. Metrics — what guides decisions?
- Which metric(s) decide whether one experiment beats another? A single primary metric is ideal; two at most (e.g. accuracy + inference latency).
- Every metric must be computable from a holdout / test set with a deterministic script. "Looks better" is not a metric.
- Ask for concrete thresholds: what value would count as "done enough to ship"?
- **Minimum**: 1 primary metric named + 1 threshold value (or explicit "no ship threshold yet, we'll refine as we go").

### 4. Experiment log — should autosprint maintain one?
- Suggest a dedicated `autosprint/data_science_results.md` that each sprint can append to. Format per-entry:
  - **Sprint / commit hash** — which sprint produced this result.
  - **Method** — 1-line summary ("random forest, 100 trees, depth 8").
  - **Settings** — hyperparameters, feature set, preprocessing.
  - **Metric values** — primary metric + any secondary metrics.
  - **Notes / conclusions** — what we learned; whether this is a keep or a discard.
- Benefits: the planning phase can read the log to decide what to try next; the select stage gets a natural input; nothing gets lost to amnesia between sprints.
- Alternative: external tool (MLflow, W&B). If the user uses one, record that in `adr.md` and skip the autosprint-local log.
- **Minimum**: decision recorded — `data_science_results.md` yes/no, or external tool name.

### 5. Success criteria — what does "shipped" look like for this data-science project?
- Augment the general `## Success criteria` section with DS-specific checkpoints. Examples:
  - "A baseline model achieves primary metric ≥ 0.X on holdout set."
  - "The final model reaches the ship threshold and has a deterministic training script committed to git."
  - "The experiment log contains ≥ N tried approaches, each with a 1-line conclusion."
- **Minimum**: at least 1 metric-tied success criterion added.

## Termination

Once all 5 topics meet their minimum bar, stop asking. Propose the *additions* to `autosprint/destination.md` (diff, not full rewrite) and, if chosen, the creation of `autosprint/data_science_results.md`.

## Writing

**Diff-mode only** — never rewrite the whole file. Show the user:

1. Proposed additions to `## Desired behavior` (staged workflow description, if applicable).
2. Proposed additions to `## Success criteria` (metric-tied checkpoints).
3. A possible new `## Experiment tracking` paragraph that points to `autosprint/data_science_results.md` (or the external tool).

Ask for approval, then write. Keep every addition above the `## AI-generated subgoals` heading — that section stays owned by autosprint's planning phase.

### If the user opted in to `data_science_results.md`

Also propose creating the file with this header template:

```markdown
# Data science results

Append one entry per experimental sprint. Autosprint's planning phase reads this log each replan to decide whether to keep exploring or move toward selection / refinement.

## Entry template

- **Sprint / commit**: <sprint-number> / <short-hash>
- **Method**: <1-line summary>
- **Settings**: <hyperparameters, feature set, preprocessing>
- **Metric values**: <primary=X, secondary=Y>
- **Notes / conclusion**: <what we learned, keep-or-discard>

---

_No entries yet._
```

Ask for approval and write.

## Note on downstream consumers

The Plan phase reads `destination.md` and (if present) `data_science_results.md` on every replan. With a staged workflow in play, the team lead's task selection will naturally oscillate between "try a new approach", "evaluate and select", and "refine the survivor". Keep entries concise so the planning prompt doesn't balloon.
