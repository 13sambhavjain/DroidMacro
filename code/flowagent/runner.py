"""Episode runner: baseline pass over the subset, recording candidate flows.

Resumable by construction. Groq's free tier WILL interrupt a full pass
(SCOPE_DECISION §6), so a killed run must resume at the next episode rather than
redo work or corrupt a partial episode.

Seeds are applied to `random` before generate_random_params(), which is what makes
L0 (same template, SAME parameters) reproducible later -- the params are also stored
in the flow record so a replay can re-instantiate the identical task.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import random
import time
from typing import Optional

os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')

from android_world import registry
from android_world.env import env_launcher

from flowagent import subset as subset_mod
from flowagent.agent import BaselineAgent
from flowagent.fingerprint import Fingerprint
from flowagent.llm import RateLimited, make_client
from flowagent.trace import (EpisodeRecord, TraceWriter, episode_id, tree_hash)

ADB = os.path.expanduser('~/Library/Android/sdk/platform-tools/adb')


def save_flow(flow_dir: pathlib.Path, task: str, seed: int, ep: EpisodeRecord,
              params: dict) -> Optional[pathlib.Path]:
  """A SUCCESSFUL baseline episode is a candidate flow (SCOPE_DECISION §0 step 1).

  Slots are discovered by exact-substring match of the recording's own params
  against each target fingerprint -- the simulated stand-in for a human pointing at
  a value and saying "that's the variable". DISCLOSED in the write-up.
  """
  steps = []
  for s in ep.steps:
    fp_d = s.get('target')
    fp = (Fingerprint.from_dict(fp_d).with_slots(params, goal=ep.goal)
           if fp_d else None)
    steps.append({'action': s['action'],
                  'target': fp.to_dict() if fp else None,
                  'source_step': s['i']})
  if not steps:
    return None
  flow_dir.mkdir(parents=True, exist_ok=True)
  path = flow_dir / f'{task}__seed{seed}.json'
  path.write_text(json.dumps({
      'task': task, 'seed': seed, 'goal': ep.goal, 'params': params,
      'recorded_start_tree_hash': ep.start_tree_hash,
      'provider': ep.provider, 'model': ep.model,
      'n_steps': len(steps), 'steps': steps}, indent=1, default=str))
  return path


def run_episode(env, client, task_cls, task_name: str, seed: int,
                writer: TraceWriter, flow_dir: pathlib.Path,
                max_steps: Optional[int] = None) -> EpisodeRecord:
  eid = episode_id(task_name, seed, 'baseline', None)
  ep = EpisodeRecord(eid, task_name, seed, 'baseline', None,
                     provider=getattr(client, 'provider', '?'),
                     model=getattr(client, 'model', '?'),
                     started_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
  t_start = time.perf_counter()

  random.seed(seed)
  params = task_cls.generate_random_params()
  task = task_cls(params)
  ep.params = {k: str(v)[:200] for k, v in params.items()}

  env.reset(go_home=True)
  task.initialize_task(env)
  ep.goal = task.goal
  budget = max_steps or int(getattr(task_cls, 'complexity', 1.5) * 10)

  ep.start_tree_hash = tree_hash(env.get_state(wait_to_stabilize=True).ui_elements)

  agent = BaselineAgent(env, client)
  agent.reset()
  try:
    for i in range(budget):
      out = agent.step(task.goal, i)
      ep.steps.append(dataclasses.asdict(out.record))
      if out.done:
        ep.done_reason = 'done'
        break
    else:
      ep.done_reason = 'step_budget'
  except RateLimited:
    ep.done_reason = 'rate_limited'
    raise
  finally:
    ep.wall_s = time.perf_counter() - t_start
    if ep.done_reason != 'rate_limited':
      try:
        ep.success = task.is_successful(env) == 1
      except Exception as exc:                      # noqa: BLE001
        ep.done_reason = f'score_error: {type(exc).__name__}: {exc}'
        ep.success = False
      try:
        task.tear_down(env)
      except Exception:                             # noqa: BLE001,S110
        pass

  if ep.success:
    p = save_flow(flow_dir, task_name, seed, ep, params)
    print(f'    flow recorded -> {p.name if p else "(no steps)"}')
  writer.append(ep)
  return ep


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument('--provider', default='fake', choices=['fake', 'groq', 'azure'])
  ap.add_argument('--model', default=None)
  ap.add_argument('--seeds', default='1')
  ap.add_argument('--tasks', default=None, help='comma list; default = full subset')
  ap.add_argument('--tier', default=None, choices=['irreversible', 'reversible', 'readonly'])
  ap.add_argument('--out', default='runs/baseline.jsonl')
  ap.add_argument('--flows', default='runs/flows')
  ap.add_argument('--max_steps', type=int, default=None)
  ap.add_argument('--console_port', type=int, default=5554)
  args = ap.parse_args()

  names = ([t.strip() for t in args.tasks.split(',')] if args.tasks
           else subset_mod.tasks(args.tier))
  seeds = [int(s) for s in args.seeds.split(',')]
  writer = TraceWriter(args.out)
  done = writer.completed_ids()
  flow_dir = pathlib.Path(args.flows)

  client = make_client(args.provider, args.model)
  aw = registry.TaskRegistry().get_registry('android_world')

  todo = [(t, s) for t in names for s in seeds
          if episode_id(t, s, 'baseline', None) not in done]
  print(f'provider={args.provider} model={getattr(client,"model","?")}  '
        f'{len(todo)} episodes to run ({len(done)} already done, resuming)')
  if not todo:
    return

  env = env_launcher.load_and_setup_env(console_port=args.console_port,
                                        emulator_setup=False, adb_path=ADB)
  n_ok = 0
  rate_limited = False
  consecutive_env_errors = 0
  env_broken = False
  n_errors = 0
  try:
    for k, (t, s) in enumerate(todo, 1):
      print(f'[{k}/{len(todo)}] {t} seed={s}')
      try:
        ep = run_episode(env, client, aw[t], t, s, writer, flow_dir, args.max_steps)
      except RateLimited as exc:
        print(f'  !! rate limited: {exc}\n  checkpointed; rerun the same command '
              f'to resume from here.')
        rate_limited = True
        break
      except Exception as exc:                      # noqa: BLE001
        print(f'  !! {type(exc).__name__}: {exc}')
        n_errors += 1
        # A dead accessibility service fails every subsequent episode instantly.
        # Observed 2026-08-26: one TimeoutError, then 10 straight 'Could not get
        # a11y tree' -- 11 episodes silently skipped while the runner still
        # exited 0 and the wrapper declared the pass COMPLETE. Bail out and let
        # the wrapper restart the emulator instead of burning the rest of the list.
        if 'a11y' in str(exc) or isinstance(exc, TimeoutError):
          consecutive_env_errors += 1
          if consecutive_env_errors >= 3:
            print(f'  !! {consecutive_env_errors} consecutive environment failures'
                  f' -- the accessibility service looks dead. Stopping so the'
                  f' emulator can be restarted; progress is checkpointed.')
            env_broken = True
            break
        continue
      consecutive_env_errors = 0
      tot = ep.totals()
      n_ok += bool(ep.success)
      print(f'    success={ep.success} steps={tot["steps"]} llm_calls={tot["llm_calls"]} '
            f'tokens={tot["prompt_tokens"]}+{tot["completion_tokens"]} '
            f'{ep.wall_s:.0f}s ({ep.done_reason})')
  finally:
    env.close()
  remaining = len(todo) - len(writer.completed_ids() & {episode_id(t, s_, 'baseline', None)
                                                        for t, s_ in todo})
  print(f'\n{n_ok}/{len(todo)} succeeded -> flows in {flow_dir}')
  if n_errors:
    print(f'{n_errors} episode(s) errored and were skipped; {remaining} still unrun')

  # Exit codes the wrapper acts on:
  #   75 rate limited      -> sleep, then resume
  #   76 environment broke -> restart the emulator, then resume immediately
  #    0 genuinely done    -> only when nothing is left unrun
  if env_broken:
    raise SystemExit(76)
  if rate_limited:
    raise SystemExit(75)
  if remaining:
    raise SystemExit(76)


if __name__ == '__main__':
  main()
