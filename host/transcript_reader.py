"""Reads the current model + reasoning effort from Claude Code's own transcripts.

ccusage does NOT expose these: its `blocks`/`daily`/`weekly` output carries
model *names* (`models`, `modelsUsed`, `modelBreakdowns`) but drops the
`effort` field entirely during aggregation. Both are present in the raw
transcripts Claude Code writes to `~/.claude/projects/**/*.jsonl`, one per
assistant message, so we read the newest transcript directly.

Only the `effort` and `message.model` fields are read — never conversation
content.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import deque
from typing import Optional

TRANSCRIPT_GLOB = os.path.join(os.path.expanduser("~"), ".claude", "projects", "**", "*.jsonl")

# Only scan the tail — transcripts run to thousands of lines and we just want
# the most recent assistant message.
_TAIL_LINES = 400


def _shorten_model(model: str) -> str:
    """`claude-sonnet-5` -> `sonnet-5`; `claude-haiku-4-5-20251001` -> `haiku-4-5`.

    Keeps the wire field short enough for the 16-byte slot in UsageState.
    """
    name = re.sub(r"^claude-", "", model)
    name = re.sub(r"-\d{8}$", "", name)  # strip trailing date stamp
    return name[:15]


def read_current_model_effort() -> tuple[str, str]:
    """Returns (model, effort) from the most recently modified transcript.

    Returns ("", "") if nothing can be read — a missing transcript is not an
    error worth crashing the poll loop over, the display just shows blanks.
    """
    try:
        files = glob.glob(TRANSCRIPT_GLOB, recursive=True)
        if not files:
            return "", ""
        newest = max(files, key=os.path.getmtime)

        with open(newest, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=_TAIL_LINES)

        model = ""
        effort = ""
        # Walk backwards: first hit is the most recent value for each field.
        for line in reversed(tail):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not model:
                msg = entry.get("message")
                if isinstance(msg, dict) and msg.get("model"):
                    model = _shorten_model(str(msg["model"]))
            if not effort and entry.get("effort"):
                effort = str(entry["effort"])[:7]
            if model and effort:
                break
        return model, effort
    except OSError:
        return "", ""
