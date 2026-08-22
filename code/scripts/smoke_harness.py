"""Validate the whole harness with a scripted fake LLM. No API calls, no key.

Exercises: agent loop, target-fingerprint capture, token/latency accounting,
JSONL checkpointing, resume, and flow recording.
"""
import json, os, pathlib, shutil, sys
os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from android_world import registry
from android_world.env import env_launcher
from flowagent.llm import FakeClient
from flowagent.runner import ADB, run_episode
from flowagent.trace import TraceWriter, episode_id

OUT = pathlib.Path('/tmp/flowagent_smoke')
shutil.rmtree(OUT, ignore_errors=True)
writer = TraceWriter(OUT / 'baseline.jsonl')
flows = OUT / 'flows'

# Scripted "model": open the app, poke the first clickable element, then declare done.
class Script:
  def __init__(self, app): self.app, self.n = app, 0
  def __call__(self, system, user):
    self.n += 1
    if self.n == 1:
      return json.dumps({'reason': 'open the app',
                         'action': {'action_type': 'open_app', 'app_name': self.app}})
    if self.n == 2:
      # pick a clickable element index out of the rendered screen
      idx = next((int(l.split()[0]) for l in user.splitlines()
                  if l[:1].isdigit() and '[C' in l), 0)
      return f'Reasoning first, then:\n```json\n{json.dumps({"reason":"poke","action":{"action_type":"click","index":idx}})}\n```'
    return json.dumps({'reason': 'stop', 'action': {'action_type': 'status', 'goal_status': 'complete'}})

env = env_launcher.load_and_setup_env(console_port=5554, emulator_setup=False, adb_path=ADB)
aw = registry.TaskRegistry().get_registry('android_world')
try:
  for task, app in [('MarkorDeleteNote', 'Markor'), ('ContactsAddContact', 'Contacts')]:
    client = FakeClient(script=Script(app))
    ep = run_episode(env, client, aw[task], task, 1, writer, flows, max_steps=4)
    t = ep.totals()
    print(f'  {task}: success={ep.success} steps={t["steps"]} llm_calls={t["llm_calls"]} '
          f'tokens={t["prompt_tokens"]}+{t["completion_tokens"]} reason={ep.done_reason}')
    tgt = [s for s in ep.steps if s.get('target')]
    print(f'    steps with a captured target fingerprint: {len(tgt)}/{len(ep.steps)}')
    if tgt:
      f = tgt[0]['target']
      print(f'    e.g. rn={(f["resource_name"] or "").split("/")[-1]!r} '
            f'cd={str(f["content_description"])[:40]!r}')
finally:
  env.close()

print('\n--- checkpoint / resume ---')
ids = writer.completed_ids()
print(f'  episodes on disk: {len(ids)}')
print(f'  resume would skip MarkorDeleteNote seed1: '
      f'{episode_id("MarkorDeleteNote",1,"baseline",None) in ids}')
recs = writer.load()
print(f'  JSONL parses: {len(recs)} records, keys ok: '
      f'{all("totals" in r and "drift_confirmed" in r for r in recs)}')
print(f'  flows recorded: {[p.name for p in sorted(flows.glob("*.json"))] if flows.exists() else "none (no task succeeded)"}')
