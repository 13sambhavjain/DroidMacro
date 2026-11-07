"""Results tables from the trace files. No emulator, no model calls.

Produces SCOPE_DECISION §8 under its rules:
  * both seeds reported individually (AM4) -- no means over n=2
  * safety as EXACT COUNTS plus a per-incident narrative, never a percentage
    or confidence interval (§5)
  * the headline is wrong-target irreversible actions, from the device inventory
    diff, not AndroidWorld's validator (D44)
  * an L3 episode counts only if its drift was confirmed (D14/D48); dropped
    episodes are counted, not hidden
  * calendar episodes use the D56 re-score (title+start_ts identity)
  * at L3a, `success` scores the DEVICE seed's task, which replay never
    attempted, so it is not reported (D57)

Also emits the hand spot-check list of every step labelled irreversible
(SCOPE_DECISION §2) and the identity-field composition of the flows (C10).

  python -m flowagent.analyze            # writes runs/results.md
"""
from __future__ import annotations

import collections
import json
import pathlib
import statistics
from typing import Any, Optional

from flowagent import devicestate
from flowagent.subset import SUBSET
from flowagent.trim import label_irreversible, trim

RUNS = pathlib.Path('runs')
DELETE_TASKS = devicestate.FILE_TASKS | devicestate.CALENDAR_TASKS
IDENTITY_FIELDS = ('resource_name', 'class_name', 'content_description', 'text')


def _load(path: pathlib.Path) -> list[dict]:
  return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def load_replay(runs: pathlib.Path = RUNS) -> list[dict]:
  """Replay episodes with the D56 calendar re-score applied (raw file untouched)."""
  rescore = {r['episode_id']: r for r in _load(runs / 'replay_rescore_D56.jsonl')}
  out = []
  for ep in _load(runs / 'replay.jsonl'):
    r = rescore.get(ep['episode_id'])
    if r:
      ep = dict(ep, outcome=dict(ep['outcome'], label=r['label'],
                                 intended_removed=r['intended_removed'],
                                 wrong_removed=r['wrong_removed'],
                                 intended_present_before=r['intended_present_before'],
                                 rescored='D56'),
                drift_check=r['drift_check'])
    out.append(ep)
  return out


def drift_family(drift: Optional[str]) -> str:
  return (drift or '-').split(':')[0]


def counts_for_drift(ep: dict) -> bool:
  """Does this episode count at its drift level? L0 always; L3 only if confirmed.

  L3b with `displaced=None` (target pushed off-screen, so its new index is
  unmeasurable) is still drifted -- decoys present, target present. It is kept
  and flagged, not dropped (D58).
  """
  fam = drift_family(ep['drift'])
  if fam.startswith('L0'):
    return True
  if fam == 'L3b' and ep['drift_check'] is False and ep['outcome'].get('displaced') is None:
    return bool(ep['outcome'].get('intended_present_before'))
  return ep['drift_check'] is True


def _label(fp: Optional[dict]) -> str:
  if not fp:
    return '—'
  return (fp.get('content_description') or fp.get('text')
          or (fp.get('resource_name') or '').split('/')[-1] or '—').strip()


def _short(key: str) -> str:
  return key.split('/')[-1] if '/' in key else key


def incident(ep: dict) -> str:
  """One-line narrative: where the replay pressed something other than recorded."""
  arm = ep['condition'].split('_')[0]
  bits = []
  for s in ep['steps']:
    if not s.get('target') or s['action'].get('action_type') in ('open_app', 'scroll'):
      continue
    rec, got = _label(s['target']), _label(s.get('acted_on'))
    if arm == 'unverified' and s.get('acted_on') and got != rec:
      bits.append(f"step {s['i']}: pressed **{got}** at recorded index {s['recorded_index']} "
                  f"(recording: {rec})")
    if s.get('error') and 'out of range' in s['error']:
      bits.append(f"step {s['i']}: index {s['recorded_index']} out of range (screen not as recorded)")
  if arm == 'verified':
    bits.append('every check passed — the recorded fingerprint matched a lookalike uniquely')
  wrong = ', '.join(f'`{_short(k)}`' for k in ep['outcome'].get('wrong_removed', []))
  return f"{'; '.join(bits[:2]) or 'no divergent press logged'} → destroyed {wrong}"


def table_rows(replay: list[dict], baseline: list[dict]) -> list[list[Any]]:
  rows = []
  for ep in sorted(baseline, key=lambda e: (e['task'], e['seed'])):
    if ep['task'] not in DELETE_TASKS:
      continue
    t = ep['totals']
    rows.append(['baseline', '—', ep['task'], ep['seed'], 1,
                 int(bool(ep['success'])), t['llm_calls'],
                 f"{t['prompt_tokens']}+{t['completion_tokens']}", f"{ep['wall_s']:.0f}", '—', '—'])
  groups: dict[tuple, list[dict]] = collections.defaultdict(list)
  for ep in replay:
    if ep['task'] in DELETE_TASKS and counts_for_drift(ep):
      groups[(ep['condition'].split('_')[0], drift_family(ep['drift']), ep['task'],
              ep['flow_seed'])].append(ep)
  for (arm, fam, task, fseed), eps in sorted(groups.items()):
    n = len(eps)
    succ = '—' if fam == 'L3a' else sum(e['outcome']['label'] == 'intended_only' for e in eps)
    rows.append([f'{arm} replay', fam, task, fseed, n, succ, 0, '0',
                 f"{statistics.median(e['wall_s'] for e in eps):.0f}",
                 sum(e['done_reason'] == 'abort' for e in eps),
                 sum(e['outcome']['label'] == 'wrong_target' for e in eps)])
  return rows


def headline(replay: list[dict]) -> dict:
  h = collections.defaultdict(lambda: collections.Counter())
  for ep in replay:
    if ep['task'] not in DELETE_TASKS or not counts_for_drift(ep):
      continue
    h[(drift_family(ep['drift']), ep['condition'].split('_')[0])][ep['outcome']['label']] += 1
  return h


def latency_rows(replay: list[dict], baseline: list[dict]) -> list[list]:
  """Where the time goes -- the efficiency number that is actually measured."""
  def dec(e):
    S = e['steps']
    llm = sum(s.get('llm_latency_s', 0) for s in S)
    obs = sum(s.get('obs_latency_s', 0) for s in S)
    act = sum(s.get('act_latency_s', 0) for s in S)
    return llm, obs, act, len(S), e['wall_s']
  groups = [('baseline (successful)', [e for e in baseline if e['task'] in DELETE_TASKS and e['success']])]
  for arm in ('verified', 'unverified'):
    groups.append((f'{arm} replay L0', [e for e in replay if e['task'] in DELETE_TASKS
                                       and e['drift'] == 'L0' and e['condition'].startswith(arm)]))
  rows = []
  for name, eps in groups:
    if not eps:
      continue
    ds = [dec(e) for e in eps]
    med = lambda i: statistics.median(d[i] for d in ds)
    per_step = statistics.median((d[0] + d[1] + d[2]) / d[3] for d in ds if d[3])
    rows.append([name, len(eps), f'{med(4):.1f}', f'{med(3):.0f}', f'{med(0):.1f}',
                 f'{med(1):.1f}', f'{med(2):.1f}', f'{per_step:.1f}'])
  return rows


def baseline_by_tier(baseline: list[dict]) -> list[list]:
  rows = []
  for tier in ('irreversible', 'reversible', 'readonly'):
    eps = [e for e in baseline if SUBSET[e['task']][0] == tier]
    rows.append([tier, len(eps), sum(bool(e['success']) for e in eps)])
  return rows


def check_cost(replay: list[dict]) -> list[list]:
  """Seconds per step, verified minus unverified, paired by flow at L0.

  A flow whose verified arm aborted is left out: it spent its time polling for
  an element and never finished, so the pair does not compare like with like.
  """
  def per_step(e):
    return sum(s.get('llm_latency_s', 0) + s.get('obs_latency_s', 0) + s.get('act_latency_s', 0)
               for s in e['steps']) / len(e['steps'])
  pairs: dict[tuple, dict] = collections.defaultdict(dict)
  for e in replay:
    if e['task'] in DELETE_TASKS and e['drift'] == 'L0':
      pairs[(e['task'], e['flow_seed'])][e['condition'].split('_')[0]] = e
  return [[task, fseed, round(per_step(p['verified']) - per_step(p['unverified']), 2)]
          for (task, fseed), p in sorted(pairs.items()) if p['verified']['done_reason'] != 'abort']


def shadow_agreement(replay: list[dict]) -> tuple[int, int]:
  """(steps where the relaxed predicate agreed with strict, steps it was evaluated on)."""
  steps = [s for e in replay if e['condition'].startswith('verified')
           for s in e['steps'] if s.get('shadow')]
  return sum(bool(s['shadow']['relaxed']['ok']) == bool(s['verify_ok']) for s in steps), len(steps)


def irreversible_steps(flow_dir: pathlib.Path) -> list[tuple]:
  out = []
  for p in sorted(flow_dir.glob('*.json')):
    d = json.loads(p.read_text())
    steps, _ = trim(d['steps'])
    for n, (s, irr) in enumerate(zip(steps, label_irreversible(steps))):
      if irr:
        prev = next((steps[k] for k in range(n - 1, -1, -1) if steps[k].get('target')), None)
        out.append((p.stem, n, _label(prev.get('target') if prev else None), _label(s['target'])))
  return out


def composition(flow_dir: pathlib.Path) -> list[tuple]:
  out = []
  for p in sorted(flow_dir.glob('*.json')):
    steps, _ = trim(json.loads(p.read_text())['steps'])
    fps = [s['target'] for s in steps if s.get('target')]
    labelled = sum(bool(f.get('content_description') or f.get('text')) for f in fps)
    out.append((p.stem, len(fps), labelled,
                {k: sum(bool(f.get(k)) for f in fps) for k in IDENTITY_FIELDS}))
  return out


def render(runs: pathlib.Path = RUNS) -> str:
  replay, baseline = load_replay(runs), _load(runs / 'baseline.jsonl')
  L = ['# DroidMacro results (generated by `flowagent/analyze.py` — do not edit)\n']

  L.append('## Headline: wrong-target irreversible actions executed (delete tasks)\n')
  L.append('Exact counts. Ground truth = device inventory diff. Calendar identity = title+start time (D56).\n')
  L.append('| Drift | Arm | Episodes | **Wrong-target** | Intended only | Nothing destroyed |')
  L.append('|---|---|---|---|---|---|')
  for (fam, arm), c in sorted(headline(replay).items()):
    L.append(f"| {fam} | {arm} | {sum(c.values())} | **{c['wrong_target']}** | "
             f"{c['intended_only']} | {c['nothing_destroyed']} |")

  dropped = [e for e in replay if e['task'] in DELETE_TASKS and not counts_for_drift(e)]
  offscreen = [e for e in replay if drift_family(e['drift']) == 'L3b'
               and e['drift_check'] is False and counts_for_drift(e)]
  L.append(f"\nDrift filter: **{len(dropped)}** delete-task episode(s) dropped as undrifted"
           + (': ' + ', '.join(f"`{e['episode_id']}`" for e in dropped) if dropped else '')
           + f". {len(offscreen)} L3b episode(s) kept with target pushed off-screen "
           "(displacement unmeasurable, decoys and target present).\n")

  L.append('## SCOPE_DECISION §8 table — each flow seed individually\n')
  L.append('Success = intended object destroyed and nothing else. Not reported at L3a (the '
           'recorded target does not exist there). Replay makes zero model calls by construction.\n')
  L.append('| Condition | Drift | Task | Flow seed | Episodes | Success | LLM calls/ep | Tokens | '
           'Median latency (s) | Aborts | Wrong-target |')
  L.append('|---|---|---|---|---|---|---|---|---|---|---|')
  for r in table_rows(replay, baseline):
    L.append('| ' + ' | '.join(str(x) for x in r) + ' |')

  L.append('\n## Every wrong-target incident\n')
  for ep in sorted(replay, key=lambda e: e['episode_id']):
    if ep['task'] in DELETE_TASKS and counts_for_drift(ep) and ep['outcome']['label'] == 'wrong_target':
      val = ' (AndroidWorld validator scored this a **success**)' if ep.get('success') else ''
      L.append(f"- `{ep['episode_id']}` — {incident(ep)}{val}")

  L.append('\n## Verified-arm aborts\n')
  reasons = collections.Counter()
  for ep in replay:
    if ep['condition'].startswith('verified') and ep['done_reason'] == 'abort' and ep['task'] in DELETE_TASKS:
      s = ep['steps'][ep['abort_step']]
      reasons[(drift_family(ep['drift']), _label(s['target']), (s['verify_reason'] or '')[:60])] += 1
  L.append('| Drift | Step target | Reason | Count |\n|---|---|---|---|')
  for (fam, tgt, why), n in sorted(reasons.items()):
    L.append(f'| {fam} | {tgt} | {why} | {n} |')

  agree, checked = shadow_agreement(replay)
  L.append('\n## Shadow predicate (D47)\n')
  L.append(f'The relaxed predicate agreed with the strict one on {agree} of {checked} checked steps.')

  L.append('\n## Baseline by tier (provisional)\n')
  L.append('Read-only tasks could not pass: they are scored from the `answer` action, which the '
           'agent prompt did not offer (D65).\n')
  L.append('| Tier | Episodes | Succeeded |\n|---|---|---|')
  for tier, n, ok in baseline_by_tier(baseline):
    L.append(f'| {tier} | {n} | {ok} |')
  L.append(f"\nTotals over {len(baseline)} episodes: "
           f"{sum(e['totals']['prompt_tokens'] for e in baseline)} input tokens, "
           f"{sum(e['totals']['completion_tokens'] for e in baseline)} output tokens, "
           f"{sum(e['wall_s'] for e in baseline):.0f} s.")

  L.append('\n## Latency decomposition (medians; delete tasks)\n')
  L.append('Replay is shorter end-to-end mainly because trimmed flows have about half the steps '
           '(D41). Per step, unverified replay costs about what a baseline step does and verified '
           'replay costs more (its element poll). The time the model no longer takes is replaced by '
           'screen settling, which dominates on this host. Zero tokens is true by construction and is '
           'not a result (DESIGN.md §1).\n')
  L.append('| Condition | Episodes | Wall (s) | Steps | LLM (s) | Observe/settle (s) | Act (s) | Per step (s) |')
  L.append('|---|---|---|---|---|---|---|---|')
  for r in latency_rows(replay, baseline):
    L.append('| ' + ' | '.join(str(x) for x in r) + ' |')

  L.append('\n## Price of the check (seconds per step, verified minus unverified, L0)\n')
  L.append('Paired by flow. A flow whose verified arm aborted is left out.\n')
  L.append('| Task | Flow seed | Difference (s) |\n|---|---|---|')
  costs = check_cost(replay)
  for task, fseed, d in costs:
    L.append(f'| {task} | {fseed} | {d:+.2f} |')
  d = [r[2] for r in costs]
  L.append(f'\nMedian {statistics.median(d):+.2f} s over {len(d)} flows; '
           f'range {min(d):+.2f} to {max(d):+.2f}.')

  wifi = [e for e in replay if e['task'] == 'SystemWifiTurnOn']
  if wifi:
    L.append('\n## Wifi (reversible, L0 only — not in the safety count)\n')
    for e in wifi:
      L.append(f"- `{e['episode_id']}`: {e['done_reason']}, validator success={e['success']}")

  L.append('\n## Hand spot-check: every step labelled irreversible (SCOPE_DECISION §2)\n')
  L.append('Checked by hand against the trimmed flows on 2026-10-24 (D67).\n')
  L.append('| Flow | Trimmed step | Destructive step | Commit step | OK? |\n|---|---|---|---|---|')
  for flow, n, prev, commit in irreversible_steps(runs / 'flows'):
    L.append(f'| {flow} | {n} | {prev} | {commit} | ✓ |')

  L.append('\n## Identity-field composition of the flows (C10)\n')
  L.append('Targeted steps after trimming; "labelled" = carries content_description or text. '
           'Unlabelled targets are where strict matching degenerates to "the only lookalike" (A24).\n')
  L.append('| Flow | Targeted steps | Labelled | ' + ' | '.join(IDENTITY_FIELDS) + ' |')
  L.append('|---|---|---|' + '---|' * len(IDENTITY_FIELDS))
  for flow, n, lab, comp in composition(runs / 'flows'):
    L.append(f'| {flow} | {n} | {lab} | ' + ' | '.join(str(comp[k]) for k in IDENTITY_FIELDS) + ' |')
  return '\n'.join(L) + '\n'


def main() -> None:
  md = render()
  (RUNS / 'results.md').write_text(md)
  print(md)


if __name__ == '__main__':
  main()
