"""Keyless smoke test: exercise the whole AndroidWorld loop except the LLM.

Answers the Week-1 go/no-go questions without spending a cent:
  - does the env launch, initialize a task, and produce accessibility trees?
  - how long does one observe->act->observe cycle actually take on this host?
  - how big is a serialized a11y tree? (this is the per-step prompt cost driver)

Usage:
  .venv/bin/python scripts/smoke_env.py --task=ContactsAddContact --steps=5
"""

import argparse
import json
import os
import pathlib
import statistics
import sys
import time

os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')
os.environ.setdefault('GRPC_TRACE', 'none')

from android_world import registry
from android_world.env import env_launcher
from android_world.env import json_action

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from flowagent.config import ADB, CONSOLE_PORT, GRPC_PORT


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument('--task', default='ContactsAddContact')
  ap.add_argument('--steps', type=int, default=5)
  ap.add_argument('--open_app', default=None,
                  help='Open this app first, so tree size is measured in-app, not on home.')
  ap.add_argument('--console_port', type=int, default=CONSOLE_PORT)
  ap.add_argument('--adb_path', default=ADB)
  args = ap.parse_args()

  t0 = time.perf_counter()
  env = env_launcher.load_and_setup_env(
      console_port=args.console_port, emulator_setup=False, adb_path=args.adb_path,
      grpc_port=GRPC_PORT)
  print(f'[timing] env launch: {time.perf_counter()-t0:.1f}s')

  env.reset(go_home=True)

  reg = registry.TaskRegistry()
  aw = reg.get_registry(reg.ANDROID_WORLD_FAMILY)
  task_type = aw[args.task]
  params = task_type.generate_random_params()
  task = task_type(params)

  t0 = time.perf_counter()
  task.initialize_task(env)
  print(f'[timing] task init: {time.perf_counter()-t0:.1f}s')
  print(f'[task]   {args.task}')
  print(f'[goal]   {task.goal}')
  print(f'[params] {json.dumps(params, default=str)[:300]}')

  if args.open_app:
    t0 = time.perf_counter()
    env.execute_action(
        json_action.JSONAction(action_type='open_app', app_name=args.open_app))
    print(f'[timing] open_app({args.open_app}): {time.perf_counter()-t0:.1f}s')

  obs_times, tree_sizes, elem_counts = [], [], []
  for i in range(args.steps):
    t0 = time.perf_counter()
    state = env.get_state(wait_to_stabilize=True)
    dt = time.perf_counter() - t0
    obs_times.append(dt)

    elems = state.ui_elements
    elem_counts.append(len(elems))
    # Rough proxy for what a text agent puts in its prompt.
    serialized = '\n'.join(str(e) for e in elems)
    tree_sizes.append(len(serialized))
    print(f'[step {i}] observe={dt:5.2f}s  ui_elements={len(elems):3d}  '
          f'serialized={len(serialized):6d} chars (~{len(serialized)//4} tokens)')

    # A deliberately harmless action so we time the act->observe cycle too.
    t0 = time.perf_counter()
    env.execute_action(json_action.JSONAction(action_type='wait'))
    print(f'[step {i}] act(wait)={time.perf_counter()-t0:5.2f}s')

  print('\n=== SUMMARY (this host) ===')
  print(f'observe latency: median {statistics.median(obs_times):.2f}s  '
        f'max {max(obs_times):.2f}s')
  print(f'ui elements:     median {statistics.median(elem_counts):.0f}')
  print(f'a11y tree size:  median {statistics.median(tree_sizes):.0f} chars '
        f'(~{statistics.median(tree_sizes)//4:.0f} tokens/step of prompt)')
  print(f'task success (should be False, no action was taken): '
        f'{task.is_successful(env) == 1}')

  task.tear_down(env)
  env.close()


if __name__ == '__main__':
  main()
