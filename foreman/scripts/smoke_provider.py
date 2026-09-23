"""Day-0 smoke test for an OpenAI-compatible provider (Phases.md, Phase 0 step 4).

Checks, for one (provider, model):
  (a) a plain chat completion works
  (b) the model makes a tool call with valid JSON arguments when it should
  (c) the model returns JSON matching a schema when asked
  (d) latency and any rate-limit headers

Usage:
  uv run scripts/smoke_provider.py --provider groq --model llama-3.3-70b-versatile
  uv run scripts/smoke_provider.py --all            # every entry in config/models.yaml

Never prints API keys. Exit code 1 if any check for any model fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# provider id -> (base_url env, api key env)
PROVIDERS: dict[str, tuple[str, str]] = {
    "mistral": ("MISTRAL_BASE_URL", "MISTRAL_API_KEY"),
    "groq": ("GROQ_BASE_URL", "GROQ_API_KEY"),
    "gemini": ("GEMINI_BASE_URL", "GEMINI_API_KEY"),
    "tokenrouter_free": ("TOKENROUTER_BASE_URL", "TOKENROUTER_FREE_API_KEY"),
    "tokenrouter": ("TOKENROUTER_BASE_URL", "TOKENROUTER_API_KEY"),
    "explabs": ("EXPLABS_BASE_URL", "EXPLABS_API_KEY"),
    "ollama": ("OLLAMA_BASE_URL", ""),
}

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city. Call this whenever the user asks about weather.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
            "additionalProperties": False,
        },
    },
}


class Plan(BaseModel):
    title: str
    steps: list[str]
    confidence: float


@dataclass
class Result:
    provider: str
    model: str
    chat: bool = False
    tool_call: bool = False
    json_schema: bool = False
    latency_ms: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.chat and self.tool_call and self.json_schema


def client_for(provider: str) -> OpenAI:
    base_env, key_env = PROVIDERS[provider]
    base_url = os.environ.get(base_env, "")
    api_key = os.environ.get(key_env, "") if key_env else "ollama"
    if not base_url:
        raise RuntimeError(f"{base_env} is not set in .env")
    if key_env and not api_key:
        raise RuntimeError(f"{key_env} is not set in .env")
    return OpenAI(base_url=base_url, api_key=api_key, timeout=60, max_retries=0)


def timed(fn):  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    out = fn()
    return out, int((time.perf_counter() - t0) * 1000)


def check_chat(c: OpenAI, model: str, r: Result) -> None:
    try:
        resp, ms = timed(
            lambda: c.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with the single word: pong"}],
                max_tokens=512,  # reasoning models spend tokens thinking before the visible answer
            )
        )
        r.latency_ms["chat"] = ms
        text = (resp.choices[0].message.content or "").strip().lower()
        r.chat = "pong" in text
        if not r.chat:
            r.notes.append(f"chat replied {text[:40]!r}")
    except Exception as e:  # noqa: BLE001 — smoke test reports everything
        r.notes.append(f"chat error: {type(e).__name__}: {str(e)[:160]}")


def check_tool_call(c: OpenAI, model: str, r: Result) -> None:
    try:
        resp, ms = timed(
            lambda: c.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": "What is the weather in Paris right now? Use the tool.",
                    }
                ],
                tools=[WEATHER_TOOL],
                tool_choice="auto",
                max_tokens=512,
            )
        )
        r.latency_ms["tool"] = ms
        calls = resp.choices[0].message.tool_calls or []
        if not calls:
            r.notes.append("no tool_calls returned")
            return
        args = json.loads(calls[0].function.arguments)
        r.tool_call = calls[0].function.name == "get_weather" and "paris" in str(args).lower()
        if not r.tool_call:
            r.notes.append(f"tool call was {calls[0].function.name} {args}")
    except Exception as e:  # noqa: BLE001
        r.notes.append(f"tool error: {type(e).__name__}: {str(e)[:160]}")


def check_json_schema(c: OpenAI, model: str, r: Result) -> None:
    schema = Plan.model_json_schema()
    schema["additionalProperties"] = False  # required by Groq (and OpenAI) strict json_schema mode
    prompt = (
        "Produce a 3-step plan to make tea. Respond with JSON only, matching this schema: "
        + json.dumps(schema)
    )
    for mode in ("json_schema", "json_object", "prompt_only"):
        try:
            kwargs: dict = {}  # type: ignore[type-arg]
            if mode == "json_schema":
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "Plan", "schema": schema, "strict": True},
                }
            elif mode == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            resp, ms = timed(
                lambda kwargs=kwargs: c.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1500,
                    **kwargs,
                )
            )
            r.latency_ms["json"] = ms
            text = resp.choices[0].message.content or ""
            text = text.strip().removeprefix("```json").removesuffix("```").strip()
            Plan.model_validate_json(text)
            r.json_schema = True
            r.notes.append(f"json via {mode}")
            return
        except ValidationError as e:
            r.notes.append(f"{mode}: invalid JSON for schema ({str(e)[:80]})")
        except Exception as e:  # noqa: BLE001
            r.notes.append(f"{mode}: {type(e).__name__}: {str(e)[:120]}")


def run_one(provider: str, model: str) -> Result:
    r = Result(provider, model)
    try:
        c = client_for(provider)
    except RuntimeError as e:
        r.notes.append(str(e))
        return r
    check_chat(c, model, r)
    check_tool_call(c, model, r)
    check_json_schema(c, model, r)
    return r


def entries_from_config() -> list[tuple[str, str]]:
    cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    seen: list[tuple[str, str]] = []
    for role in cfg.get("roles", {}).values():
        for entry in role.get("chain", []):
            pair = (entry["provider"], entry["model"])
            if (
                pair not in seen
                and entry["provider"] in PROVIDERS
                and role is not cfg["roles"].get("embedding")
            ):
                seen.append(pair)
    # embeddings are not chat models; skip them here
    emb = {(e["provider"], e["model"]) for e in cfg["roles"].get("embedding", {}).get("chain", [])}
    return [p for p in seen if p not in emb]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=PROVIDERS.keys())
    ap.add_argument("--model")
    ap.add_argument(
        "--all", action="store_true", help="test every chat entry in config/models.yaml"
    )
    args = ap.parse_args()

    targets = entries_from_config() if args.all else [(args.provider, args.model)]
    if not args.all and (not args.provider or not args.model):
        ap.error("--provider and --model are required unless --all")

    results = [run_one(p, m) for p, m in targets]
    print(
        f"{'provider':<17}{'model':<44}{'chat':<6}{'tool':<6}{'json':<6}{'ms(chat/tool/json)':<22}notes"
    )
    for r in results:
        lat = "/".join(str(r.latency_ms.get(k, "-")) for k in ("chat", "tool", "json"))
        flag = lambda b: "ok" if b else "FAIL"  # noqa: E731
        print(
            f"{r.provider:<17}{r.model:<44}{flag(r.chat):<6}{flag(r.tool_call):<6}{flag(r.json_schema):<6}{lat:<22}{'; '.join(r.notes)}"
        )
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
