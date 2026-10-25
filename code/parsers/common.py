"""Shared helpers: key=value tokenizer, id/email detection, state words."""
from __future__ import annotations

import json
import re
from typing import Dict, Optional

KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S+)')
RESOURCE_ID = re.compile(r"\b((?:account|search|analytics|support|export|backup|data_flow|audit_record|tombstone|legal_hold|marketing|cache|email_delivery|object)_[0-9]+)\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH64 = re.compile(r"\b[0-9a-f]{64}\b")
USER_ID = re.compile(r"\buser_[0-9]+\b")
TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

NULLS = {"", "null", "none", "n/a", "-", "no direct identifier", "[redacted]"}


def is_null(v) -> bool:
    return v is None or str(v).strip().lower() in NULLS


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v.replace('\\"', '"')


def tokens(line: str) -> Dict[str, str]:
    """Extract key=value pairs from any of the log syntaxes (incl. JSON lines and
    key=value nested inside an escaped message="..." payload)."""
    s = line.strip()
    d: Dict[str, str] = {}
    m = re.match(r"^(\S+)\s+(\{.*\})\s*$", s)
    if m:
        try:
            obj = json.loads(m.group(2))
            d = {k: ("" if v is None else str(v)) for k, v in obj.items()}
            d["_ts"] = m.group(1)
            return d
        except json.JSONDecodeError:
            pass
    for k, v in KV.findall(s):
        val = unquote(v)
        if k == "message" and "=" in val:
            for k2, v2 in KV.findall(val):
                d[k2] = unquote(v2)
        d[k] = val
    mts = TS.match(s)
    if mts:
        d.setdefault("_ts", mts.group(0))
    return d


def event_name(line: str, d: Dict[str, str]) -> Optional[str]:
    if d.get("event"):
        return d["event"]
    if d.get("event_type"):
        return d["event_type"]
    m = re.search(r"\]\s+([a-z_]+):", line)  # bracketed: [INFO] [svc] [tr] event: k=v
    if m:
        return m.group(1)
    m = re.search(r"\|\s*([a-z_]+)\s*\|\s*[a-z_]+=", line)  # pipe: | event | k=v
    if m:
        return m.group(1)
    return None


def trace_id(line: str, d: Dict[str, str]) -> Optional[str]:
    for k in ("trace_id", "requestId", "request_id"):
        if d.get(k):
            return d[k]
    m = re.search(r"\b(tr-[0-9a-z-]+|rb-[0-9]+-[0-9]+)\b", line)
    return m.group(1) if m else None


def subject_values(*vals) -> set:
    out = set()
    for v in vals:
        if is_null(v):
            continue
        out.add(str(v).strip().lower())
    return out


def ident_fields(email=None, phone=None, email_hash=None, name=None, user_id=None) -> Dict[str, str]:
    d = {}
    if not is_null(email):
        d["email"] = str(email).strip()
    if not is_null(phone):
        d["phone"] = str(phone).strip()
    if not is_null(email_hash):
        d["email_hash"] = str(email_hash).strip()
    if not is_null(name):
        d["name"] = str(name).strip()
    if not is_null(user_id):
        d["user_id"] = str(user_id).strip()
    return d


def to_bool(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "enabled", "active", "allow", "allowed"):
        return True
    if s in ("0", "false", "no", "n", "disabled", "stopped", "deny", "denied", "blocked"):
        return False
    return None
