"""Request intent gate: does the text authorise deletion of the account?

Layer 1 is a multilingual regex: an explicit "keep my account" marker wins,
then a strong deletion phrase without any non-deletion marker short-circuits
to DELETION.  Everything else goes to the model (llm.intent_model) through a
cache; when no model is available the regex verdict is used and the case is
flagged so the review list shows it.
"""
from __future__ import annotations

import re
from typing import Callable, Optional, Tuple

STRONG_KEEP_PATTERNS = [
    r"\b(do not|don't|dont|please do not|please don't) (close|delete|remove|erase|touch|change)\b",
    r"\bkeep (the|my|this) account\b",
    r"\bkeep it (as it is|open|active)\b",
    r"\bi (still|want to) (use|need|want) (it|the account|this account|my account)\b",
    r"\bcontinue using\b",
    r"\bwithout (removing|deleting|closing)\b",
    r"\b(marketing|promotional|newsletter) (lists?|emails?|messages?) only\b",
    r"\bonly\b[^.]*\b(marketing|promotional|newsletter)\b",
    r"\bstop the (promotional|marketing) emails\b",
    r"\bunsubscribe\b",
    r"\bopt[- ]?out\b",
    r"\bnot a (deletion|removal|closure) request\b",
    r"\bnot a request to (erase|delete|remove|close)\b",
    r"\bthis is an? (information|access|data access|correction|portability) request\b",
    r"\bkeep (the|my) account open\b",
]
DELETE_PATTERNS = [
    r"\b(delet\w*|eras\w*|remov\w*|wip\w*|purg\w*|forget me|right to be forgotten)\b",
    r"\bclos(e|ed|ing)\b[^.]*\baccount\b|\baccount\b[^.]*\bclos(e|ed|ing)\b",
    r"\bsuppression\b|\bsupprim\w*|\beffac\w*",            # fr
    r"\belimin\w*|\bborr\w*|\bsuprim\w*",                  # es/pt/it
    r"\bexclus[aã]o\b|\bexclu\w*|\bapag\w*",               # pt
    r"\bl[öo]sch\w*|\bgel[öo]scht\b",                      # de
    r"\bcancell\w*",                                       # it
    r"\bverwijder\w*",                                     # nl
    r"削除|消去|抹消",                                       # ja
    r"삭제|지워",                                            # ko
    r"删除|註銷|注销",                                       # zh
    r"\busu[nń]\w*|\bsmaz\w*",                             # pl/cs
    r"\bradera\b|\bta bort\b|\bslett\w*|\bslet\w*",        # sv/no/da
    r"\bуда[лн]\w*",                                       # ru
    r"\bsil(in|me|mek)\b|\bkald[ıi]r\w*",                  # tr
]
NON_DELETION_PATTERNS = [
    r"\bdownload my\b", r"\bexport (a copy|my)\b", r"\ba copy of (my|the) (information|data|invoices?)\b", r"\bsend me a copy\b",
    r"\baccess to my\b", r"\btell me what\b", r"\bwhat (personal )?information you hold\b",
    r"\b(correct|fix|update|change) (my|the) (surname|name|email|address|phone)\b", r"\bmisspell", r"\breset(ting)? (the|my) (login|password)\b",
    r"\binvoices?\b", r"\bportable format\b",
]
STRONG_KEEP_RE = [re.compile(p, re.IGNORECASE) for p in STRONG_KEEP_PATTERNS]
DEL_RE = [re.compile(p, re.IGNORECASE) for p in DELETE_PATTERNS]
NON_RE = [re.compile(p, re.IGNORECASE) for p in NON_DELETION_PATTERNS]


def regex_verdict(text: str) -> Tuple[str, float]:
    """Return (verdict, confidence) from patterns alone."""
    t = text or ""
    if any(r.search(t) for r in STRONG_KEEP_RE):
        return "NON_DELETION", 0.9
    strong = any(r.search(t) for r in DEL_RE)
    other = any(r.search(t) for r in NON_RE)
    if strong and not other:
        return "DELETION", 0.95
    if other and not strong:
        return "NON_DELETION", 0.9
    if strong and other:
        return "DELETION", 0.55  # mixed ("delete ... but send me a copy first"): the model decides
    if not t.strip():
        return "DELETION", 0.3
    return "DELETION", 0.4


class IntentGate:
    def __init__(self, model_fn: Optional[Callable[[str], Optional[str]]] = None, strict_offline: bool = False):
        self.model_fn = model_fn
        self.strict_offline = strict_offline
        self.stats = {"regex": 0, "model": 0, "fallback": 0}

    def __call__(self, text: str) -> Tuple[str, str]:
        verdict, conf = regex_verdict(text)
        if conf >= 0.9:
            self.stats["regex"] += 1
            return verdict, f"regex:{conf}"
        if self.model_fn is not None:
            v = self.model_fn(text)
            if v in ("DELETION", "NON_DELETION"):
                self.stats["model"] += 1
                return v, "model"
        if self.strict_offline:
            raise RuntimeError(f"intent cache miss in --offline --strict mode for text: {text[:60]!r}")
        self.stats["fallback"] += 1
        return verdict, f"regex-fallback:{conf}"
