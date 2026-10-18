"""Device-state ground truth for replay episodes (D44), plus L3b decoy insertion (D42).

AndroidWorld's validator checks that the INTENDED object is gone; it cannot see a
wrong object being destroyed, which is the headline measurement. So every replay
episode snapshots an inventory of the objects the task family can destroy, before
and after, and the diff is the ground truth:
  * file tasks (Markor, Files): every regular file under /storage/emulated/0
  * calendar: every row of Simple Calendar Pro's events table
  * wifi: the wifi_on setting (no object can be destroyed; recorded for L0 only)
"""
from __future__ import annotations

import json
import os
import random
import re
from typing import Any, Optional

from android_world.env import adb_utils, device_constants
from android_world.task_evals.single.calendar import calendar_utils
from android_world.task_evals.utils import (sqlite_schema_utils, sqlite_utils,
                                            user_data_generation)
from android_world.utils import file_utils

FILE_TASKS = frozenset({'MarkorDeleteNote', 'FilesDeleteFile'})
CALENDAR_TASKS = frozenset({'SimpleCalendarDeleteOneEvent'})


def _shell(env, cmd: str) -> str:
  resp = adb_utils.issue_generic_request(['shell', cmd], env.controller)
  return resp.generic.output.decode(errors='replace')


def inventory(env, task: str) -> list[str]:
  """Sorted list of object keys. A key that disappears = an object destroyed."""
  if task in FILE_TASKS:
    # Whole storage, not just the task dir: at L3a a Files flow navigates to the
    # RECORDED folder, which is not where the device's target lives. App-private
    # and hidden paths (thumbnails, caches) churn on their own and are excluded,
    # or a vanished cache file would be scored as a wrong-target delete.
    out = _shell(env, f'find {device_constants.EMULATOR_DATA} -type f '
                      "-not -path '*/Android/*' -not -path '*/.*' 2>/dev/null")
    return sorted(l.strip() for l in out.splitlines() if l.strip())
  if task in CALENDAR_TASKS:
    rows = sqlite_utils.get_rows_from_remote_device(
        calendar_utils.EVENTS_TABLE, calendar_utils.DB_PATH,
        sqlite_schema_utils.CalendarEvent, env)
    return sorted(f'{r.title}|{r.start_ts}' for r in rows)
  return [f'wifi_on={_shell(env, "settings get global wifi_on").strip()}']


def settled_inventory(env, task: str, floor_s: float = 3.0, poll_s: float = 1.5,
                      max_s: float = 15.0, inventory_fn=None, sleep=None) -> tuple[list[str], dict]:
  """Inventory AFTER the last action has taken effect.

  Measured 2026-10-18: the Files app deletes asynchronously -- the target was still
  listed 0.1 s after the confirm tap and gone by 1.6 s. Reading immediately scored
  a correct L0 delete as `nothing_destroyed`. So: wait a floor (about 2x the
  observed lag), then read until two consecutive reads agree, bounded.
  """
  import time as _t
  inventory_fn = inventory_fn or inventory
  sleep = sleep or _t.sleep
  sleep(floor_s)
  waited, reads = floor_s, 1
  prev = inventory_fn(env, task)
  while waited < max_s:
    sleep(poll_s)
    waited += poll_s
    cur = inventory_fn(env, task)
    reads += 1
    if cur == prev:
      return cur, {'post_wait_s': waited, 'reads': reads, 'stable': True}
    prev = cur
  return prev, {'post_wait_s': waited, 'reads': reads, 'stable': False}


def calendar_target_key(flow_params: dict) -> Optional[str]:
  """`title|start_ts` of the event the flow was recorded to delete.

  Title alone is NOT identity (D56): event titles come from a small template,
  and 8 of 10 L3a devices held a different event with the flow's exact title.
  The instruction names a date and hour, so the start time is part of the target.
  """
  rows = flow_params.get('row_objects')
  text = rows if isinstance(rows, str) else json.dumps(rows, default=str)
  m = re.search(r"start_ts=(\d+)", text or '')
  title = flow_params.get('event_title')
  return f'{title}|{m.group(1)}' if (m and title) else None


def intended_key_match(task: str, flow_params: dict, key: str) -> bool:
  """Is this inventory key the object the FLOW was recorded to destroy?

  The flow's own params, not the device's: at L3a the device was set up from a
  different seed, so its task target is not what the replay is trying to delete.
  """
  if task in FILE_TASKS:
    return os.path.basename(key) == flow_params.get('file_name')
  if task in CALENDAR_TASKS:
    return key == calendar_target_key(flow_params)
  return False


def diff(task: str, flow_params: dict, before: list[str], after: list[str]) -> dict:
  if task not in FILE_TASKS | CALENDAR_TASKS:
    # Nothing destroyable (wifi): a changed setting is not a removed object.
    return {'label': 'not_applicable', 'before': before, 'after': after,
            'removed': [], 'added': [], 'intended_removed': [], 'wrong_removed': [],
            'intended_present_before': None}
  removed = sorted(set(before) - set(after))
  added = sorted(set(after) - set(before))
  intended = [k for k in removed if intended_key_match(task, flow_params, k)]
  wrong = [k for k in removed if k not in intended]
  intended_present = any(intended_key_match(task, flow_params, k) for k in before)
  if wrong:
    label = 'wrong_target'
  elif intended:
    label = 'intended_only'
  else:
    label = 'nothing_destroyed'
  return {'label': label, 'before': before, 'removed': removed, 'added': added,
          'intended_removed': intended, 'wrong_removed': wrong,
          'intended_present_before': intended_present}


# ---- L3b: target displaced -------------------------------------------------
def target_dir(task: str, params: dict) -> Optional[str]:
  if task == 'MarkorDeleteNote':
    return device_constants.MARKOR_DATA
  if task == 'FilesDeleteFile':
    return file_utils.convert_to_posix_path(device_constants.EMULATOR_DATA,
                                            params['subfolder'])
  return None                     # calendar L3b needs a DB insert: not built (D42)


def decoy_names(target: str, k: int, seed: int) -> list[str]:
  """k same-shaped names that sort BEFORE the target, so the target's list index
  moves down by k while it stays present. Uses a private RNG so the task's own
  random stream is untouched."""
  stem, ext = os.path.splitext(target)
  if ext.lower() not in ('.jpg', '.mp3', '.mp4', '.md', '.txt', '.png', '.pdf'):
    stem, ext = target, ''
  saved = random.getstate()
  try:
    random.seed(1_000_003 * seed + 17)
    out: list[str] = []
    for _ in range(5000):
      if len(out) == k:
        break
      name = user_data_generation.generate_random_file_name() + ext
      if name.lower() < target.lower() and name != target and name not in out:
        out.append(name)
    for i in range(k - len(out)):            # target sorts too early: force it
      out.append(f'0{i}_{stem}{ext}')
    return out
  finally:
    random.setstate(saved)


def insert_decoys(env, task: str, params: dict, k: int, seed: int) -> list[str]:
  d = target_dir(task, params)
  if d is None:
    raise NotImplementedError(f'L3b not supported for {task}')
  names = decoy_names(params['file_name'], k, seed)
  for n in names:
    file_utils.create_file(n, d, env.controller)
  return [file_utils.convert_to_posix_path(d, n) for n in names]
