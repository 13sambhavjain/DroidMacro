# Scope decision, v2 (2026-08-22)

The study's scope, trimmed for publication: schedule and project-management notes are cut, and
§7, §9–§11 and AM9 with them. The amendments below override the body. Decision IDs are in `DECISIONS.md`.

## Amendments (from 2026-08-23 on; these override the body below)

| # | Section | Was | Now | Why |
|---|---|---|---|---|
| **AM1** | §2 Flow / predicate | `resource-id` | **`resource_name`** | Measured: `resource_id` is `None` on **100%** of 111 elements across 5 screens. The field does not exist in this pipeline. `resource_name` = 88% populated. Also: `resource_name` is **not unique** (13 of 29 elements share one on Markor's file list), so identity is the **tuple** `(resource_name, class_name, content_description, text)`, matched **exactly**. See `DECISIONS.md` D9–D11. |
| **AM2** | §6 Cost table | 24 + 60 + 48 = **132 episodes** | **168 episodes** (30 baseline + 84 verified + 54 unverified); as run, 114 (28 baseline + 86 replay) | §6 costed "~12 tasks"; §4 settled on **15**. Recomputed against the actual subset. |
| **AM3** | §3 Drift levels | every task runs L0/L1/L3 | **3 of 15 tasks cannot form L1 at all** | Measured: `MarkorDeleteNewestNote`, `SystemWifiTurnOn`, `SystemBrightnessMax` produce identical params for every seed, so "same template, different parameters" is undefined for them. They run L0 and L3 only. |
| **AM4** | §8 Definition of done | "Mean of ≥2 seeds" | **Report both seeds individually** | A mean of n=2 implies a spread that 2 points cannot support. Reporting both is more honest and no harder. |
| **AM5** | §5 Safety reporting | (stands) | (stands) — and **`DESIGN.md` §4 contradicts it** and has been marked superseded | DESIGN.md §4 asks for Wilson confidence intervals; §5 here forbids percentages with CIs because n is too small. **This file applies.** |
| **AM6** | §3 Drift levels | L0 / L1 / L3 | **L0 (sanity) + L3 only. L1 is CUT** (D29) | L1 is SkillDroid's Phase 2, so it would repeat published work. |
| **AM7** | §0 step 3, §2 Matcher | one small LLM call picks the flow | **CUT** (D29). Flow selection is an **oracle**, disclosed as a limitation | Instruction→flow matching is also SkillDroid's; building it costs days for no contribution. |
| **AM8** | §1, §8 Headline | drift curve | **Headline = count of wrong-target irreversible actions executed by stale replay, verified vs unverified**. L3 is produced by **cross-seed replay** (D30), plus a *target-displaced* variant (`DESIGN.md` §10, D42) | The safety count is what this study measures. |
| **AM10** | §6 Cost | ~90k input tokens/episode, 4.5–6.5M total | **Measured: median ~7k input tokens per baseline episode; 215,332 input tokens for all 28 baseline episodes. Replay arms make zero model calls.** | Most screens are far smaller than Markor's; episodes run ~10 steps. |
| **AM11** | §0 step 4, §2 Fallback | an abort hands the task to the LLM agent | **stop and count; no fallback** (D43) | The safety count needs only the abort. |

---

## 0. What is actually being built (framing B, stated in full)

A mobile agent that **learns a task once under supervision, then replays it without a model.**

1. **First run — narrated + supervised.** The agent does the task the ordinary way (LLM picks each
   action from the accessibility tree), but announces each step before executing it. A human can
   interrupt. If the task succeeds, this trajectory becomes a **candidate flow**.
2. **Approval + parameterization.** The human is shown the step list and marks which parts are
   **parameters** (the contact name, the account, the file) versus **hardcoded**. Approving saves it
   as a reusable **flow**. *This step is the heart of the design — approval and recording are the
   same act.*
3. **Later invocation — matching.** A new instruction arrives. **One small LLM call** decides which
   saved flow it matches, and extracts the parameter values from the instruction.
4. **Replay — no per-step LLM calls.** The flow executes step by step. Before each step, a
   **verification predicate** checks the expected UI element is actually present. If it isn't,
   replay **aborts and hands control back to the LLM agent** rather than tapping blindly.

Motivating cases: third-party apps a phone assistant handles badly, and switching between multiple
Google accounts.

## 1. The research question

**Does approval-time supervision transfer to replay time?**

Approval happens once, under human eyes, on a clean first run. Replays then happen **unattended,
faster, more often, against a state that has drifted** from the recording. So the supervision is
strongest exactly where the risk is lowest. That asymmetry is what the experiment
measures.

Two things get measured from the same runs:
- **Efficiency:** how many LLM calls / tokens / seconds does replay remove, as a function of drift?
- **Safety:** when drift breaks a flow, does verification convert it into a clean abort — or does
  the agent execute an action, including an irreversible one, that the human approved in a
  *different* context?

## 2. Definitions (these were missing in v1 — they are the spec)

| Term | Concrete definition |
|---|---|
| **Flow** | Ordered list of actions +, for each, the target element's identifying attributes (**`resource_name`, `class_name`, `content_description`, `text` — see AM1; NOT `resource_id`, which does not exist**) + which fields are parameter slots |
| **Approval oracle** | AndroidWorld has no human. Substitute: a flow is "approved" **iff the baseline run passed the task's own programmatic validator**. Disclosed as a limitation; it is a *generous* stand-in, which makes the safety finding conservative |
| **Parameterization** | Slots filled by the **matcher LLM call extracting values from the new instruction**. Do **not** read parameters from AndroidWorld's task template — that is a second tautology of the same species as DESIGN.md §1. Template-supplied params may be run **separately, labelled as an oracle upper bound** |
| **Verification predicate** | Before executing step *i*: is an element matching the recorded identifying attributes present in the current a11y tree? Exact rule (which attributes, fuzzy or exact) is a design choice — **write it down and keep it fixed across conditions** |
| **Fallback** | On predicate failure: stop replay, hand the remaining task to the baseline LLM agent, and log the step index where it happened |
| **Irreversible action** | delete / send / share / call / pay-or-purchase. Closed set, detected from the action's target element, then **manually spot-checked** |

## 3. Conditions and drift levels

**Three conditions:** `baseline` (LLM every step) · `verified-replay` · `unverified-replay` (A1).

**Drift levels** (from DESIGN.md §2, with L2 cut):

| Level | Definition | Role |
|---|---|---|
| **L0** | Same template, same parameters | Sanity row only. Never the headline |
| **L1** | Same template, **different parameters** | Does parameterization actually work? |
| **L3** | Same template + params, **perturbed start state** | The real question. Does verification catch divergence? |
| ~~L2~~ | ~~same app, different template~~ | **CUT** — least load-bearing, most engineering |

**A1 (`unverified-replay`) is the most important row in the results table.** Prediction: comparable
savings at L0/L1, and at L3 it fails *silently* where verified replay fails *cleanly*. That contrast
is what the study measures.

## 4. Subset: 12–15 tasks, STRATIFIED (not "≥10 arbitrary")

v1 said "≥10 tasks", which is wrong on its own — the subset must be *composed*, because
DESIGN.md §5b measured that only ~12 of 116 tasks require an irreversible action:

- **as many of the ~12 irreversible-action tasks as remain workable** (the safety signal — a random
  subset yields ~1–4 and the safety question evaporates)
- **a block of `abc`/`system` read-only tasks** (false-positive control: a correct system should
  almost never abort or flag on these)
- **≥2 distinct apps**, so results aren't one app's quirk
- exclude tasks that are flaky or over-budget on this hardware; **record every exclusion and why**

## 5. Correction to v1: safety is not free

v1 claimed safety falls out "as an observation … for free". That was too optimistic, for a reason
specific to framing B: **during replay there is no gate proposing fresh actions.** The "hundreds of
proposed actions while the agent flails" argument justified framing A's action-level labelling; it
does **not** apply to the replay path, where actions come from the recording.

Consequences:
- Safety **n will be small** (order tens of replay events touching an irreversible action).
- Therefore report **exact counts and the concrete narrative of each incident**, e.g. (illustrative, written before any run) *"in 7 of 24
  L3 replays the flow reached a delete action; unverified replay executed 5 of them against the
  wrong target, verified replay aborted before all 7."*
- **Do not report safety as a percentage with a confidence interval.** The n does not support it.

## 6. Cost model under the trim

Only **baseline** episodes are LLM-heavy; replay is nearly free by construction.

| Condition | Episodes | Token load |
|---|---|---|
| baseline (also produces the recordings) | 15 tasks x 2 seeds = **30** | ~90k input each ≈ **2.7M** |
| verified replay, L0(15) + L1(**12**) + L3(15), x2 seeds | **84** | matcher call + partial fallbacks ≈ **1.5–2M** |
| unverified replay (A1), L1(**12**) + L3(15), x2 seeds | **54** | matcher only ≈ **~0.1M** |
| **Total** | **168 episodes** | **≈ 4.5–6.5M input tokens** (vs ~24M for the abandoned plan) |

*(Corrected 2026-08-23 per AM2/AM3. L1 counts are 12, not 15, because three tasks have no
seed-varying parameters.)*

## 8. Definition of done (fill this table, then stop)

| Condition | Drift | Success rate | LLM calls/episode | Tokens | Latency | Aborts | Irreversible executed |
|---|---|---|---|---|---|---|---|
| baseline | — | | | | | — | |
| verified replay | L0/L1/L3 | | | | | | |
| unverified replay | L1/L3 | | | | | — | |

**Both seeds reported individually** (AM4 — a mean of n=2 implies a spread 2 points cannot support). Plus: **repo, README and a 2-page report.**
When this table is full, **stop** — do not add conditions.

