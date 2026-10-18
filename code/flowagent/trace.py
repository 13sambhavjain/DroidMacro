"""Structured per-step logging + resumable checkpointing.

Every number in the final results table comes out of these JSONL files, so the
schema is fixed early and written append-only: an episode is appended the moment it
finishes, which is what makes a run resumable after a rate-limit kill.

Groq's free tier guarantees interruption (SCOPE_DECISION §6), so resume is a
requirement, not a nicety.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
from typing import Any, Optional


@dataclasses.dataclass
class StepRecord:
  i: int
  action: dict
  target: Optional[dict] = None          # Fingerprint.to_dict() of the target element
  verify_ok: Optional[bool] = None       # None in baseline (no predicate ran)
  verify_reason: Optional[str] = None
  verify_candidates: Optional[int] = None
  source: str = 'llm'                    # llm | replay | fallback
  llm_prompt_tokens: int = 0
  llm_completion_tokens: int = 0
  llm_latency_s: float = 0.0
  obs_latency_s: float = 0.0
  act_latency_s: float = 0.0
  n_elements: int = 0
  tree_chars: int = 0
  raw_response: str = ''
  error: Optional[str] = None
  # replay only
  recorded_index: Optional[int] = None   # index the flow recorded; unverified acts on it
  acted_on: Optional[dict] = None        # fingerprint of the element actually acted on
  irreversible: bool = False             # trim.label_irreversible: this step commits
  shadow: Optional[dict] = None          # predicates evaluated but NOT acted on (D47)


@dataclasses.dataclass
class EpisodeRecord:
  episode_id: str
  task: str
  seed: int
  condition: str            # baseline | verified_replay | unverified_replay
  drift: Optional[str]      # None | L0 | L1 | L3
  goal: str = ''
  params: dict = dataclasses.field(default_factory=dict)
  provider: str = ''
  model: str = ''
  steps: list = dataclasses.field(default_factory=list)
  success: Optional[bool] = None
  done_reason: str = ''      # done | step_budget | error | rate_limited
  wall_s: float = 0.0
  started_at: str = ''
  # L3 guard (SCOPE_DECISION critique): an episode only COUNTS as drifted if the
  # observed start state actually differs from the recording's.
  start_tree_hash: Optional[str] = None
  recorded_start_tree_hash: Optional[str] = None
  # replay only
  flow_seed: Optional[int] = None        # seed the flow was RECORDED on; `seed` is the device's
  trim: dict = dataclasses.field(default_factory=dict)
  decoys: list = dataclasses.field(default_factory=list)
  abort_step: Optional[int] = None
  outcome: dict = dataclasses.field(default_factory=dict)   # devicestate.diff
  # tree hashes include the status-bar clock, so `drift_confirmed` is true for
  # any two observations a minute apart (D48). Replay confirms drift from the
  # inventory instead: L3a = intended target absent, L3b = target + decoys present.
  drift_check: Optional[bool] = None

  @property
  def drift_confirmed(self) -> Optional[bool]:
    if self.recorded_start_tree_hash is None or self.start_tree_hash is None:
      return None
    return self.start_tree_hash != self.recorded_start_tree_hash

  def totals(self) -> dict:
    return {
        'llm_calls': sum(1 for s in self.steps if s.get('source') in ('llm', 'fallback')),
        'prompt_tokens': sum(s.get('llm_prompt_tokens', 0) for s in self.steps),
        'completion_tokens': sum(s.get('llm_completion_tokens', 0) for s in self.steps),
        'steps': len(self.steps),
        'aborts': sum(1 for s in self.steps if s.get('verify_ok') is False),
    }

  def to_dict(self) -> dict:
    d = dataclasses.asdict(self)
    d['totals'] = self.totals()
    d['drift_confirmed'] = self.drift_confirmed
    return d


class TraceWriter:
  """Append-only JSONL. One line per finished episode."""

  def __init__(self, path: str | pathlib.Path):
    self.path = pathlib.Path(path)
    self.path.parent.mkdir(parents=True, exist_ok=True)

  def append(self, ep: EpisodeRecord) -> None:
    with self.path.open('a') as f:
      f.write(json.dumps(ep.to_dict(), default=str) + '\n')

  def completed_ids(self) -> set[str]:
    """Episode ids already on disk -- the resume mechanism."""
    if not self.path.exists():
      return set()
    out = set()
    for line in self.path.read_text().splitlines():
      if not line.strip():
        continue
      try:
        out.add(json.loads(line)['episode_id'])
      except (json.JSONDecodeError, KeyError):
        continue
    return out

  def load(self) -> list[dict]:
    if not self.path.exists():
      return []
    return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]


def episode_id(task: str, seed: int, condition: str, drift: Optional[str]) -> str:
  return f'{task}|seed{seed}|{condition}|{drift or "-"}'


def tree_hash(elements: list[Any]) -> str:
  import hashlib
  key = '\n'.join(
      f'{e.resource_name}|{e.content_description}|{e.text}|{e.class_name}'
      for e in elements)
  return hashlib.sha256(key.encode()).hexdigest()[:16]
