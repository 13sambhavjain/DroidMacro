"""Predicate tests against a REAL captured Markor file list (29 elements).

The fixture matters: AndroidWorld seeds near-miss filenames as distractors, so this
screen genuinely contains e.g. "copy_jolly_island" alongside "jolly_island". A fuzzy
predicate would pick the wrong one and delete the wrong file.
"""
import json, pathlib, types
import pytest
from flowagent.fingerprint import Fingerprint, verify

FIX = pathlib.Path(__file__).parent / 'fixtures' / 'markor_filelist.json'


def load():
  d = json.loads(FIX.read_text())
  return d, [types.SimpleNamespace(**e) for e in d['elements']]


def note_elements(els):
  return [e for e in els if (e.content_description or '').startswith('File ')]


def test_fixture_contains_distractors():
  """Guard: if this fails the fixture lost the property that makes it useful."""
  _, els = load()
  names = [e.content_description[5:] for e in note_elements(els)]
  assert len(names) >= 5

  def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
      if x != y:
        break
      n += 1
    return n

  # The real distractor shape is not substring containment -- it is a long shared
  # PREFIX with a short differing tail, e.g.
  #   art_project_sketches_EEIu.md  vs  art_project_sketches_Fpks.md
  # Any prefix/edit-distance/fuzzy matcher would confuse these two. Exact equality
  # is the only rule that separates them.
  worst = max((common_prefix(a, b), a, b)
              for a in names for b in names if a != b)
  assert worst[0] >= 10, f'fixture lost its distractors; longest shared prefix {worst}'
  print(f'\n  distractor pair (shared prefix {worst[0]} chars):\n    {worst[1]!r}\n    {worst[2]!r}')


def test_exact_match_selects_the_right_note():
  _, els = load()
  target = note_elements(els)[0]
  fp = Fingerprint.from_element(target)
  r = verify(fp, els, 'click')
  assert r.ok and r.n_candidates == 1
  assert els[r.index].content_description == target.content_description


def test_resource_name_alone_would_be_ambiguous():
  """The measured failure of SCOPE_DECISION v2's spec: rows share resource_name."""
  _, els = load()
  target = note_elements(els)[0]
  fp = Fingerprint.from_element(target)
  loose = Fingerprint(resource_name=fp.resource_name, class_name=fp.class_name)
  r = verify(loose, els, 'click')
  assert not r.ok and r.n_candidates > 1, 'expected ambiguity across note rows'
  assert 'ambiguous' in r.reason


def test_missing_element_fails_cleanly():
  _, els = load()
  fp = Fingerprint.from_element(note_elements(els)[0])
  survivors = [e for e in els if e is not note_elements(els)[0]]
  r = verify(fp, survivors, 'click')
  assert not r.ok and r.n_candidates == 0


def test_no_target_actions_pass_vacuously():
  _, els = load()
  fp = Fingerprint()
  for a in ('navigate_home', 'open_app', 'wait', 'keyboard_enter'):
    assert verify(fp, els, a).ok


def test_parameter_substitution_retargets_the_flow():
  """L1: same flow, different parameter -> must resolve to the OTHER note."""
  d, els = load()
  notes = note_elements(els)
  recorded, other = notes[0], notes[1]
  rec_name = recorded.content_description[5:]
  oth_name = other.content_description[5:]
  fp = Fingerprint.from_element(recorded).with_slots({'file_name': rec_name})
  assert fp.slots, 'slot discovery found nothing'
  r = verify(fp, els, 'click', values={'file_name': oth_name})
  assert r.ok, r.reason
  assert els[r.index].content_description == other.content_description


def test_input_text_ignores_text_field():
  """Record-time and replay-time text legitimately differ for a field being typed into."""
  _, els = load()
  base = els[0]
  fp = Fingerprint(resource_name=base.resource_name, class_name=base.class_name,
                   text='typed at record time')
  r_click = verify(fp, els, 'click')
  r_input = verify(fp, els, 'input_text')
  assert not r_click.ok
  assert r_input.ok or r_input.n_candidates != 1


# ---- slot discovery: phantom-slot regression (found 2026-08-23) ----------------
# Naive substring matching over task params creates FALSE slots. Measured values in
# the subset include on_or_off='on', month='10', hour='18', duration_mins='15'.

def test_short_params_are_rejected_as_slots():
  fp = Fingerprint(resource_name='r', class_name='c', content_description='Contacts')
  # 'on' IS inside 'Contacts', and IS in the goal -- only the length floor and the
  # word-boundary rule stop it becoming a slot.
  assert fp.with_slots({'on_or_off': 'on'}, goal='Turn wifi on.').slots == ()


def test_word_boundary_blocks_embedded_matches():
  fp = Fingerprint(resource_name='r', class_name='c', content_description='Notifications')
  assert fp.with_slots({'mode': 'ication'}, goal='set ication').slots == ()


def test_value_absent_from_goal_is_not_a_slot():
  """If the user never said it, the matcher cannot extract it at replay."""
  fp = Fingerprint(resource_name='r', class_name='c', content_description='File report_2024')
  assert fp.with_slots({'file_name': 'report_2024'}, goal='Delete something').slots == ()
  got = fp.with_slots({'file_name': 'report_2024'}, goal='Delete report_2024 please').slots
  assert got and got[0][1][0][0] == 'file_name'


def test_longest_match_wins_over_contained_param():
  fp = Fingerprint(resource_name='r', class_name='c', content_description='File trip_notes_2024')
  goal = 'Delete trip_notes_2024 which is in trip_notes'
  got = fp.with_slots({'short': 'trip_notes', 'long': 'trip_notes_2024'}, goal=goal).slots
  flat = [kv for _, hits in got for kv in hits]
  assert flat == [('long', 'trip_notes_2024')], flat
