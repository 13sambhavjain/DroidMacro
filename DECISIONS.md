# Decisions cited in this repo

Code comments, `code/runs/results.md`, `DESIGN.md` and `REPORT.md` cite these IDs. They come from the
study's working log, which is not published, so the numbering has gaps. `AM` IDs are the amendments at
the top of `SCOPE_DECISION.md`. `A1`–`A3` in the design notes name ablations, not the `A` rows below.

This repo carries the study's code and data history. The design notes and this file were added in the
latest commit, so early commit messages cite documents that appear only later, and interim numbers (such as
a partial baseline pass) that `REPORT.md` supersedes. Code was committed at the end of each stage of work,
so some commits add a whole component at once.

| ID | Date | Decision | Why |
|---|---|---|---|
| D9 | 2026-08-22 | The predicate uses `resource_name`, not `resource_id`. | `resource_id` was `None` on 100% of 111 elements across 5 screens; `resource_name` was 88% populated. |
| D10 | 2026-08-22 | The predicate matches exactly, never fuzzily. Ambiguity is a failure. | AndroidWorld seeds near-miss filenames (21-character shared prefix), and 13 of 29 elements on Markor's file list share one `resource_name`. |
| D11 | 2026-08-22 | The predicate excludes coordinates. `text` is exempt for `input_text`. | Actions are index-based and resolved again at replay. For `input_text`, the field's content is what changes. |
| D14 | 2026-08-22 | An episode counts as drifted only if its drift is confirmed. | Otherwise "verification rarely fired" could be reported when no drift was applied. The check itself was replaced by D48. |
| D28 | 2026-08-26 | Two harness bugs from the first live run were fixed. | The accessibility service can die while the device reports booted, and 11 episodes were skipped while the wrapper reported completion. |
| D29 | 2026-09-04 | The safety count is the deliverable. L1 and the matcher call are cut, so flow selection is an oracle, disclosed as a limitation. | L1 and instruction-to-flow matching repeat SkillDroid. |
| D30 | 2026-09-04 | L3 is produced by replaying a flow on another seed's device state. | The recorded target is absent but same-shaped objects are present, and it needs no new environment code. |
| D40 | 2026-10-12 | The verified arm always checks that the right app is open. | Wrong app means immediate abort. It is deterministic and free. |
| D41 | 2026-10-12 | Flows are trimmed by a fixed rule before any replay, the same for both arms, disclosed as simulated approval. | Flows are raw LLM trajectories with post-delete presses and open-then-back loops, which a person approving them would remove. |
| D42 | 2026-10-12 | Run both L3a (target absent) and L3b (target displaced). | L3a alone is half-tautological, because the verified arm is expected to abort. L3b tests whether it finds the moved target. |
| D43 | 2026-10-12 | After a verified-arm abort, stop and count. No LLM fallback. | The safety count needs only the abort. |
| D44 | 2026-10-12 | A wrong-target action is measured by a before-and-after inventory of the device. | AndroidWorld's validator checks that the intended object is gone. It cannot see collateral deletion. |
| D45 | 2026-10-12 | Scale L3a with 5 extra device seeds per flow, with no model calls. | A flow is needed only at its source seed, so the device can use any seed. |
| D47 | 2026-10-18 | The strict predicate is frozen. A relaxed variant is logged on the same screens but never acts. | The relaxed variant reads labels only when id tags leave several candidates. Its risk is passing on a lookalike, so both were measured. |
| D48 | 2026-10-18 | Replay confirms drift from the device inventory, not the tree hash. | The tree hash includes the status-bar clock, so it differs even at L0. |
| D49 | 2026-10-18 | A review of `replay.py` before the runs made 11 findings: 9 fixed in code, 2 deferred to analysis. | One was a real scoring bug: a wifi setting change was counted as a removed object. |
| D50 | 2026-10-18 | The post-replay inventory waits until two reads agree (3 s floor, 15 s cap). | The Files app deletes asynchronously, so an immediate read scored a correct delete as nothing destroyed. |
| D51 | 2026-10-18 | The first L0 pass is recorded as it came out, and two confounds are raised (A22, A23). | Verified 6/6 intended only, unverified 3/6 wrong target. This pass was then quarantined by D52. |
| D52 | 2026-10-18 | Both arms wait until the screen has changed and held still for one poll. The first L0 pass is quarantined and L0 re-run. | The verified arm's element poll gave it time the blind arm lacked, so a blind press could land on a half-loaded screen. |
| D53 | 2026-10-18 | Add a labelled `L0clean` run for Markor with the hidden `.app` folder removed. The natural L0 stays reported. | L0 needs a real control, and the natural incidents are data. |
| D54 | 2026-10-18 | L0 pass 2 and `L0clean` are the reported L0. | `.app` appeared in 4 of 4 Markor episodes in pass 1 and 1 of 4 in pass 2, so it is not deterministic. |
| D55 | 2026-10-18 | `.app` is also removed at L3a and L3b. | A random one-row shift would blur the controlled drift. See D60. |
| D56 | 2026-10-18 | Calendar target identity is title plus start time. Episodes are re-scored into a side file, and the raw file is not edited. | Titles repeat across seeds, so title-only scoring counted a same-titled event at another time as the intended delete. |
| D57 | 2026-10-18 | L3a result recorded. Validator success is not reported at L3a. | At L3a the validator scores the device seed's task, which replay never attempted. |
| D58 | 2026-10-18 | L3b result recorded. Episodes with the target pushed off-screen stay in and are flagged. | Decoys and target are present, so the episode is drifted even though the new index cannot be measured. |
| D59 | 2026-10-21 | `analyze.py` generates `results.md`, and a test pins the counts. | Report numbers should trace to a generator. |
| D60 | 2026-10-22 | D55 stands, with no L3 re-run. The report must state the removal and must not present Markor's L3 numbers as natural conditions. | L3 varies one thing at a time. Natural drift is reported at L0. |
| D61 | 2026-10-22 | Probe the Markor toolbar failure, with outcomes fixed in advance. Rig bug: fix both arms, quarantine, re-run all 8 L3b episodes. Real behaviour: no re-run, name the cause. No reproduction: call it flaky and stop. | 1 of 4 completed cannot be attributed while the cause is unknown. Fixing the outcomes in advance prevents a fishing trip. |
| D62 | 2026-10-22 | The lookalike weakness is reported as a limitation with its measured facts. No offline shadow with event time. | All 3 lookalikes differ in time and 1 shares the date. A time field would pass all 3 by construction. |
| D63 | 2026-10-22 | No extra L3 runs now. They remain possible later, as separate rows, never pooled. | A repeat on the same layout is not a new sample. |
| D64 | 2026-10-23 | Bulk deletes are excluded and stated in the report. A loop version is future work. | There are no recordings, and the question would change from wrong target to overreach. |
| D65 | 2026-10-23 | Add the missing `answer` action and re-run read-only tasks on the same model. Replay any successes at L0, counting aborts only. | The tasks could not pass without it. Replayed answers are copies, so replay "success" means nothing there. |
| D66 | 2026-10-23 | Report efficiency as measured, naming host and provider, and lead with the price of the check. | Only blind versus checked avoids the trimming and provider confounds. |
| D67 | 2026-10-24 | All 6 irreversible labels were checked by hand against the trimmed flows and are correct. | Only the confirm step (OK or Yes) is irreversible; Delete just opens a dialog. |
| A22 | 2026-10-18 | Open question, answered by D53: L0 carries unplanned drift from Markor's `.app` folder. | |
| A23 | 2026-10-18 | Open question, answered by D52: the verified arm waited longer than the unverified arm. | |
| A24 | 2026-10-18 | Open question, reported per D62: the predicate is under-determined when a recorded element has no distinguishing field. | |
| A25 | 2026-10-18 | Open, probe pending per D61: a long-press on the correct displaced Markor row did not open the selection toolbar. | |
| C10 | | Report how many recorded targets carry each identity field. | Shows where strict matching degrades to "the only lookalike". |
