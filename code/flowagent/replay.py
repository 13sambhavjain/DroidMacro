"""Replay a recorded flow with no model calls: the verified and unverified arms.

Plan of record (D29/D30, D40-D48):
  * verified   -- before each targeted step, the app must be the one the flow
                  opened (package guard, D40) and fingerprint.verify() must
                  resolve EXACTLY ONE element; the action is executed on that
                  element. Either check failing for SETTLE_TIMEOUT_S -> abort,
                  stop, count (D43).
  * unverified -- execute each recorded action on its recorded index. No check.
                  The predicates are still evaluated and logged (`shadow`) so the
                  analysis can ask "would verification have caught this?".
Drift levels:
  * L0        -- flow replayed on the seed it was recorded on (sanity)
  * L3a:srcS  -- seed-S flow replayed on another seed's device: target absent
  * L3b:kK    -- same seed, K decoys inserted that sort before the target, so it
                 sits at a different index while still present (D42)
Ground truth for "destroyed the wrong object" is the before/after inventory diff
(devicestate, D44), not AndroidWorld's validator.

Flows are trimmed by the fixed rule in trim.py before replay (D41).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import random
import time
from collections import Counter
from typing import Any, Optional

os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')

from flowagent import devicestate
from flowagent.fingerprint import NO_TARGET_ACTIONS, Fingerprint, verify
from flowagent.shadow import verify_relaxed
from flowagent.trace import (EpisodeRecord, StepRecord, TraceWriter, episode_id,
                             tree_hash)
from flowagent.trim import label_irreversible, trim

ADB = os.path.expanduser('~/Library/Android/sdk/platform-tools/adb')

# Same window the baseline agent waits for a screen to change (agent.py).
SETTLE_TIMEOUT_S = 6.0
SETTLE_POLL_S = 0.6
SYSTEMUI = 'com.android.systemui'
GUARD_UNAVAILABLE = '<foreground package unreadable>'

ARMS = ('verified', 'unverified')
# L0clean (D53): files the app creates on its own that were absent at recording
# time. Removed after setup so L0 is a real control; the natural L0 is kept too.
CLEAN_L0 = {'MarkorDeleteNote': ['/storage/emulated/0/Documents/Markor/.app']}
DESTRUCTIVE_TASKS = devicestate.FILE_TASKS | devicestate.CALENDAR_TASKS


# ---- screen helpers ---------------------------------------------------------
def _is_systemui(e: Any) -> bool:
  return (getattr(e, 'package_name', None) == SYSTEMUI
          or (e.resource_name or '').startswith(SYSTEMUI + ':'))


def content_hash(elements: list[Any]) -> str:
  """tree_hash without the status bar: its clock changes every minute (D48)."""
  key = '\n'.join(f'{e.resource_name}|{e.content_description}|{e.text}|{e.class_name}'
                  for e in elements if not _is_systemui(e))
  return hashlib.sha256(key.encode()).hexdigest()[:16]


def compact(elements: list[Any]) -> list[list]:
  """What the shadow analysis needs to re-run any predicate offline."""
  return [[e.resource_name, e.class_name, e.content_description, e.text,
           getattr(e, 'package_name', None)] for e in elements]


def foreground_package(env) -> Optional[str]:
  from android_world.env import adb_utils
  act = adb_utils.get_current_activity(env.controller)[0]
  return act.split('/')[0] if act else None


def _vr(r) -> dict:
  return {'ok': r.ok, 'index': r.index, 'n': r.n_candidates, 'reason': r.reason}


# ---- the replay loop --------------------------------------------------------
class Replayer:
  """Executes one trimmed flow against the current device state.

  Kept separate from episode setup so it can be tested against a fake env.
  """

  def __init__(self, env, arm: str, settle_timeout_s: float = SETTLE_TIMEOUT_S,
               poll_s: float = SETTLE_POLL_S, package_fn=foreground_package,
               screen_log=None):
    assert arm in ARMS, arm
    self.env, self.arm = env, arm
    self.settle_timeout_s, self.poll_s = settle_timeout_s, poll_s
    self.package_fn = package_fn
    self.screen_log = screen_log           # callable(step_i, elements) or None
    self.last_hash: Optional[str] = None
    self.expected_pkg: Optional[str] = None
    self._pending_pkg = False

  def _observe(self) -> list:
    return self.env.get_state(wait_to_stabilize=True).ui_elements

  def _settle(self) -> tuple[list, float]:
    """Wait (bounded) until the screen has changed AND then held still for a poll.

    "Changed" alone can be a half-loaded screen (Calendar s2 L0: 4 elements on
    screen, recorded index 16). Readiness uses no predicate, so it is identical
    for both arms (D52); before it, the blind arm tapped sooner than the checked
    one and some of its failures would have been impatience, not drift.
    """
    t0 = time.perf_counter()
    changed = self.last_hash is None
    prev: Optional[str] = None
    while True:
      els = self._observe()
      h = content_hash(els)
      if not changed and h != self.last_hash:
        changed = True
      elif changed and h == prev:
        break
      prev = h
      if time.perf_counter() - t0 >= self.settle_timeout_s:
        break
      time.sleep(self.poll_s)
    return els, time.perf_counter() - t0

  def _check(self, fp: Fingerprint, els: list, atype: str) -> tuple[bool, str, Any]:
    """Package guard, then the strict predicate. Returns (ok, reason, result)."""
    if self.expected_pkg is not None:
      pkg = self.package_fn(self.env)
      if pkg != self.expected_pkg:
        return False, f'package guard: foreground {pkg!r} != expected {self.expected_pkg!r}', None
    r = verify(fp, els, atype)
    return r.ok, r.reason, r

  def run(self, steps: list[dict]) -> tuple[list[StepRecord], Optional[int]]:
    """Returns (step records, index of the aborting step or None)."""
    labels = label_irreversible(steps)
    records: list[StepRecord] = []
    for n, step in enumerate(steps):
      action = dict(step['action'])
      atype = action.get('action_type', '')
      fp = Fingerprint.from_dict(step['target']) if step.get('target') else None
      rec = StepRecord(i=n, action=action, target=step.get('target'), source='replay',
                       recorded_index=action.get('index'), irreversible=labels[n])

      els, obs_dt = self._settle()
      if self._pending_pkg:
        # Measured, not mapped: whatever app open_app actually brought up. It can
        # read None mid-launch; poll, and if it never resolves make the guard FAIL
        # loudly rather than silently run the rest of the episode unguarded.
        t0 = time.perf_counter()
        pkg = self.package_fn(self.env)
        while pkg is None and time.perf_counter() - t0 < self.settle_timeout_s:
          time.sleep(self.poll_s)
          pkg = self.package_fn(self.env)
        self.expected_pkg = pkg if pkg is not None else GUARD_UNAVAILABLE
        self._pending_pkg = False

      targeted = fp is not None and atype not in NO_TARGET_ACTIONS
      if targeted and self.arm == 'verified':
        # Poll: a slow screen must not be mistaken for drift (D47).
        t0 = time.perf_counter()
        while True:
          ok, reason, r = self._check(fp, els, atype)
          if ok:
            # execute_action re-reads the tree and resolves the index against
            # THAT, so only act if a fresh read is still the screen we verified.
            fresh = self._observe()
            if content_hash(fresh) == content_hash(els):
              break
            els, ok, reason = fresh, False, 'screen changed between check and act'
          if time.perf_counter() - t0 >= self.settle_timeout_s:
            break
          time.sleep(self.poll_s)
          els = self._observe()
        obs_dt += time.perf_counter() - t0
        rec.verify_ok, rec.verify_reason = ok, reason
        rec.verify_candidates = r.n_candidates if r else None
        rec.shadow = {'relaxed': _vr(verify_relaxed(fp, els, atype))}
        if ok:
          action['index'] = r.index
      elif targeted:
        els = self._observe()               # as close to execute_action's read as we get
        rec.shadow = {'strict': _vr(verify(fp, els, atype)),
                      'relaxed': _vr(verify_relaxed(fp, els, atype))}

      rec.obs_latency_s = obs_dt
      rec.n_elements = len(els)
      if self.screen_log:
        self.screen_log(n, els)

      if rec.verify_ok is False:
        records.append(rec)
        return records, n

      idx = action.get('index')
      if targeted:
        if isinstance(idx, int) and 0 <= idx < len(els):
          rec.acted_on = Fingerprint.from_element(els[idx]).to_dict()
        else:
          rec.error = f'index {idx} out of range (0..{len(els) - 1})'

      self.last_hash = content_hash(els)
      t0 = time.perf_counter()
      try:
        from flowagent.agent import to_json_action
        self.env.execute_action(to_json_action(action))
      except Exception as exc:                    # noqa: BLE001 -- blind replay continues
        if isinstance(exc, TimeoutError) or 'a11y' in str(exc):
          raise                                     # infrastructure, not an outcome
        rec.error = (rec.error + '; ' if rec.error else '') + f'{type(exc).__name__}: {exc}'
      rec.act_latency_s = time.perf_counter() - t0
      if atype == 'open_app':
        self._pending_pkg = True
      records.append(rec)
    return records, None


# ---- episodes ---------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Plan:
  task: str
  flow_seed: int
  device_seed: int
  arm: str
  drift: str            # L0 | L3a:srcS | L3b:kK

  @property
  def eid(self) -> str:
    return episode_id(self.task, self.device_seed, f'{self.arm}_replay', self.drift)


def load_flows(flow_dir: pathlib.Path) -> dict[tuple[str, int], dict]:
  out = {}
  for p in sorted(flow_dir.glob('*__seed*.json')):
    d = json.loads(p.read_text())
    out[(d['task'], int(d['seed']))] = d
  return out


def build_plan(flows: dict, drifts: list[str], arms: list[str], n_targets: int,
               decoys_k: int, tasks: Optional[set[str]] = None) -> list[Plan]:
  plans = []
  for (task, s) in sorted(flows):
    if tasks and task not in tasks:
      continue
    for arm in arms:
      if 'L0' in drifts:
        plans.append(Plan(task, s, s, arm, 'L0'))
      if 'L0clean' in drifts and task in CLEAN_L0:
        plans.append(Plan(task, s, s, arm, 'L0clean'))
      if 'L3a' in drifts and task in DESTRUCTIVE_TASKS:
        for t in [t for t in range(1, 100) if t != s][:n_targets]:
          plans.append(Plan(task, s, t, arm, f'L3a:src{s}'))
      if 'L3b' in drifts and task in devicestate.FILE_TASKS:   # calendar L3b not built
        plans.append(Plan(task, s, s, arm, f'L3b:k{decoys_k}'))
  return plans


def run_episode(env, task_cls, plan: Plan, flow: dict, writer: TraceWriter,
                screens_path: pathlib.Path) -> EpisodeRecord:
  ep = EpisodeRecord(plan.eid, plan.task, plan.device_seed, f'{plan.arm}_replay',
                     plan.drift, provider='none', model='none',
                     started_at=time.strftime('%Y-%m-%dT%H:%M:%S'),
                     flow_seed=plan.flow_seed,
                     recorded_start_tree_hash=flow.get('recorded_start_tree_hash'))
  t_start = time.perf_counter()

  random.seed(plan.device_seed)                    # identical to runner.py
  params = task_cls.generate_random_params()
  task = task_cls(params)
  ep.params = {k: str(v)[:200] for k, v in params.items()}
  env.reset(go_home=True)
  task.initialize_task(env)
  ep.goal = flow['goal']                           # the REPLAYED instruction

  pre_cleanup = []
  if plan.drift == 'L0clean':
    for path in CLEAN_L0[plan.task]:
      devicestate._shell(env, f'rm -rf {path}')
      pre_cleanup.append(path)

  if plan.drift.startswith('L3b'):
    ep.decoys = devicestate.insert_decoys(env, plan.task, params,
                                          int(plan.drift.split(':k')[1]), plan.device_seed)

  ep.start_tree_hash = tree_hash(env.get_state(wait_to_stabilize=True).ui_elements)
  before = devicestate.inventory(env, plan.task)

  steps, ep.trim = trim(flow['steps'])

  # Buffered: a crashed episode is retried on resume, and its half-written screens
  # must not sit in the file next to the retry's (duplicate (episode_id, i) keys).
  screens: list[dict] = []
  def log_screen(i, els):
    screens.append({'episode_id': plan.eid, 'i': i, 'elements': compact(els)})

  try:
    recs, ep.abort_step = Replayer(env, plan.arm, screen_log=log_screen).run(steps)
    ep.steps = [dataclasses.asdict(r) for r in recs]
    ep.done_reason = 'abort' if ep.abort_step is not None else 'completed'
    ep.wall_s = time.perf_counter() - t_start

    # An inventory failure leaves the episode without its ground truth. Raise:
    # the episode is not written, so resume retries it instead of keeping an
    # invalid record that looks like a normal one.
    after, settle = devicestate.settled_inventory(env, plan.task)
    ep.outcome = devicestate.diff(plan.task, flow['params'], before, after)
    ep.outcome['inventory_settle'] = settle
    if pre_cleanup:
      ep.outcome['pre_cleanup'] = pre_cleanup
    if plan.drift.startswith('L3a'):
      ep.drift_check = not ep.outcome['intended_present_before']
    elif plan.drift.startswith('L3b'):
      ep.outcome['displaced'] = target_displaced(ep.steps, flow['params'])
      # Python's sort order is not the app's collation, so "decoys sort first"
      # is an assumption -- only a resolved index that actually moved confirms it.
      ep.drift_check = bool(ep.outcome['intended_present_before']
                            and all(d in before for d in ep.decoys)
                            and ep.outcome['displaced'])
    try:
      # Meaningful at L0/L3b. At L3a it scores the DEVICE seed's task, which the
      # replay was never trying to do -- recorded, not reported as success.
      ep.success = task.is_successful(env) == 1
    except Exception as exc:                       # noqa: BLE001
      ep.success = None
      ep.outcome['validator_error'] = f'{type(exc).__name__}: {exc}'
  finally:
    try:
      task.tear_down(env)
    except Exception:                              # noqa: BLE001,S110
      pass

  writer.append(ep)
  screens_path.parent.mkdir(parents=True, exist_ok=True)
  with screens_path.open('a') as sf:
    for row in screens:
      sf.write(json.dumps(row, default=str) + '\n')
  return ep


def target_displaced(steps: list[dict], flow_params: dict) -> Optional[bool]:
  """Did the flow's target sit at a different index than recorded?

  Uses the strict predicate's resolution of the step whose recorded target names
  the file. None = unknown (the step was never reached, or strict found nothing,
  e.g. the decoys pushed the target off-screen).
  """
  name = flow_params.get('file_name')
  for s in steps:
    t = s.get('target') or {}
    if not name or name not in ((t.get('content_description') or '') + (t.get('text') or '')):
      continue
    if s.get('verify_ok') is True:
      resolved = s['action'].get('index')
    else:
      resolved = ((s.get('shadow') or {}).get('strict') or {}).get('index')
    if resolved is None:
      return None
    return resolved != s.get('recorded_index')
  return None


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument('--drift', default='L0', help='comma list of L0,L0clean,L3a,L3b')
  ap.add_argument('--arms', default='verified,unverified')
  ap.add_argument('--tasks', default=None)
  ap.add_argument('--n_targets', type=int, default=5, help='L3a device seeds per flow (D45)')
  ap.add_argument('--decoys', type=int, default=2, help='L3b decoys inserted')
  ap.add_argument('--flows', default='runs/flows')
  ap.add_argument('--out', default='runs/replay.jsonl')
  ap.add_argument('--screens', default='runs/replay_screens.jsonl')
  ap.add_argument('--errors', default='runs/replay_errors.jsonl')
  ap.add_argument('--max_attempts', type=int, default=2,
                  help='skip an episode after this many non-environment crashes')
  ap.add_argument('--console_port', type=int, default=5554)
  ap.add_argument('--dry_run', action='store_true', help='print the plan and exit')
  args = ap.parse_args()

  flows = load_flows(pathlib.Path(args.flows))
  plans = build_plan(flows, args.drift.split(','), args.arms.split(','),
                     args.n_targets, args.decoys,
                     set(args.tasks.split(',')) if args.tasks else None)
  writer = TraceWriter(args.out)
  done = writer.completed_ids()
  errors_path = pathlib.Path(args.errors)
  crashes = Counter(json.loads(l)['episode_id'] for l in
                    (errors_path.read_text().splitlines() if errors_path.exists() else [])
                    if l.strip())
  given_up = {e for e, c in crashes.items() if c >= args.max_attempts}
  todo = [p for p in plans if p.eid not in done and p.eid not in given_up]
  print(f'{len(plans)} planned, {len(done)} already done, '
        f'{len(given_up)} given up after {args.max_attempts} crashes, {len(todo)} to run')
  for e in sorted(given_up):
    print('   given up:', e)
  if args.dry_run:
    for p in todo:
      print('  ', p.eid)
    return
  if not todo:
    return

  from android_world import registry
  from android_world.env import env_launcher
  aw = registry.TaskRegistry().get_registry('android_world')
  env = env_launcher.load_and_setup_env(console_port=args.console_port,
                                        emulator_setup=False, adb_path=ADB)
  screens = pathlib.Path(args.screens)
  consecutive_env_errors, env_broken = 0, False
  try:
    for k, p in enumerate(todo, 1):
      print(f'[{k}/{len(todo)}] {p.eid}')
      try:
        ep = run_episode(env, aw[p.task], p, flows[(p.task, p.flow_seed)], writer, screens)
      except Exception as exc:                     # noqa: BLE001
        print(f'  !! {type(exc).__name__}: {exc}')
        infra = 'a11y' in str(exc) or isinstance(exc, TimeoutError)
        if not infra:
          # A deterministic crash would otherwise loop forever under the wrapper.
          errors_path.parent.mkdir(parents=True, exist_ok=True)
          with errors_path.open('a') as ef:
            ef.write(json.dumps({'episode_id': p.eid,
                                 'error': f'{type(exc).__name__}: {exc}'[:500]}) + '\n')
        # Same failure mode as the baseline (D28): a dead a11y service fails
        # every later episode instantly. Stop so the emulator can be restarted.
        if infra:
          consecutive_env_errors += 1
          if consecutive_env_errors >= 3:
            env_broken = True
            break
        continue
      consecutive_env_errors = 0
      o = ep.outcome
      print(f'    {ep.done_reason}'
            f'{"@" + str(ep.abort_step) if ep.abort_step is not None else ""}'
            f'  outcome={o.get("label")} wrong={len(o.get("wrong_removed", []))}'
            f'  drift_check={ep.drift_check}  success={ep.success}  {ep.wall_s:.0f}s')
  finally:
    env.close()
  remaining = len([p for p in todo if p.eid not in writer.completed_ids()])
  print(f'{remaining} still unrun (crash-capped episodes excluded)')
  if env_broken or remaining:
    raise SystemExit(76)                           # run_overnight.sh: restart + resume


if __name__ == '__main__':
  main()
