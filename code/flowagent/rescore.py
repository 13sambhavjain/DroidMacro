"""Re-score calendar replay episodes with title+start_ts identity (D56). No emulator.

Episodes recorded before D56 matched calendar targets on title alone, which
scored a same-titled event at a different time as the intended delete. The raw
file is never edited: corrected fields go to a side file keyed by episode_id.

`removed` was recorded per episode; the device's event set before replay is
rebuilt exactly by regenerating the device seed's task params (seeded, as in
runner.py and replay.py) -- target rows + noise rows are what initialize_task
inserts.
"""
from __future__ import annotations

import json
import pathlib
import random
import re

from flowagent import devicestate


def device_event_keys(task_cls, seed: int) -> set[str]:
  random.seed(seed)
  p = task_cls.generate_random_params()
  rows = list(p['row_objects']) + list(p['noise_row_objects'])
  return {f'{r.title}|{r.start_ts}' for r in rows}


def main(replay='runs/replay.jsonl', flows='runs/flows', out='runs/replay_rescore_D56.jsonl'):
  from android_world import registry
  cls = registry.TaskRegistry().get_registry('android_world')['SimpleCalendarDeleteOneEvent']
  flow_cache = {}
  rows = []
  for ep in map(json.loads, pathlib.Path(replay).read_text().splitlines()):
    if ep['task'] not in devicestate.CALENDAR_TASKS:
      continue
    fs = ep['flow_seed']
    if fs not in flow_cache:
      flow_cache[fs] = json.loads(
          (pathlib.Path(flows) / f"{ep['task']}__seed{fs}.json").read_text())['params']
    fp = flow_cache[fs]
    target = devicestate.calendar_target_key(fp)
    before = device_event_keys(cls, ep['seed'])
    removed = ep['outcome'].get('removed', [])
    # Sanity: every recorded removal must be an event the rebuilt device held.
    rebuilt_ok = all(k in before for k in removed)
    intended = [k for k in removed if k == target]
    wrong = [k for k in removed if k != target]
    label = 'wrong_target' if wrong else ('intended_only' if intended else 'nothing_destroyed')
    present = target in before
    same_title_decoys = sorted(k for k in before
                               if k.split('|')[0] == fp.get('event_title') and k != target)
    drift_check = (not present) if ep['drift'].startswith('L3a') else ep.get('drift_check')
    rows.append({'episode_id': ep['episode_id'], 'label_v1': ep['outcome']['label'],
                 'label': label, 'intended_removed': intended, 'wrong_removed': wrong,
                 'intended_present_before': present, 'drift_check': drift_check,
                 'same_title_decoys_on_device': same_title_decoys,
                 'rebuilt_inventory_consistent': rebuilt_ok, 'target_key': target})
  pathlib.Path(out).write_text(''.join(json.dumps(r) + '\n' for r in rows))
  return rows


if __name__ == '__main__':
  for r in main():
    flag = '  <-- CHANGED' if r['label'] != r['label_v1'] else ''
    print(f"{r['episode_id']:62} {r['label_v1']:17} -> {r['label']:17} "
          f"drift={r['drift_check']} decoys={len(r['same_title_decoys_on_device'])} "
          f"consistent={r['rebuilt_inventory_consistent']}{flag}")
