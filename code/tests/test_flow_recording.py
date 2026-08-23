"""save_flow + slot discovery + L0/L1 substitution round-trip, on the real fixture."""
import dataclasses, json, pathlib, types
import pytest
from flowagent.fingerprint import Fingerprint, verify
from flowagent.runner import save_flow
from flowagent.trace import EpisodeRecord, StepRecord, episode_id

FIX = pathlib.Path(__file__).parent / 'fixtures' / 'markor_filelist.json'


def load():
  d = json.loads(FIX.read_text())
  return d, [types.SimpleNamespace(**e) for e in d['elements']]


def notes(els):
  return [e for e in els if (e.content_description or '').startswith('File ')]


def build_episode(target, params):
  ep = EpisodeRecord(episode_id('MarkorDeleteNote', 1, 'baseline', None),
                     'MarkorDeleteNote', 1, 'baseline', None,
                     goal=f'Delete the note in Markor named {params["file_name"]}.',
                     provider='fake', model='fake-0')
  ep.steps = [
      dataclasses.asdict(StepRecord(0, {'action_type': 'open_app', 'app_name': 'Markor'})),
      dataclasses.asdict(StepRecord(1, {'action_type': 'long_press', 'index': 3},
                                    target=Fingerprint.from_element(target).to_dict())),
      dataclasses.asdict(StepRecord(2, {'action_type': 'status', 'goal_status': 'complete'})),
  ]
  ep.success = True
  ep.start_tree_hash = 'abc123'
  return ep


def test_flow_written_with_slots(tmp_path):
  d, els = load()
  target = notes(els)[0]
  name = target.content_description[len('File '):].strip()
  ep = build_episode(target, {'file_name': name})
  p = save_flow(tmp_path, 'MarkorDeleteNote', 1, ep, {'file_name': name})
  assert p and p.exists()
  flow = json.loads(p.read_text())

  assert flow['n_steps'] == 3
  assert flow['recorded_start_tree_hash'] == 'abc123'
  assert flow['params'] == {'file_name': name}

  targeted = [s for s in flow['steps'] if s['target']]
  assert len(targeted) == 1, 'only the long_press has a target'
  slots = targeted[0]['target']['slots']
  assert slots, 'slot discovery found no parameter in the fingerprint'
  fields = {f for f, _ in slots}
  assert 'content_description' in fields, slots


def test_flow_replays_at_L0_and_retargets_at_L1(tmp_path):
  """L0: same params -> same element. L1: new param -> the OTHER element."""
  d, els = load()
  ns = notes(els)
  recorded, other = ns[0], ns[1]
  rec_name = recorded.content_description[len('File '):].strip()
  oth_name = other.content_description[len('File '):].strip()

  ep = build_episode(recorded, {'file_name': rec_name})
  p = save_flow(tmp_path, 'MarkorDeleteNote', 1, ep, {'file_name': rec_name})
  flow = json.loads(p.read_text())
  fp = Fingerprint.from_dict(
      next(s for s in flow['steps'] if s['target'])['target'])

  r0 = verify(fp, els, 'long_press', values={'file_name': rec_name})
  assert r0.ok and els[r0.index].content_description == recorded.content_description

  r1 = verify(fp, els, 'long_press', values={'file_name': oth_name})
  assert r1.ok, r1.reason
  assert els[r1.index].content_description == other.content_description
  assert r1.index != r0.index, 'L1 must retarget, not reuse the recorded element'


def test_L3_abort_when_target_absent(tmp_path):
  """The whole point of `verified`: element gone -> clean abort, not a blind tap."""
  d, els = load()
  recorded = notes(els)[0]
  name = recorded.content_description[len('File '):].strip()
  ep = build_episode(recorded, {'file_name': name})
  p = save_flow(tmp_path, 'MarkorDeleteNote', 1, ep, {'file_name': name})
  fp = Fingerprint.from_dict(
      next(s for s in json.loads(p.read_text())['steps'] if s['target'])['target'])

  perturbed = [e for e in els if e is not recorded]
  r = verify(fp, perturbed, 'long_press', values={'file_name': name})
  assert not r.ok and r.n_candidates == 0
