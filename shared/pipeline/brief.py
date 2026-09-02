"""Insight-brief generation (decision D11).

Contract, identical for every case:
  1. The case pipeline writes a stats JSON — computed numbers only.
  2. generate() turns it into a short brief via one low-temperature LLM call.
     The prompt forbids any number not present in the stats JSON.
  3. The brief is committed on a branch and opened as a PR.
     MERGING THE PR IS THE HUMAN REVIEW. Unmerged brief = site keeps the old one.
  4. The site renders it labeled "generated · reviewed" with a timestamp.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

MODEL = "claude-haiku-4-5-20251001"

SYSTEM = """You write a short data brief for an analytics dashboard.
Use ONLY numbers present in the provided stats JSON — never invent, extrapolate,
or round beyond what is given. Plain language, no hype, 120-180 words.
Structure: one-sentence headline finding; two or three supporting observations;
one caveat drawn from the `caveats` field. British-neutral English."""


def generate(stats_path: Path, out_path: Path) -> Path:
    """Render the brief markdown from a stats JSON. Returns the output path."""
    import anthropic  # deferred import

    stats = json.loads(stats_path.read_text())
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=600,
        temperature=0.2,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(stats, indent=2)}],
    )
    body = message.content[0].text
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path.write_text(
        f"---\ngenerated: {stamp}\nvintage: {stats.get('vintage', str(date.today()))}\n"
        f"reviewed: false\n---\n\n{body}\n"
    )
    return out_path
