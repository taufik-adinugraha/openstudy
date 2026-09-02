"""Insight-brief generation (decision D11).

Contract, identical for every case:
  1. The case pipeline writes a stats JSON — computed numbers only.
  2. generate() turns it into a short brief via one low-temperature LLM call.
     The prompt forbids any number not present in the stats JSON.
  3. ops/brief REFUSES the result unless every numeric token in it traces to a
     number in that stats JSON, allowing for rounding and percent conversion. A
     prompt that forbids invention is a request; this is the check.
  4. The brief is written with `reviewed: false` and a person has to approve it.
     Generating is not publishing.
  5. The site renders it labeled "generated · reviewed" with the model and timestamp.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

# Which provider actually runs is read from the environment, because the repository
# declared `anthropic` while the only credential present was Gemini's — which is why
# generate() had never once been called. ANSWER_PROVIDER decides; both paths take the
# same system prompt and the same low temperature, and both outputs go through the
# same verification in ops/brief before anything is written.
PROVIDER = os.environ.get("ANSWER_PROVIDER", "anthropic").strip().lower()
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
TEMPERATURE = 0.2
MAX_TOKENS = 600

SYSTEM = """You write a short data brief for an analytics dashboard.
Use ONLY numbers present in the provided stats JSON — never invent, extrapolate,
or round beyond what is given. Plain language, no hype, 120-180 words.
Structure: one-sentence headline finding; two or three supporting observations;
one caveat drawn from the `caveats` field. British-neutral English."""


def _call_anthropic(payload: str) -> tuple[str, str]:
    import anthropic  # deferred import

    message = anthropic.Anthropic().messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=SYSTEM,
        messages=[{"role": "user", "content": payload}],
    )
    return message.content[0].text, ANTHROPIC_MODEL


def _call_gemini(payload: str) -> tuple[str, str]:
    from google import genai  # deferred import
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=payload,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            temperature=TEMPERATURE,
            max_output_tokens=MAX_TOKENS,
        ),
    )
    return resp.text, GEMINI_MODEL


def generate(stats_path: Path, out_path: Path) -> Path:
    """Render the brief markdown from a stats JSON. Returns the output path."""
    stats = json.loads(stats_path.read_text())
    payload = json.dumps(stats, indent=2)
    body, model = {"gemini": _call_gemini}.get(PROVIDER, _call_anthropic)(payload)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path.write_text(
        f"---\ngenerated: {stamp}\nmodel: {model}\n"
        f"vintage: {stats.get('vintage', str(date.today()))}\n"
        f"reviewed: false\n---\n\n{body.strip()}\n"
    )
    return out_path
