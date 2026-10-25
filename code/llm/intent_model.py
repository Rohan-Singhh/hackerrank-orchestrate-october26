"""Model-backed intent classification for request texts the regex cannot settle."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from .client import Client, ModelUnavailable, extract_json

log = logging.getLogger("intent_model")
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "intent.md"


def make_intent_model(client: Client) -> Callable[[str], Optional[str]]:
    system = PROMPT_PATH.read_text(encoding="utf-8")
    state = {"disabled": False}

    def classify(text: str) -> Optional[str]:
        if state["disabled"]:
            return None
        prompt = 'Customer message:\n"""\n' + text + '\n"""\nReply with the JSON object only.'
        try:
            reply = client.complete(prompt, system=system, max_tokens=120, purpose="intent")
        except ModelUnavailable as exc:
            state["disabled"] = True
            log.warning("intent model disabled for this run: %s", str(exc).splitlines()[0])
            return None
        if reply is None:
            return None
        obj = extract_json(reply)
        if isinstance(obj, dict):
            v = str(obj.get("intent", "")).strip().upper().replace("-", "_")
            if v in ("DELETION", "NON_DELETION"):
                return v
        up = reply.upper()
        if "NON_DELETION" in up or "NON-DELETION" in up:
            return "NON_DELETION"
        if "DELETION" in up:
            return "DELETION"
        return None

    return classify
