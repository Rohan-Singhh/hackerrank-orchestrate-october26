"""Media evidence (PNG screenshots, PDFs) -> Observations via a vision/text model.

PDFs: the text layer is extracted with pypdf and sent as text (plus the
record-id regex on the raw text as a guard).  PNGs: sent as images.  The model
output is validated: resource ids must match the platform id grammar, record
types must be known, and for PDFs every id must occur literally in the
extracted text.  The model never sets a status or an evidence id.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from audit.data import EvidenceFile
from audit.observations import (CATEGORIES, CATEGORY_SYSTEM, Observation, ParseResult, default_category)
from parsers.common import RESOURCE_ID, ident_fields, subject_values, to_bool

from .client import Client, ModelUnavailable, extract_json

log = logging.getLogger("vision")
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "vision.md"
ORDER_RE = re.compile(r"\bORD-(\d{4})(\d+)\b")


def pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to read PDF evidence\n  cause: package not installed\n  fix: pip install -r code/requirements.txt") from exc
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _records_to_observations(ef: EvidenceFile, records: List[dict], guard_text: Optional[str]) -> ParseResult:
    res = ParseResult()
    for rec in records or []:
        rid = str(rec.get("resource_id") or "").strip()
        if not RESOURCE_ID.fullmatch(rid):
            res.notes.append(f"dropped id {rid!r}")
            continue
        if guard_text is not None and rid not in guard_text:
            res.notes.append(f"dropped id not in source text {rid!r}")
            continue
        cat = str(rec.get("record_type") or "").upper()
        if cat not in CATEGORIES:
            cat = default_category(rid) or ""
        if not cat:
            res.notes.append(f"dropped unknown category for {rid}")
            continue
        present = rec.get("present")
        present = True if present is None else bool(to_bool(present))
        ids = ident_fields(rec.get("email"), rec.get("phone"), rec.get("email_hash"), rec.get("name"), rec.get("user_id"))
        flags: Dict[str, object] = {}
        if rec.get("enabled") is not None:
            flags["enabled"] = to_bool(rec.get("enabled"))
        if rec.get("restore_allowed") is not None:
            flags["restore_allowed"] = to_bool(rec.get("restore_allowed"))
        if rec.get("retention_until"):
            flags["retention_until"] = rec.get("retention_until")
        res.observations.append(Observation(ef.file_id, ef.captured_at, rid, cat, present, system_id=CATEGORY_SYSTEM.get(cat, ""),
                                            identifiers=ids, subject_refs=subject_values(*ids.values()), flags=flags, source=f"media:{ef.kind}"))
    return res


def read_media(files: List[EvidenceFile], client: Client) -> Dict[str, ParseResult]:
    out: Dict[str, ParseResult] = {}
    system = PROMPT_PATH.read_text(encoding="utf-8")
    for ef in files:
        if ef.kind != "image":
            continue  # PDFs carry a text layer and are parsed deterministically (parsers/pdf.py)
        try:
            if ef.kind == "pdf":
                text = pdf_text(ef.abs_path)
                # legal registers reference orders; map ORD-2026NNNN to legal_hold_NNNN so the id guard can see it
                guard = text + "\n" + "\n".join(f"legal_hold_{m.group(2)}" for m in ORDER_RE.finditer(text))
                prompt = f"Document text (extracted from the PDF {ef.file_path}):\n\"\"\"\n{text[:6000]}\n\"\"\"\nList the customer records it contains as JSON."
                reply = client.complete(prompt, system=system, max_tokens=1500, purpose="pdf")
            else:
                guard = None
                reply = client.complete(f"Screenshot {ef.file_path}. List the customer records visible as JSON.", system=system, images=[ef.abs_path], max_tokens=1500, purpose="vision")
        except ModelUnavailable as exc:
            log.warning("media %s skipped: %s", ef.file_id, str(exc).splitlines()[0])
            if "HTTP 401" in str(exc):
                log.warning("model rejected the API key; remaining media files skipped (catalog fallback applies)")
                break
            continue
        except RuntimeError as exc:
            log.warning("media %s skipped: %s", ef.file_id, exc)
            continue
        if reply is None:
            log.info("media %s: no model available (offline / no key) - skipped", ef.file_id)
            continue
        obj = extract_json(reply)
        records = obj.get("records") if isinstance(obj, dict) else (obj if isinstance(obj, list) else None)
        if records is None:
            log.warning("media %s: unparseable model reply", ef.file_id)
            continue
        out[ef.file_id] = _records_to_observations(ef, records, guard)
    return out
