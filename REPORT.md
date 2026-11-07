# What a stale replay destroys: checked and blind replay of recorded Android agent runs

Sambhav Jain · Independent study, August 2026 to present · Interim report, 7 November 2026

**Status: interim.** The study is ongoing. The L0 and L3a counts are final. The L3b counts and the
baseline success rate are provisional, because two pending items could change them (§6). Every count in the tables
below comes from `code/runs/results.md`, which `code/flowagent/analyze.py` generates from the raw
traces, and a test pins the wrong-target counts and the derived figures. Incident details not in that
file come from the raw traces in `code/runs/`.

## 1. Question

An agent's successful run on an Android app can be recorded, approved and replayed later with no model
calls. The mechanism is published work (SkillDroid [2], PreAct [3], AgentRR [4]), and so is checking a
step's preconditions before acting (SkillDroid's guard conditions, AgentRR's check functions). AAPT (Adaptive
Anticipatory Policy Trees) [5] compares guarded with open-loop execution on a synthetic benchmark where
a decision goes stale within milliseconds, and reports no incorrect actions under its guard. This study replays recordings on real
apps after the screen has changed, and asks what each replay destroys with and without a check before
each step. A check is expected to beat a blind press; what is measured is how often each arm
destroys the wrong object under each kind of drift, and what the check still lets through.

The main measure is the number of **wrong-target irreversible actions executed**. Counts are small, so
they are given exactly and each incident is described; no percentages or confidence intervals are used.

## 2. Setup

- **Benchmark:** AndroidWorld [1] on a Pixel 6, API 33 emulator, on a MacBook Air (M2, 8 GB RAM). It has
  not been tried on another machine. There is one deviation: longer app-launch and install timeouts
  (`patches/`), because a warm app launch on this machine took about 5.6 s against the benchmark's 5 s limit.
- **Recordings:** a baseline LLM agent ran a stratified 15-task subset on 2 seeds. Of 30 planned episodes,
  28 ran and 7 succeeded, which gave 7 flows: 3 delete tasks × 2 seeds, plus one Wi-Fi task. A run counts
  as approved if it passed the benchmark's validator, and a fixed trim rule stands in for a person
  reviewing it (D41).
- **Checked replay** (`verified` in the code) acts only if the app is the one the flow opened and the
  recorded element is on screen exactly once. It waits up to 6 s, then aborts and counts the abort, with
  no fallback (D43).
- **Blind replay** (`unverified` in the code) presses the recorded index. Both arms wait under the same
  screen-settle rule (D52). Neither calls a model; that is by design, not a measured result.
- **Ground truth:** the device's files or calendar events are listed before and after every episode. A
  wrong-target action means something other than the intended object disappeared (D44).

| Drift | Device state | Episodes per arm |
|---|---|---|
| L0 | Same seed as the recording | 6 |
| L0clean | L0 for Markor, with a stray `.app` folder removed | 2 |
| L3a | Recorded target absent: each flow replayed on 5 other device seeds | 30 |
| L3b | Target displaced: same seed, 2 decoys sorted above it (Files and Markor only) | 4 |

## 3. Results

### Wrong-target irreversible actions, delete tasks

| Drift | Arm | Episodes | **Wrong target** | Intended only | Nothing destroyed |
|---|---|---|---|---|---|
| L0 | blind | 6 | 1 | 5 | 0 |
| L0 | checked | 6 | 0 | 5 | 1 |
| L0clean | blind | 2 | 0 | 2 | 0 |
| L0clean | checked | 2 | 0 | 2 | 0 |
| L3a | blind | 30 | 17 | 0 | 13 |
| L3a | checked | 30 | 3 | 0 | 27 |
| L3b (provisional) | blind | 4 | 4 | 0 | 0 |
| L3b (provisional) | checked | 4 | 0 | 1 | 3 |

All 25 incidents are listed one by one in `code/runs/results.md`.

### L3a, target absent

At L3a the recorded target is absent, so aborting is the correct outcome for the checked arm, and its
aborts are expected by design (D42). What L3a measures is what each arm destroys instead.

| App | Blind wrong | Checked wrong | Checked aborts |
|---|---|---|---|
| Files | 0/10 | 0/10 | 10 |
| Markor | 7/10 | 0/10 | 10 |
| Calendar | 10/10 | 3/10 | 7 |

Files at 0/10 blind is not a safety result. The recorded long-press landed on the status bar every time
(5 times on the clock, 5 on the battery indicator), so nothing was selected. Why the layout differs across
seeds is not established.

5 of Calendar's 10 blind wrong deletes, all from the seed-1 flow, went wrong before the absent target
mattered: a `Go to today` button sat at `Change view`'s recorded index, as in the L0 incident below.

The 3 checked wrong deletes all hit an event with the right title at a different time. 8 of the 10 calendar
devices held another event with the target's title. Times are UTC.

| Device seed, flow | Intended | Destroyed |
|---|---|---|
| seed 5, source seed 1 | Workshop on Project X, Thu 19 Oct 18:00 | same title, Thu 19 Oct 23:00 |
| seed 6, source seed 1 | Workshop on Project X, Thu 19 Oct 18:00 | same title, Mon 16 Oct 23:00 |
| seed 4, source seed 2 | Workshop on Annual Report, Mon 16 Oct 02:00 | same title, Wed 25 Oct 05:00 |

Both instructions name the title, date and hour. The recorded fingerprint kept only the title.

### L3b, target displaced (provisional)

The decoys shift the list, so something else sits at the recorded position and blind wrong deletes are
expected by design. What L3b tests is whether checked replay finds the moved target.

| Flow | Blind destroyed | Checked outcome |
|---|---|---|
| Files, seed 1 | decoy `DAUC_great_koala.jpg` | abort: target off-screen, and the flow has no scroll |
| Files, seed 2 | neighbour `early_alarm_2023_09_21.mp3` | completed correctly, the only one of 4 |
| Markor, seed 1 | decoy `DAUC_great_koala` | abort: found the right note, but the selection toolbar never appeared |
| Markor, seed 2 | neighbour `2023_09_25_research_paper_summary.md` | abort: same |

### L0, no planned drift

Blind replay destroyed `Meeting with Dr. Smith` on Calendar seed 1, because a `Go to today` button now sits
at `Change view`'s recorded index. This reproduced in both passes. The one checked abort was Files seed 1:
"Show roots" did not open the drawer, and the same flow completed in the blind arm. The Wi-Fi flow is
reversible and outside the safety count: checked completed with validator failure, blind with validator
success.

### Time

Medians on delete tasks, on this host, with the model served by Groq at about 1.6 s per step.

| Condition | Episodes | Wall (s) | Steps | Model (s) | Observe and settle (s) | Act (s) | Per step (s) |
|---|---|---|---|---|---|---|---|
| Baseline, successful runs | 6 | 47.5 | 10 | 15.8 | 17.6 | 5.2 | 4.2 |
| Checked, L0 | 6 | 36.9 | 5 | 0 | 21.1 | 6.1 | 5.0 |
| Blind, L0 | 6 | 36.9 | 6 | 0 | 20.5 | 5.1 | 4.3 |

**Price of the check:** median +0.71 s per step over 5 flows paired at L0 (range −0.10 to +1.77 s), leaving
out the flow whose checked arm aborted.

### Baseline (provisional)

7 of 28 episodes succeeded on `openai/gpt-oss-20b` via Groq: irreversible 6/13, reversible 1/8, read-only
0/7. The 7 read-only episodes could not pass, because they are scored from an `answer` action that the agent
prompt did not offer. Excluding them gives **7/21**. The other tasks were not audited for similar harness
problems.

## 4. Findings

1. **In this setup, blind replay destroyed the wrong object under drift:** 17/30 with the target absent (5 of them
   through the toolbar change also seen at L0), 4/4 with it displaced, and 1/6 with no planned drift.
2. **Checked replay reduces this but is not safe by construction:** 3/30. It deleted a same-titled event
   because the recording kept less than the instruction said. An earlier scoring rule matched events on
   title alone and hid these 3. Re-scoring by title and start time exposed them (D56), and the raw results
   were not edited.
3. **Checked replay made no wrong deletes under displacement, but mostly aborted:** 0/4 wrong, and 1/4 completed. One target was
   off-screen, and on two the Markor toolbar did not appear. That cause is still open (§6).
4. **The benchmark's validator was not usable as the safety metric here.** It counted 3 of the 17 blind wrong deletes
   at L3a as successes. In each, the destroyed object was that device seed's own task target: replay sets up
   the task from the device seed but replays another seed's recording, so the validator was correctly
   scoring a different task. The inventory diff is the measure instead.
5. **Replay still spends its time waiting for the screen.** Model time is replaced by screen settling. Whole-task
   time falls from 47.5 s to 36.9 s mostly because trimmed flows have about half the steps. Per step, checked
   replay is slower than the agent (5.0 s vs 4.2 s) under this settle rule.
6. **A relaxed predicate changed nothing measured:** it agreed with the strict one on 126 of 126 checked
   steps (D47).

## 5. Limitations

- Small n: L3b has 4 episodes per arm and L0 has 6, and Calendar was not run at L3b.
- The 30 L3a episodes per arm are 6 flows, each replayed on 5 device seeds, so they are not 30
  independent samples. Outcomes cluster by app (blind wrong target: Files 0/10, Markor 7/10, Calendar
  10/10), so the per-app counts say more than the totals.
- Flows: they come from one model and are trimmed by a rule, written after seeing the 7 flows but before
  any replay, not approved by a person (D41). Flow
  selection is an oracle, and there is no parameterization (AM6, AM7). There is no fallback after an abort
  (D43).
- The harness changed during the runs. Four problems were found and fixed before reporting: asynchronous
  deletes (D50), unequal settling between arms (D52), the stray `.app` folder (D55) and calendar identity
  (D56). The earlier passes are kept in `code/runs/quarantine/` and are not reported.
- Markor at L3 is a controlled condition: `.app` is removed there (D60). Natural drift
  is reported at L0, and it does not reproduce reliably: `.app` appeared in 4/4 Markor episodes in one pass
  and 1/4 in the next.
- The calendar re-score rebuilds each device's events from its seed. The rebuild was consistent with the
  run's own record on all 24 re-scored episodes.
- Exclusions: bulk deletes are excluded by design (D64). Wi-Fi appears only as an example of unlabelled
  targets: 2 of its 8 targeted steps carry a label.
- Time figures depend on this host, this provider and the settle rule (6 s timeout, 0.6 s poll), which
  was chosen for fairness between arms, not speed.
- Prior work was last searched in August 2026; newer work may exist.

## 6. Pending

| Item | What it could change |
|---|---|
| Probe of the Markor toolbar failure, with outcomes fixed in advance (D61) | If it is a rig bug, all 8 L3b episodes are re-run and reported whatever the outcome. If it is real behaviour, the cause gets named. Otherwise it is called flaky. |
| Read-only re-run with the `answer` action, same model (D65) | The baseline denominator becomes 29, and the false-positive control gains data if any flow succeeds. |

## 7. Tools used

| Purpose | Tool |
|---|---|
| Benchmark and device | AndroidWorld, Android emulator (Pixel 6, API 33) |
| Baseline agent model | `openai/gpt-oss-20b` via Groq |
| Experiment code and tests | Python 3.11, pytest |
| Development support | Claude Code (Anthropic): support with coding, analysis and writing |

## References

1. Rawles et al. *AndroidWorld: A Dynamic Benchmarking Environment for Autonomous Agents.* arXiv:2405.14573, 2024.
2. Chen et al. *SkillDroid: Compile Once, Reuse Forever.* arXiv:2604.14872, 2026.
3. Li. *PreAct: Computer-Using Agents that Get Faster on Repeated Tasks.* arXiv:2606.17929, 2026.
4. Feng et al. *Get Experience from Practice: LLM Agents with Record & Replay.* arXiv:2505.17716, 2025.
5. Dong et al. *Why Are GUI Agents Correct but Late? Decode on the Decision-Time Critical Path, Tested with
   Pre-Compiled Policy Trees.* arXiv:2607.28399, 2026.
