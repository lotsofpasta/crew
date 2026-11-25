"""An agent's reasoning layer: one structured Claude call (JSON matching a schema), with a daily call budget and an
optional number check.

Two tiers per agent: `model` (its main model - judgment, anything the owner acts on) and `light_model` (cheaper -
lookups, routing, summaries). ask(light=True) uses the light tier on its own daily count; a failed light call is
retried once on the main model.

The number check (check_numbers=True): every number should be computed in Python and handed to the model as facts;
the model explains and recommends. A reply containing a number that is not in the input (rounding allowed) is
rejected and retried once with the offending numbers named.

Providers (the first one with a key wins):
  roteo      ROTEO_API_KEY - the Roteo gateway (https://roteo.ai), OpenAI-style chat/completions at ROTEO_URL
             (default https://api.roteo.ai/v1). Any other OpenAI-style gateway works the same way: point ROTEO_URL at it.
  anthropic  ANTHROPIC_API_KEY - the official Anthropic SDK, straight to Claude (ANTHROPIC_BASE_URL for a gateway that
             speaks the Anthropic API).
"""
import json
import logging
import os
import re
from pathlib import Path

from .. import clock, config

log = logging.getLogger("crew.brain")
MODEL = config.env("CREW_MODEL", "claude-opus-5-5")
LIGHT_MODEL = config.env("CREW_LIGHT_MODEL", "claude-sonnet-5-5")
EFFORT = config.env("CREW_EFFORT", "high")
ROTEO_URL = config.env("ROTEO_URL", "https://api.roteo.ai/v1").rstrip("/")


class BudgetSpent(Exception):
    pass


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------ numbers
NUM = re.compile(r"(?<![\w.])[-+−]?\$?\d[\d,]*(?:\.\d+)?")


def numbers(text):
    """Numbers in text as (value, decimals): '1,234.5' -> (1234.5, 1). Signs are dropped."""
    out = []
    for m in NUM.finditer(str(text)):
        s = m.group().lstrip("+-−$").replace(",", "")
        try:
            out.append((float(s), len(s.split(".")[1]) if "." in s else 0))
        except ValueError:
            pass
    return out


def unverified(output, source):
    """Numbers in `output` that are not a rounding of some number in `source` (0, 1 and 2 always allowed)."""
    have = {v for v, _ in numbers(source)} | {0.0, 1.0, 2.0}
    bad = []
    for v, dec in numbers(output):
        tol = 0.5 * 10 ** -dec + 1e-9
        if not any(abs(v - h) <= tol for h in have):
            bad.append(v)
    return bad


# ------------------------------------------------------------------------------------------------ provider
def provider():
    """'roteo', 'anthropic' or None - which LLM the agents use."""
    if os.environ.get("ROTEO_API_KEY"):
        return "roteo"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    return None


def available():
    return provider() is not None


def call_llm(system, user, schema, model):
    """One structured call through whichever provider is configured. -> (text, (in, out), served_model)."""
    p = provider()
    if p == "roteo":
        return call_roteo(system, user, schema, model)
    if p == "anthropic":
        return call_claude(system, user, schema, model)
    raise RuntimeError("no LLM key: set ROTEO_API_KEY (or ANTHROPIC_API_KEY) in .env")


# roteo ------------------------------------------------------------------------------------------
class GatewayError(Exception):
    pass


def roteo_chat(messages, model, tools=None, max_tokens=16000, timeout=300.0):
    """One OpenAI-style chat/completions call to the gateway. -> the response JSON. Retries 429/5xx twice."""
    import time
    import httpx
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": "Bearer " + os.environ["ROTEO_API_KEY"]}
    for attempt in range(3):
        try:
            r = httpx.post(ROTEO_URL + "/chat/completions", json=body, headers=headers, timeout=timeout)
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        hint = {401: "the gateway rejected ROTEO_API_KEY", 402: "the Roteo wallet is out of funds - top it up"}.get(r.status_code, "")
        raise GatewayError(f"HTTP {r.status_code}{': ' + hint if hint else ''} {r.text[:200]}")


def call_roteo(system, user, schema, model):
    # The gateway doesn't enforce response_format, so the schema goes in the instructions and ask() parses the JSON.
    sys_msg = (system + "\n\nReply with only one JSON object (no prose, no code fences) matching this JSON schema:\n"
               + json.dumps(schema))
    j = roteo_chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": user}], model)
    choice = (j.get("choices") or [{}])[0]
    if choice.get("finish_reason") in ("content_filter", "refusal"):
        raise Refused("model declined")
    u = j.get("usage") or {}
    return (choice.get("message") or {}).get("content") or "", (u.get("prompt_tokens", 0), u.get("completion_tokens", 0)), j.get("model") or model


# anthropic --------------------------------------------------------------------------------------
_client = None


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=300.0, max_retries=2)
    return _client


def call_claude(system, user, schema, model):
    """One structured call. -> (text, (input_tokens, output_tokens), served_model).
    Server-side fallbacks are on: if the model declines, the API re-runs the request on a fallback model."""
    resp = _anthropic().beta.messages.create(
        model=model, max_tokens=16000, system=system,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}])
    if resp.stop_reason == "refusal":
        raise Refused(getattr(getattr(resp, "stop_details", None), "explanation", None) or "model declined")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return text, (resp.usage.input_tokens, resp.usage.output_tokens), getattr(resp, "model", model)


# ------------------------------------------------------------------------------------------------ brain
class Brain:
    """ask() = one checked structured call, counted against a daily budget kept in data/budget-<agent>.json.

    on_call(phase, info): 'ask' before each call, 'answer' / 'error' after - the agent client uses it to put an 'llm'
    pulse on the exchange. call(system, user, schema, model): a stand-in for the provider (tests, offline demos)."""

    def __init__(self, agent, system, model=None, light_model=None, daily_calls=100, light_calls=None, on_call=None, call=None):
        self.agent, self.system = agent, system
        self.model, self.light_model = model or MODEL, light_model or LIGHT_MODEL
        self.daily_calls = daily_calls
        self.light_calls = 2 * daily_calls if light_calls is None else light_calls
        self.on_call, self._call = on_call, call or call_llm
        self.path = Path(config.DATA_DIR) / f"budget-{agent}.json"

    # budget ----------------------------------------------------------------------------------------
    def usage(self):
        day = clock.local_day()
        try:
            u = json.loads(self.path.read_text())
        except (OSError, ValueError):
            u = {}
        return u if u.get("day") == day else {"day": day, "calls": 0, "light": 0, "in": 0, "out": 0, "errors": 0, "rejected": 0}

    def _count(self, **inc):
        u = self.usage()
        for k, v in inc.items():
            u[k] = u.get(k, 0) + v if isinstance(v, (int, float)) else v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(u))

    def spent(self, light=False):
        u = self.usage()
        return u.get("light", 0) >= self.light_calls if light else u["calls"] >= self.daily_calls

    def _hook(self, phase, info):
        if self.on_call:
            try:
                self.on_call(phase, info)
            except Exception:
                log.debug("on_call hook failed", exc_info=True)

    # calls -----------------------------------------------------------------------------------------
    def _one(self, prompt, schema, light):
        model = self.light_model if light else self.model
        self._hook("ask", {"model": model, "light": light})
        try:
            text, (tin, tout), served = self._call(self.system, prompt, schema, model)
        except Exception as e:
            self._count(**({"light": 1} if light else {"calls": 1}), errors=1)
            self._hook("error", {"model": model, "error": f"{type(e).__name__}: {str(e)[:160]}"})
            if light and not self.spent(False):
                log.warning("light model failed (%s), using the main model", e)
                return self._one(prompt, schema, False)
            raise
        self._count(**({"light": 1} if light else {"calls": 1}), **{"in": tin, "out": tout})
        self._hook("answer", {"model": served, "in": tin, "out": tout, "light": light})
        return text

    def ask(self, user, schema, light=False, check_numbers=None):
        """-> the parsed JSON result, or raises (BudgetSpent, Refused, ValueError, SDK errors).
        check_numbers: a function result -> [strings to check], or None to skip the number check."""
        last_bad = None
        for _ in range(2):
            if self.spent(light):
                raise BudgetSpent(f"{self.agent}: daily {'light ' if light else ''}LLM budget spent")
            prompt = user if not last_bad else (
                user + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED: it contained numbers that are not in the input: "
                + ", ".join(f"{x:g}" for x in last_bad[:12]) + ". Use only numbers given above; describe anything else in words.")
            text = self._one(prompt, schema, light)
            res = json.loads(text[text.find("{"):text.rfind("}") + 1])
            if not check_numbers:
                return res
            last_bad = unverified(" ".join(check_numbers(res)), user)
            if not last_bad:
                return res
            self._count(rejected=1)
        raise ValueError(f"answer failed the number check twice ({', '.join(f'{x:g}' for x in last_bad[:6])})")
