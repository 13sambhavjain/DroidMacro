"""Deterministic flow trimming -- the simulated approval step (D41).

The recorded flows are raw baseline trajectories, mistakes included. Measured on
the 7 flows (2026-10-12):
  * MarkorDeleteNote s1 deletes the note, then long-presses the decoy
    `copy_friendly_koala...` and ends with status=infeasible.
  * SimpleCalendarDeleteOneEvent s2 deletes at step 3, then opens a second event
    and switches views.
  * FilesDeleteFile s2 opens and backs out of the app twice before starting.

Framing B has a human approve the step list, and that human would delete these.
No such person is available, so a FIXED rule stands in for them. It is applied before any
replay, identically to both arms, and disclosed. It must not be tuned per flow:
a rule edited after looking at replay results would be fitting the experiment.

Three rules, in order:
  R1. drop `status` steps -- replay ends when the steps run out
  R2. drop an `open_app` immediately followed by `navigate_back` (both steps)
  R3. if the flow contains an irreversible commit, drop everything after the
      LAST commit -- the task is finished once the delete is confirmed

Consecutive duplicate `open_app` steps are KEPT: the second launch may have been
needed (slow first launch), and re-opening the foreground app is harmless.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Closed set of destructive verbs (SCOPE_DECISION §2). Matched as whole words on
# the recorded target's text / content_description.
DESTRUCTIVE_VERBS = ('delete', 'remove', 'send', 'share', 'call', 'pay')

# Positive buttons of a confirm dialog. android:id/button1 is AlertDialog's
# positive button whatever its label says.
CONFIRM_LABELS = frozenset({'ok', 'yes', 'delete', 'confirm', 'remove'})
CONFIRM_RESOURCE = 'android:id/button1'


def _label(target: Optional[dict]) -> str:
  if not target:
    return ''
  return ' '.join(x for x in (target.get('text'), target.get('content_description'))
                  if x).strip()


def is_destructive(target: Optional[dict]) -> bool:
  lab = _label(target).lower()
  return any(re.search(rf'\b{v}\b', lab) for v in DESTRUCTIVE_VERBS)


def is_confirm(target: Optional[dict]) -> bool:
  if not target:
    return False
  if target.get('resource_name') == CONFIRM_RESOURCE:
    return True
  return _label(target).lower() in CONFIRM_LABELS


def label_irreversible(steps: list[dict]) -> list[bool]:
  """Mark the step that COMMITS an irreversible action.

  A destructive step followed (at the next targeted step) by a confirm button is
  committed by the confirm; a destructive step with no confirm after it commits
  itself. Labels are heuristic and get spot-checked by hand (SCOPE_DECISION §2).
  """
  out = [False] * len(steps)
  targeted = [i for i, s in enumerate(steps) if s.get('target')]
  for n, i in enumerate(targeted):
    if not is_destructive(steps[i]['target']):
      continue
    nxt = targeted[n + 1] if n + 1 < len(targeted) else None
    if nxt is not None and is_confirm(steps[nxt]['target']):
      out[nxt] = True
    else:
      out[i] = True
  return out


def _atype(step: dict) -> str:
  return (step.get('action') or {}).get('action_type', '')


def trim(steps: list[dict]) -> tuple[list[dict], dict[str, Any]]:
  """Apply R1-R3. Returns (kept steps, report of what was dropped and why)."""
  dropped: list[tuple[int, str]] = []
  kept: list[tuple[int, dict]] = []

  for i, s in enumerate(steps):                                  # R1
    if _atype(s) == 'status':
      dropped.append((i, 'R1 status'))
    else:
      kept.append((i, s))

  out: list[tuple[int, dict]] = []                               # R2
  j = 0
  while j < len(kept):
    if (j + 1 < len(kept) and _atype(kept[j][1]) == 'open_app'
        and _atype(kept[j + 1][1]) == 'navigate_back'):
      dropped += [(kept[j][0], 'R2 open->back'), (kept[j + 1][0], 'R2 open->back')]
      j += 2
      continue
    out.append(kept[j])
    j += 1

  commits = label_irreversible([s for _, s in out])              # R3
  if any(commits):
    last = max(n for n, c in enumerate(commits) if c)
    dropped += [(i, 'R3 after last commit') for i, _ in out[last + 1:]]
    out = out[:last + 1]

  kept_steps = [s for _, s in out]
  return kept_steps, {
      'n_recorded': len(steps),
      'n_kept': len(kept_steps),
      'kept_source_steps': [i for i, _ in out],
      'dropped': sorted(dropped),
      'irreversible_steps': [n for n, c in enumerate(label_irreversible(kept_steps)) if c],
  }
