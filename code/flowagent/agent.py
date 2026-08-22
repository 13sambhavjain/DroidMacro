"""The baseline agent loop: observe -> ask LLM -> act -> repeat.

Written rather than reusing AndroidWorld's T3A for two reasons: the replay path and
the verification predicate have to be injected into this loop later, and the per-step
records it emits (tokens, latency, target fingerprint) are the raw material for every
reported number.

Recording is not a separate mode -- a successful baseline episode IS a candidate flow
(SCOPE_DECISION §0 step 1), so the target fingerprint is captured on every step.
"""
from __future__ import annotations

import dataclasses
import json
import re
import time
from typing import Any, Optional

from android_world.env import json_action

from flowagent.fingerprint import NO_TARGET_ACTIONS, Fingerprint
from flowagent.trace import StepRecord

SYSTEM = """You are an agent operating an Android phone to complete a task.
At each step you see the screen as a numbered list of UI elements. Choose ONE action.

Available actions:
  {"action_type":"click","index":<int>}
  {"action_type":"long_press","index":<int>}
  {"action_type":"input_text","text":"<str>","index":<int>}
  {"action_type":"scroll","direction":"up|down|left|right"}
  {"action_type":"open_app","app_name":"<str>"}
  {"action_type":"navigate_back"}
  {"action_type":"navigate_home"}
  {"action_type":"keyboard_enter"}
  {"action_type":"wait"}
  {"action_type":"status","goal_status":"complete"}      <- when the task is done
  {"action_type":"status","goal_status":"infeasible"}    <- when it cannot be done

Rules:
- Reply with ONE JSON object and nothing else: {"reason":"<short>","action":{...}}
- `index` must be an index from the element list on THIS screen.
- Emit status/complete as soon as the goal is satisfied. Do not keep going.
"""


def render_elements(elements: list[Any]) -> str:
  lines = []
  for i, e in enumerate(elements):
    parts = [f'{i}']
    cls = (e.class_name or '').split('.')[-1]
    if cls:
      parts.append(cls)
    if e.text:
      parts.append(f'text={e.text!r}')
    if e.content_description:
      parts.append(f'desc={e.content_description!r}')
    if e.hint_text:
      parts.append(f'hint={e.hint_text!r}')
    flags = ''.join(c for c, v in
                    (('C', e.is_clickable), ('L', e.is_long_clickable),
                     ('E', e.is_editable), ('S', e.is_scrollable),
                     ('X', e.is_checked)) if v)
    if flags:
      parts.append(f'[{flags}]')
    lines.append(' '.join(parts))
  return '\n'.join(lines)


def parse_action(raw: str) -> tuple[dict, str]:
  """Pull the JSON action out of a model reply. Returns (action_dict, reason)."""
  txt = raw.strip()
  txt = re.sub(r'^```(?:json)?|```$', '', txt, flags=re.MULTILINE).strip()
  obj = None
  # Prefer the last complete top-level object: reasoning models often narrate first.
  for m in reversed(list(re.finditer(r'\{.*\}', txt, re.DOTALL))):
    for cand in (m.group(0), *re.findall(r'\{[^{}]*\}', m.group(0))):
      try:
        o = json.loads(cand)
      except json.JSONDecodeError:
        continue
      if isinstance(o, dict) and ('action' in o or 'action_type' in o):
        obj = o
        break
    if obj:
      break
  if obj is None:
    raise ValueError(f'no JSON action found in response: {raw[:200]!r}')
  action = obj.get('action', obj)
  if not isinstance(action, dict) or 'action_type' not in action:
    raise ValueError(f'malformed action: {action!r}')
  return action, str(obj.get('reason', ''))[:300]


def to_json_action(action: dict) -> json_action.JSONAction:
  allowed = {'action_type', 'index', 'x', 'y', 'text', 'direction',
             'goal_status', 'app_name', 'keycode'}
  return json_action.JSONAction(**{k: v for k, v in action.items()
                                   if k in allowed and v is not None})


@dataclasses.dataclass
class StepOutcome:
  record: StepRecord
  done: bool
  fingerprint: Optional[Fingerprint] = None


class BaselineAgent:
  """LLM decides every step. Also the recorder: each step captures the target
  element's fingerprint so a successful episode becomes a candidate flow."""

  def __init__(self, env, client, max_history: int = 8):
    self.env, self.client, self.max_history = env, client, max_history
    self.history: list[str] = []

  def reset(self) -> None:
    self.history = []

  def step(self, goal: str, i: int) -> StepOutcome:
    t0 = time.perf_counter()
    state = self.env.get_state(wait_to_stabilize=True)
    obs_dt = time.perf_counter() - t0
    elements = state.ui_elements
    rendered = render_elements(elements)

    hist = ('\n'.join(f'{n+1}. {h}' for n, h in
                      enumerate(self.history[-self.max_history:]))
            or '(nothing yet)')
    user = (f'GOAL: {goal}\n\nPREVIOUS ACTIONS:\n{hist}\n\n'
            f'SCREEN ({len(elements)} elements):\n{rendered}\n\n'
            'Reply with one JSON object.')

    rec = StepRecord(i=i, action={}, source='llm', obs_latency_s=obs_dt,
                     n_elements=len(elements), tree_chars=len(rendered))

    resp = self.client.complete(SYSTEM, user)
    rec.llm_prompt_tokens = resp.prompt_tokens
    rec.llm_completion_tokens = resp.completion_tokens
    rec.llm_latency_s = resp.latency_s
    rec.raw_response = resp.text[:1500]

    try:
      action, reason = parse_action(resp.text)
    except ValueError as exc:
      rec.error = f'parse: {exc}'
      self.history.append(f'(unparseable reply)')
      return StepOutcome(rec, done=False)

    rec.action = action
    fp = None
    idx = action.get('index')
    if (action.get('action_type') not in NO_TARGET_ACTIONS
        and isinstance(idx, int) and 0 <= idx < len(elements)):
      fp = Fingerprint.from_element(elements[idx])
      rec.target = fp.to_dict()
    elif isinstance(idx, int):
      rec.error = f'index {idx} out of range (0..{len(elements)-1})'

    if action.get('action_type') == 'status':
      self.history.append(f'status:{action.get("goal_status")}')
      return StepOutcome(rec, done=True, fingerprint=fp)

    t0 = time.perf_counter()
    try:
      self.env.execute_action(to_json_action(action))
    except Exception as exc:                       # noqa: BLE001 - log and continue
      rec.error = f'{type(exc).__name__}: {exc}'
    rec.act_latency_s = time.perf_counter() - t0

    desc = action.get('action_type', '?')
    if fp is not None:
      desc += f' -> {fp.text or fp.content_description or fp.resource_name}'
    if action.get('text'):
      desc += f' text={action["text"]!r}'
    self.history.append(desc)
    return StepOutcome(rec, done=False, fingerprint=fp)
