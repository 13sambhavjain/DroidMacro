"""Element fingerprints and the verification predicate (SCOPE_DECISION.md §2).

THIS FILE IS THE EXPERIMENT. The predicate must stay FIXED across all three
conditions -- baseline, verified-replay, unverified-replay -- or the comparison is
meaningless. Change it only with a logged decision.

Measured facts that forced the design (2026-08-22, 111 elements / 5 screens):
  * resource_id  is None on 100% of elements -> the field named in SCOPE_DECISION
    v2 does not exist in this pipeline. resource_name (88% populated) is the real one.
  * text is populated on only 21% of elements; content_description on 47%.
  * resource_name is NOT unique: 13 of 29 elements on Markor's file list share
    ".../opoc_filesystem_item__root", carrying identity only in content_description.
  * AndroidWorld deliberately seeds near-miss filenames as distractors
    (markor.py: noise_candidates), so the list can hold "fancy_nurse_backup" and
    "copy_fancy_nurse_..." at once.

=> Matching is EXACT, never fuzzy: a substring match would select a distractor and
   delete the wrong file INSIDE the condition we claim is safe, manufacturing the
   very behaviour the experiment is supposed to measure.
=> Ambiguity (>1 candidate) is a FAIL, not a tiebreak. Guessing is the behaviour
   under study.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

# Actions that carry no target element: the pre-check passes vacuously.
NO_TARGET_ACTIONS = frozenset({
    'navigate_home', 'navigate_back', 'open_app', 'wait',
    'keyboard_enter', 'status', 'answer', 'unknown',
})

# For input_text the field's CONTENT is what we are changing, so record-time and
# replay-time text legitimately differ. Drop `text` from the predicate there.
TEXT_EXEMPT_ACTIONS = frozenset({'input_text'})

PARAMETERIZABLE_FIELDS = ('text', 'content_description')


@dataclasses.dataclass(frozen=True)
class Fingerprint:
  """Identifying attributes of one target element, plus its parameter slots."""
  resource_name: Optional[str] = None
  content_description: Optional[str] = None
  text: Optional[str] = None
  class_name: Optional[str] = None
  is_clickable: Optional[bool] = None
  is_editable: Optional[bool] = None
  # field name -> {param_name: literal value seen at record time}
  slots: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()

  @classmethod
  def from_element(cls, e: Any) -> 'Fingerprint':
    return cls(
        resource_name=e.resource_name or None,
        content_description=e.content_description or None,
        text=e.text or None,
        class_name=e.class_name or None,
        is_clickable=e.is_clickable,
        is_editable=e.is_editable,
    )

  def to_dict(self) -> dict:
    d = dataclasses.asdict(self)
    d['slots'] = [[f, [list(kv) for kv in vs]] for f, vs in self.slots]
    return d

  @classmethod
  def from_dict(cls, d: dict) -> 'Fingerprint':
    slots = tuple((f, tuple(tuple(kv) for kv in vs)) for f, vs in d.get('slots', ()))
    return cls(**{**d, 'slots': slots})

  # ---- parameterization (SCOPE_DECISION §2) --------------------------------
  def with_slots(self, params: dict[str, Any]) -> 'Fingerprint':
    """Discover parameter slots by exact substring match against recorded params.

    Simulates the human pointing at "XYZ" and saying *that's the variable*.
    DISCLOSED DEVIATION: framing B has a person mark the slots; we derive them from
    the recording's own params. Slot VALUES at replay come only from the matcher
    LLM reading the new instruction -- never from the task template (§2).
    """
    found = []
    for field in PARAMETERIZABLE_FIELDS:
      val = getattr(self, field)
      if not val:
        continue
      hits = tuple((k, str(v)) for k, v in params.items()
                   if isinstance(v, (str, int, float)) and str(v) and str(v) in val)
      if hits:
        found.append((field, hits))
    return dataclasses.replace(self, slots=tuple(found))

  def substitute(self, values: dict[str, str]) -> 'Fingerprint':
    """Fill slots with values extracted from the NEW instruction."""
    out = self
    for field, hits in self.slots:
      cur = getattr(out, field)
      if cur is None:
        continue
      for param_name, recorded_value in hits:
        new = values.get(param_name)
        if new is not None:
          cur = cur.replace(recorded_value, str(new))
      out = dataclasses.replace(out, **{field: cur})
    return out


@dataclasses.dataclass(frozen=True)
class VerifyResult:
  ok: bool
  index: Optional[int]
  n_candidates: int
  reason: str


def verify(fp: Fingerprint, elements: list[Any], action_type: str,
           values: Optional[dict[str, str]] = None) -> VerifyResult:
  """The verification predicate. Returns the resolved element index, or a failure.

  1. substitute parameter values into the fingerprint
  2. candidates: exact match on (resource_name, class_name)
  3. narrow by content_description / text, each applied only if recorded non-None
  4. PASS iff EXACTLY ONE candidate survives
  """
  if action_type in NO_TARGET_ACTIONS:
    return VerifyResult(True, None, 0, 'no-target action; vacuous pass')

  fp = fp.substitute(values or {})

  cands = [
      i for i, e in enumerate(elements)
      if (e.resource_name or None) == fp.resource_name
      and (e.class_name or None) == fp.class_name
  ]
  stage = 'resource_name+class_name'

  if fp.content_description is not None:
    cands = [i for i in cands
             if (elements[i].content_description or None) == fp.content_description]
    stage = 'content_description'

  if fp.text is not None and action_type not in TEXT_EXEMPT_ACTIONS:
    cands = [i for i in cands if (elements[i].text or None) == fp.text]
    stage = 'text'

  if len(cands) == 1:
    return VerifyResult(True, cands[0], 1, 'unique match')
  if not cands:
    return VerifyResult(False, None, 0, f'no element matched (narrowed by {stage})')
  return VerifyResult(
      False, None, len(cands),
      f'ambiguous: {len(cands)} elements matched (narrowed by {stage}); '
      'guessing is the behaviour under study, so this is a failure')
