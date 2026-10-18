"""Shadow predicate: a relaxed variant of fingerprint.verify, NEVER used to act (D47).

The relaxed rule: match on the id tags (resource_name, class_name) first, and
consult content_description / text only when more than one candidate remains,
because some labels change without the element's identity changing (clocks,
dates, counters). The objection: list rows share their id tags, so with one row
visible this passes on a lookalike. Instead of choosing, both are measured:
replay acts on the strict predicate, and this one is evaluated on the same screen
at every step so the analysis can count
  (a) false aborts it would have avoided, and
  (b) wrong targets it would have selected.
"""
from __future__ import annotations

from typing import Any, Optional

from flowagent.fingerprint import (NO_TARGET_ACTIONS, TEXT_EXEMPT_ACTIONS,
                                   Fingerprint, VerifyResult)


def verify_relaxed(fp: Fingerprint, elements: list[Any], action_type: str,
                   values: Optional[dict[str, str]] = None) -> VerifyResult:
  if action_type in NO_TARGET_ACTIONS:
    return VerifyResult(True, None, 0, 'no-target action; vacuous pass')

  fp = fp.substitute(values or {})
  cands = [
      i for i, e in enumerate(elements)
      if (e.resource_name or None) == fp.resource_name
      and (e.class_name or None) == fp.class_name
  ]
  if len(cands) == 1:
    return VerifyResult(True, cands[0], 1, 'unique on id tags; labels not consulted')

  stage = 'resource_name+class_name'
  if len(cands) > 1 and fp.content_description is not None:
    cands = [i for i in cands
             if (elements[i].content_description or None) == fp.content_description]
    stage = 'content_description'
  if len(cands) > 1 and fp.text is not None and action_type not in TEXT_EXEMPT_ACTIONS:
    cands = [i for i in cands if (elements[i].text or None) == fp.text]
    stage = 'text'

  if len(cands) == 1:
    return VerifyResult(True, cands[0], 1, f'unique after {stage}')
  if not cands:
    return VerifyResult(False, None, 0, f'no element matched (narrowed by {stage})')
  return VerifyResult(False, None, len(cands),
                      f'ambiguous: {len(cands)} elements matched (narrowed by {stage})')
