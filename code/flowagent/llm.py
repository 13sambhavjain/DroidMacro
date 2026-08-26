"""Provider-swappable LLM client with token accounting and rate-limit handling.

One `complete()`, provider chosen by config, retries on 429/5xx.

Two things this adds for the research harness:
  * every call returns exact token counts + latency -- these ARE the results
  * exhausted rate limits raise RateLimited so the runner can checkpoint and stop
    cleanly rather than corrupting a partial episode. Groq's free tier makes this a
    certainty, not an edge case (SCOPE_DECISION §6).

The `fake` provider runs the entire harness with zero API calls.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

_MAX_RETRIES = 4
_BASE_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


class RateLimited(RuntimeError):
  """Provider quota exhausted. Runner should checkpoint and stop."""


def load_env(path: Optional[str] = None) -> list[str]:
  """Load the git-ignored .env, two levels above this file, into os.environ. Returns the names loaded (never values).

  Real shell exports win: an already-set variable is not overwritten, so a
  deliberate `export GROQ_API_KEY=...` still takes precedence over the file.
  """
  f = pathlib.Path(path) if path else pathlib.Path(__file__).resolve().parents[2] / '.env'
  if not f.is_file():
    return []
  # Collect first so a LATER line overrides an earlier one -- standard .env
  # behaviour, and the case that matters: a rotated key appended below the dead
  # one. Taking the first match silently sends the revoked credential.
  values: dict[str, str] = {}
  for line in f.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
      continue
    k, v = line.split('=', 1)
    k, v = k.strip(), v.strip().strip('"').strip("'")
    if v:
      values[k] = v
  loaded = []
  for k, v in values.items():
    if not os.environ.get(k):      # a real shell export still wins
      os.environ[k] = v
      loaded.append(k)
  return loaded


@dataclasses.dataclass
class LLMResponse:
  text: str
  prompt_tokens: int
  completion_tokens: int
  latency_s: float
  model: str
  provider: str


def _post(url: str, payload: dict, headers: dict) -> dict:
  req = urllib.request.Request(
      url, data=json.dumps(payload).encode(),
      headers={'Content-Type': 'application/json',
               'User-Agent': 'flowagent-research/0.1', **headers}, method='POST')
  last = None
  for attempt in range(_MAX_RETRIES):
    try:
      with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
      last = e
      if e.code == 429 or 500 <= e.code < 600:
        if attempt == _MAX_RETRIES - 1:
          break
        ra = e.headers.get('Retry-After') if e.headers else None
        try:
          wait = float(ra) if ra else _BASE_BACKOFF * (2 ** attempt)
        except ValueError:
          wait = _BASE_BACKOFF * (2 ** attempt)
        time.sleep(min(wait, _MAX_BACKOFF))
        continue
      raise
  if last is not None and last.code == 429:
    raise RateLimited(f'quota exhausted after {_MAX_RETRIES} attempts: {last}')
  raise last  # type: ignore[misc]


class GroqClient:
  """Groq free tier. Default model is the small one on purpose: gpt-oss-120b is a
  REASONING model that burns completion tokens thinking, which is slow and wasteful
  inside a 15-step agent loop."""
  provider = 'groq'

  def __init__(self, model: str = 'openai/gpt-oss-20b', max_tokens: int = 900):
    self.model, self.max_tokens = model, max_tokens
    self.key = os.environ.get('GROQ_API_KEY')
    if not self.key:
      raise RuntimeError('Set GROQ_API_KEY (free key from console.groq.com)')

  def complete(self, system: str, user: str) -> LLMResponse:
    body = {'model': self.model, 'temperature': 0.0, 'max_tokens': self.max_tokens,
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': user}]}
    if 'gpt-oss' in self.model:
      body['reasoning_effort'] = 'low'
    t0 = time.perf_counter()
    r = _post('https://api.groq.com/openai/v1/chat/completions', body,
              {'Authorization': f'Bearer {self.key}'})
    dt = time.perf_counter() - t0
    u = r.get('usage', {})
    return LLMResponse(r['choices'][0]['message']['content'] or '',
                       u.get('prompt_tokens', 0), u.get('completion_tokens', 0),
                       dt, self.model, self.provider)


class AzureClient:
  """Azure OpenAI via a gateway using the NON-STANDARD path
  {endpoint}/deployments/{deployment}/chat/completions (the official SDK builds
  /openai/deployments/... and 404s on it).
  """
  provider = 'azure'

  def __init__(self, deployment: Optional[str] = None, endpoint: Optional[str] = None,
               api_version: str = '2024-12-01-preview', max_tokens: int = 900):
    self.endpoint = (endpoint or os.environ.get('AZURE_OPENAI_ENDPOINT', '')).rstrip('/')
    self.model = deployment or os.environ.get('AZURE_OPENAI_DEPLOYMENT', '')
    self.api_version, self.max_tokens = api_version, max_tokens
    self.key = os.environ.get('AZURE_OPENAI_API_KEY')
    if not (self.key and self.endpoint and self.model):
      raise RuntimeError('Need AZURE_OPENAI_API_KEY / _ENDPOINT / _DEPLOYMENT')

  def complete(self, system: str, user: str) -> LLMResponse:
    url = (f'{self.endpoint}/deployments/{self.model}/chat/completions'
           f'?api-version={self.api_version}')
    t0 = time.perf_counter()
    r = _post(url, {'messages': [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': user}],
                    'max_completion_tokens': self.max_tokens},
              {'api-key': self.key})
    dt = time.perf_counter() - t0
    u = r.get('usage', {})
    return LLMResponse(r['choices'][0]['message']['content'] or '',
                       u.get('prompt_tokens', 0), u.get('completion_tokens', 0),
                       dt, self.model, self.provider)


class FakeClient:
  """Scripted responses. Exists so the ENTIRE harness -- agent loop, logging,
  checkpointing, flow recording -- is validated before a single token is spent."""
  provider = 'fake'

  def __init__(self, script: Optional[Callable[[str, str], str]] = None,
               model: str = 'fake-0'):
    self.script, self.model, self.calls = script, model, 0

  def complete(self, system: str, user: str) -> LLMResponse:
    self.calls += 1
    text = (self.script(system, user) if self.script
            else json.dumps({'reason': 'fake', 'action': {'action_type': 'wait'}}))
    return LLMResponse(text, len(system + user) // 4, len(text) // 4,
                       0.0, self.model, self.provider)


def make_client(provider: str, model: Optional[str] = None, **kw):
  load_env()
  if provider == 'groq':
    return GroqClient(model or 'openai/gpt-oss-20b', **kw)
  if provider == 'azure':
    return AzureClient(model, **kw)
  if provider == 'fake':
    return FakeClient(**kw)
  raise ValueError(f'unknown provider {provider!r} (groq|azure|fake)')
