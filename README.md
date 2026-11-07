# DroidMacro

When an approved Android agent run is replayed later with no model, and the screen has changed, what does
the replay destroy? This independent study compares **blind replay** (press the recorded position) with
**checked replay** (act only if the recorded element is on screen exactly once).

**Status: interim.** The study is ongoing. Read [`REPORT.md`](REPORT.md) for the setup, findings,
limitations and tools used.

## Measured so far

Wrong-target irreversible actions executed on delete tasks. Exact counts, from a before-and-after inventory
of the device.

| Drift | Blind | Checked | Status |
|---|---|---|---|
| No planned drift (L0) | 1 of 6 | 0 of 6 | final |
| Recorded target absent (L3a) | 17 of 30 | 3 of 30 | final |
| Target displaced by decoys (L3b) | 4 of 4 | 0 of 4, task completed in 1 of 4 | provisional |

L3a replays each of 6 flows on 5 other device seeds, so its 30 episodes are not independent samples.

The baseline agent succeeded on 7 of 21 episodes, leaving out 7 read-only episodes that could not pass
because of a harness problem. That figure is provisional.

## Not done yet

- A probe of why Markor's selection toolbar did not appear at L3b, which could mean re-running L3b
- A re-run of the read-only tasks with the missing `answer` action

## What's here

| Path | What it is |
|---|---|
| `REPORT.md` | The interim report |
| `code/flowagent/` | Baseline agent, replay arms, device inventory and analysis |
| `code/flowagent/fingerprint.py` | The verification predicate |
| `code/runs/results.md` | All result tables, generated; do not edit |
| `code/runs/*.jsonl`, `code/runs/flows/` | Raw traces and recorded flows, never edited |
| `code/runs/quarantine/` | Earlier passes set aside after harness fixes, not reported |
| `code/tests/` | Tests, including ones that pin the reported figures |
| `SCOPE_DECISION.md`, `DESIGN.md` | Scope and design as they evolved |
| `DECISIONS.md` | Each decision cited by ID, with its reason |
| `SETUP.md` | macOS setup and machine settings |
| `patches/` | The one change made to AndroidWorld |

`verified` and `unverified` in the code are the checked and blind arms in the report.

## Reproduce

Everything ran on a MacBook Air (M2, 8 GB RAM) and has not been tried on another machine.
[`SETUP.md`](SETUP.md) gives the macOS steps and the environment variables for machine-specific values;
each defaults to what the study used. Tests and analysis need AndroidWorld installed, but no emulator and no
API key:

```bash
cd code
.venv/bin/python -m pytest -q
.venv/bin/python -m flowagent.analyze     # rewrites runs/results.md, byte-identical
```

The experiments need the emulator, and the baseline also needs a Groq key in `.env`.

```bash
.venv/bin/python -m flowagent.replay --drift L0     # also L0clean, L3a, L3b; no model calls
.venv/bin/python -m flowagent.runner --provider groq --seeds 1,2
```

Both resume by episode ID and append to the files in `code/runs/`. To run anything again without touching
the recorded data, give new paths: `--out` and `--flows` for the runner, and `--out`, `--screens` and
`--errors` for replay.

## License

[Apache-2.0](LICENSE). Copyright 2026 Sambhav Jain. AndroidWorld, by Google Research, is also
Apache-2.0; `patches/` holds the one change made to it.
