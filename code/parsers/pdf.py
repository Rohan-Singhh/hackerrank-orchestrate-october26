"""PDF evidence (text layer via pypdf): deterministic extraction of records.

The organiser PDFs are business documents (support attachment, compliance
journal extract, customer statement, legal register).  Each names its records
with platform ids (support_001, audit_record_003, ORD-2026NNNN -> legal_hold_NNNN)
next to the subject's identifiers.  We extract (resource id, identifiers,
retention dates) per document block; a vision/text model can add to this but
is not required.
"""
from __future__ import annotations

import re
from typing import List

from audit.data import EvidenceFile
from audit.observations import (LEGAL_HOLD_RECORD, SECURITY_AUDIT_LOG, SUPPORT_ATTACHMENT, USER_EXPORT,
                                CATEGORY_SYSTEM, Observation, ParseResult, default_category)

from .common import EMAIL, RESOURCE_ID, USER_ID, ident_fields, subject_values

ORDER = re.compile(r"\bORD-2026(\d{4})\b")
RETAIN = re.compile(r"retain(?:ed)? until (\d{1,2} \w{3} \d{4})", re.IGNORECASE)
PHONE = re.compile(r"\+\d[\d \-()]{7,}\d")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _iso(d: str) -> str:
    try:
        day, mon, year = d.split()
        return f"{int(year):04d}-{MONTHS[mon[:3].title()]:02d}-{int(day):02d}T23:59:59Z"
    except Exception:
        return d


def pdf_text(path) -> str:
    from pypdf import PdfReader
    return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def parse(ef: EvidenceFile, text: str) -> ParseResult:
    res = ParseResult()
    low = text
    emails = EMAIL.findall(low)
    users = USER_ID.findall(low)
    phones = PHONE.findall(low)
    rids = list(dict.fromkeys(RESOURCE_ID.findall(low)))
    # legal register rows: ORD-2026NNNN | user_X | ... | Retain until DATE
    for line in low.splitlines():
        m = ORDER.search(line)
        if m and "retain" in line.lower():
            u = USER_ID.search(line)
            r = RETAIN.search(line)
            rid = f"legal_hold_{m.group(1)}"
            res.observations.append(Observation(ef.file_id, ef.captured_at, rid, LEGAL_HOLD_RECORD, True, system_id="legal_service",
                                                identifiers=ident_fields(user_id=u.group(0) if u else None), subject_refs=subject_values(u.group(0) if u else None),
                                                flags={"retained_until": _iso(r.group(1)) if r else None, "order_reference": m.group(0)}, source="pdf:legal_register"))
    for rid in rids:
        cat = default_category(rid)
        if cat is None:
            continue
        if cat == "SUPPORT_MESSAGE" and re.search(r"attachment|file |upload", low, re.IGNORECASE):
            cat = SUPPORT_ATTACHMENT
        ids = ident_fields(emails[0] if emails else None, phones[0] if phones else None, None, None, users[0] if users else None)
        flags = {}
        r = RETAIN.search(low)
        if r:
            flags["retention_until"] = _iso(r.group(1))
        res.observations.append(Observation(ef.file_id, ef.captured_at, rid, cat, True, system_id=CATEGORY_SYSTEM.get(cat, ""), identifiers=ids,
                                            subject_refs=subject_values(*ids.values()), flags=flags, source="pdf:text"))
    # a customer statement with a statement number is an export document: resolve via NST-...-NNNN
    m = re.search(r"\bNST-\d{4}-\d{4}-(\d{3,5})\b", low)
    if m and not any(o.category == USER_EXPORT for o in res.observations):
        ids = ident_fields(emails[0] if emails else None, phones[0] if phones else None, None, None, users[0] if users else None)
        res.observations.append(Observation(ef.file_id, ef.captured_at, f"export_{m.group(1)}", USER_EXPORT, True, system_id="export_service", identifiers=ids,
                                            subject_refs=subject_values(*ids.values()), flags={"statement": m.group(0)}, source="pdf:statement"))
    if not res.observations and (emails or users):
        res.unparsed_lines.append(low[:200])
    return res
