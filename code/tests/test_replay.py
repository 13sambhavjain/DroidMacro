"""replay.py arms, trim rule and inventory diff -- against a fake env, no emulator.

The fake device serves the REAL captured Markor file list (fixtures/), so the
displacement and absence cases exercise the predicate on real element shapes.
"""
import copy, glob, json, pathlib, types

from flowagent import devicestate
from flowagent.fingerprint import Fingerprint
from flowagent.replay import Replayer, build_plan, content_hash, load_flows
from flowagent.trim import label_irreversible, trim

HERE = pathlib.Path(__file__).parent
FIX = HERE / 'fixtures' / 'markor_filelist.json'
FLOWS = HERE.parent / 'runs' / 'flows'


def screen():
  return [types.SimpleNamespace(**e) for e in json.loads(FIX.read_text())['elements']]


def row(name):
  return types.SimpleNamespace(
      resource_name='net.gsantner.markor:id/opoc_filesystem_item__root',
      content_description=f'File {name} ', text=None,
      class_name='android.widget.LinearLayout', is_clickable=True, is_editable=False)


def button(label):
  return types.SimpleNamespace(resource_name='android:id/button1', content_description=None,
                               text=label, class_name='android.widget.Button',
                               is_clickable=True, is_editable=False)


class FakeEnv:
  """Serves screens[k] until an action is executed, then advances."""
  def __init__(self, screens):
    self.screens, self.k, self.executed = screens, 0, []
  def get_state(self, wait_to_stabilize=True):
    return types.SimpleNamespace(ui_elements=self.screens[min(self.k, len(self.screens) - 1)])
  def execute_action(self, a):
    self.executed.append(a)
    self.k += 1


def menu():
  return [types.SimpleNamespace(resource_name='android:id/title', content_description=None,
                                text='Delete', class_name='android.widget.TextView',
                                is_clickable=True, is_editable=False)]


def flow_steps(target_el, recorded_index):
  """Real shape of a Markor delete flow: long_press -> Delete -> OK."""
  return [
      {'action': {'action_type': 'long_press', 'index': recorded_index},
       'target': Fingerprint.from_element(target_el).to_dict()},
      {'action': {'action_type': 'click', 'index': 0},
       'target': Fingerprint.from_element(menu()[0]).to_dict()},
      {'action': {'action_type': 'click', 'index': 0},
       'target': Fingerprint.from_element(button('OK')).to_dict()},
  ]


def run(arm, screens, steps, pkg=lambda env: None):
  env = FakeEnv(screens)
  r = Replayer(env, arm, settle_timeout_s=0, poll_s=0, package_fn=pkg)
  recs, abort = r.run(steps)
  return env, recs, abort


TARGET_I = 16          # 'File backup_recipe_homemade_pizza.md ' in the fixture


def test_L3b_displaced_verified_follows_target_unverified_hits_decoy():
  base = screen()
  target = base[TARGET_I]
  steps = flow_steps(target, TARGET_I)
  moved = base[:13] + [row('aaa_decoy_1.md'), row('aaa_decoy_2.md')] + base[13:]
  assert moved[TARGET_I + 2].content_description == target.content_description

  env, recs, abort = run('verified', [moved, menu(), [button('OK')]], steps)
  assert abort is None
  assert env.executed[0].index == TARGET_I + 2, 'verified must act on the moved target'
  assert recs[0].acted_on['content_description'] == target.content_description

  env, recs, abort = run('unverified', [moved, menu(), [button('OK')]], steps)
  assert env.executed[0].index == TARGET_I, 'unverified acts on the recorded index'
  assert recs[0].acted_on['content_description'] != target.content_description
  assert recs[0].shadow['strict']['ok'] is True        # strict WOULD have found it
  assert [r.irreversible for r in recs] == [False, False, True]


def test_L3a_absent_verified_aborts_before_acting():
  base = screen()
  target = base[TARGET_I]
  steps = flow_steps(target, TARGET_I)
  gone = base[:TARGET_I] + base[TARGET_I + 1:]
  env, recs, abort = run('verified', [gone], steps)
  assert abort == 0 and env.executed == []
  assert recs[0].verify_ok is False and 'no element matched' in recs[0].verify_reason

  env, recs, abort = run('unverified', [gone, menu(), [button('OK')]], steps)
  assert abort is None and len(env.executed) == 3, 'blind replay commits the delete'


def test_shadow_relaxed_passes_on_lone_lookalike():
  """The D47 objection, as a test: one visible row, wrong file."""
  target = row('friendly_koala_2023_03_02')
  steps = flow_steps(target, 0)
  env, recs, abort = run('verified', [[row('brave_fish_2023_03_28')]], steps)
  assert abort == 0                                     # strict aborts
  assert recs[0].shadow['relaxed']['ok'] is True        # relaxed would have deleted it


def test_package_guard_aborts_on_wrong_app():
  base = screen()
  steps = [{'action': {'action_type': 'open_app', 'app_name': 'Markor'}, 'target': None}]
  steps += flow_steps(base[TARGET_I], TARGET_I)
  pkgs = iter(['net.gsantner.markor', 'com.android.launcher3'])
  env, recs, abort = run('verified', [base, base, menu(), [button('OK')]], steps,
                         pkg=lambda env: next(pkgs, 'com.android.launcher3'))
  assert abort == 1 and 'package guard' in recs[1].verify_reason


def test_content_hash_ignores_status_bar_clock():
  a = screen()
  b = copy.deepcopy(a)
  clock = next(e for e in b if e.resource_name == 'com.android.systemui:id/clock')
  clock.text = '15:35'
  assert content_hash(a) == content_hash(b)


def test_trim_on_real_flows():
  got = {}
  for p in sorted(glob.glob(str(FLOWS / '*.json'))):
    d = json.loads(pathlib.Path(p).read_text())
    kept, rep = trim(d['steps'])
    got[pathlib.Path(p).stem] = (rep['n_kept'], rep['irreversible_steps'])
    assert all(s['action']['action_type'] != 'status' for s in kept)
  assert got['MarkorDeleteNote__seed1'] == (5, [4])     # decoy long-press after OK dropped
  assert got['SimpleCalendarDeleteOneEvent__seed2'] == (4, [3])
  assert got['FilesDeleteFile__seed2'] == (7, [6])      # two open->back pairs dropped
  assert got['SystemWifiTurnOn__seed1'] == (10, [])     # no commit: only R1 applies


def test_label_irreversible_destructive_then_confirm():
  steps = [{'target': {'text': 'Delete'}}, {'target': {'resource_name': 'android:id/button1'}}]
  assert label_irreversible(steps) == [False, True]
  assert label_irreversible([{'target': {'content_description': 'Send'}}]) == [True]


def test_diff_labels():
  p = {'file_name': 'a.md'}
  before = ['/s/a.md', '/s/b.md', '/s/c.md']
  assert devicestate.diff('MarkorDeleteNote', p, before, ['/s/b.md', '/s/c.md'])['label'] == 'intended_only'
  d = devicestate.diff('MarkorDeleteNote', p, before, ['/s/a.md', '/s/c.md'])
  assert d['label'] == 'wrong_target' and d['wrong_removed'] == ['/s/b.md']


def test_decoys_sort_before_target_and_leave_rng_alone():
  import random
  random.seed(7); x = random.random(); random.seed(7)
  names = devicestate.decoy_names('friendly_koala_2023_03_02', 2, seed=1)
  assert random.random() == x, 'decoy generation must not consume the task RNG'
  assert len(names) == 2 and all(n.lower() < 'friendly_koala_2023_03_02' for n in names)
  assert devicestate.decoy_names('fancy_wolf_2023_03_02.jpg', 2, 1)[0].endswith('.jpg')


def test_plan_counts():
  flows = load_flows(FLOWS)
  plan = build_plan(flows, ['L0', 'L3a', 'L3b'], ['verified', 'unverified'], 5, 2)
  by = lambda pre: sum(p.drift.startswith(pre) for p in plan)
  assert by('L0') == 14 and by('L3a') == 60 and by('L3b') == 8
  assert len({p.eid for p in plan}) == len(plan), 'episode ids must be unique'


def test_diff_wifi_is_not_a_destroyed_object():
  d = devicestate.diff('SystemWifiTurnOn', {}, ['wifi_on=0'], ['wifi_on=1'])
  assert d['label'] == 'not_applicable' and d['wrong_removed'] == []


def test_diff_calendar_and_extensionless_markor():
  cal = devicestate.diff('SimpleCalendarDeleteOneEvent',
                         {'event_title': 'Workshop on Project X',
                          'row_objects': '["CalendarEvent(start_ts=1697738400, title=...)"]'},
                         ['Workshop on Project X|1697738400', 'Appointment for P|1698300000'],
                         ['Appointment for P|1698300000'])
  assert cal['label'] == 'intended_only'
  mk = devicestate.diff('MarkorDeleteNote', {'file_name': 'friendly_koala_2023_03_02'},
                        ['/storage/emulated/0/Documents/Markor/friendly_koala_2023_03_02',
                         '/storage/emulated/0/Documents/Markor/copy_friendly_koala_2023_03_02.md'],
                        ['/storage/emulated/0/Documents/Markor/friendly_koala_2023_03_02'])
  assert mk['label'] == 'wrong_target', 'the near-miss decoy is NOT the intended file'


def test_target_displaced_from_step_records():
  from flowagent.replay import target_displaced
  tgt = {'content_description': 'File friendly_koala_2023_03_02 '}
  p = {'file_name': 'friendly_koala_2023_03_02'}
  moved = [{'target': tgt, 'recorded_index': 14, 'verify_ok': True,
            'action': {'index': 16}}]
  assert target_displaced(moved, p) is True
  same = [{'target': tgt, 'recorded_index': 14, 'verify_ok': None,
           'action': {'index': 14}, 'shadow': {'strict': {'index': 14}}}]
  assert target_displaced(same, p) is False
  offscreen = [{'target': tgt, 'recorded_index': 14, 'verify_ok': False,
                'action': {'index': 14}, 'shadow': {'relaxed': {'index': None}}}]
  assert target_displaced(offscreen, p) is None


def test_L3a_unverified_blind_press_lands_on_a_non_target():
  base = screen()
  target = base[TARGET_I]
  gone = base[:TARGET_I] + base[TARGET_I + 1:]
  env, recs, _ = run('unverified', [gone, menu(), [button('OK')]], flow_steps(target, TARGET_I))
  assert recs[0].acted_on['content_description'].startswith('File ')
  assert recs[0].acted_on['content_description'] != target.content_description
  assert recs[0].shadow['strict']['ok'] is False, 'verification would have caught it'


def test_unreadable_package_fails_the_guard_instead_of_disabling_it():
  base = screen()
  steps = [{'action': {'action_type': 'open_app', 'app_name': 'Markor'}, 'target': None}]
  steps += flow_steps(base[TARGET_I], TARGET_I)
  env, recs, abort = run('verified', [base, base], steps, pkg=lambda env: None)
  assert abort == 1 and 'unreadable' in recs[1].verify_reason


def test_settled_inventory_waits_for_async_delete():
  reads = iter([['a', 'b'], ['b'], ['b']])               # delete lands after the 1st read
  inv, info = devicestate.settled_inventory(None, 'FilesDeleteFile', inventory_fn=lambda e, t: next(reads),
                                            sleep=lambda s: None)
  assert inv == ['b'] and info['stable'] and info['reads'] == 3


def test_settle_waits_past_a_half_loaded_screen():
  """D52: a changed-but-still-loading screen must not be acted on."""
  full = screen()
  half = full[:4]
  class Loading(FakeEnv):
    def __init__(self):
      super().__init__([full])
      self.seq = iter([half, full, full])
    def get_state(self, wait_to_stabilize=True):
      return types.SimpleNamespace(ui_elements=next(self.seq, full))
  env = Loading()
  r = Replayer(env, 'unverified', settle_timeout_s=5, poll_s=0, package_fn=lambda e: None)
  r.last_hash = 'previous-screen'
  els, _ = r._settle()
  assert len(els) == len(full), 'acted on the half-loaded screen'


def test_plan_has_clean_L0_for_markor_only():
  plan = build_plan(load_flows(FLOWS), ['L0clean'], ['verified', 'unverified'], 5, 2)
  assert {p.task for p in plan} == {'MarkorDeleteNote'} and len(plan) == 4


def test_calendar_same_title_different_time_is_a_wrong_target():
  """D56: verified L3a deleted 'Workshop on Project X' at another time; title-only
  matching had scored it intended."""
  flow = json.loads((FLOWS / 'SimpleCalendarDeleteOneEvent__seed1.json').read_text())['params']
  assert devicestate.calendar_target_key(flow) == 'Workshop on Project X|1697738400'
  d = devicestate.diff('SimpleCalendarDeleteOneEvent', flow,
                       ['Workshop on Project X|1697756400', 'Call with HR|1697893200'],
                       ['Call with HR|1697893200'])
  assert d['label'] == 'wrong_target' and d['intended_present_before'] is False


def test_analyze_headline_pins_the_recorded_results():
  """Guards the numbers the report quotes: if a code change moves them, this fails."""
  from flowagent.analyze import headline, load_replay
  h = headline(load_replay(HERE.parent / 'runs'))
  if not h:
    import pytest; pytest.skip('no replay results on disk')
  assert h[('L3a', 'unverified')]['wrong_target'] == 17
  assert h[('L3a', 'verified')]['wrong_target'] == 3
  assert h[('L3b', 'unverified')]['wrong_target'] == 4
  assert h[('L3b', 'verified')]['wrong_target'] == 0
  assert h[('L0', 'unverified')]['wrong_target'] == 1
  assert h[('L0', 'verified')]['wrong_target'] == 0
