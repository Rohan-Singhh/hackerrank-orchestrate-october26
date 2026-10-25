"""Normalisers shared by several formats (record_lookup rows and collection rows)."""
from __future__ import annotations

import re
from typing import Dict, Optional

from audit.data import EvidenceFile
from audit.observations import (ACCOUNT_TOMBSTONE, ANALYTICS_PROFILE, BACKUP_ARCHIVE, CATEGORIES,
                                CATEGORY_SYSTEM, DATA_FLOW, LEGAL_HOLD_RECORD, SEARCH_INDEX_ENTRY,
                                SECONDARY_IDENTIFIER, SECURITY_AUDIT_LOG, SUPPORT_ATTACHMENT,
                                SUPPORT_MESSAGE, USER_EXPORT, USER_PROFILE, Observation,
                                default_category)

from .common import EMAIL, HASH64, ident_fields, is_null, subject_values, to_bool

PRESENT_WORDS = {"FOUND", "PRESENT", "IDENTIFIABLE", "ACTIVE", "ANONYMIZED", "REDACTED", "BLOCKED", "DISABLED", "EXPOSED", "RETAINED"}
ABSENT_WORDS = {"NOT_FOUND", "ABSENT", "MISSING", "DELETED", "GONE"}


def classify_subject(v: str) -> Dict[str, str]:
    """A subject string from a lookup row: email / hash / user id / name."""
    if is_null(v):
        return {}
    s = str(v).strip()
    if EMAIL.fullmatch(s):
        return {"email": s}
    if HASH64.fullmatch(s.lower()):
        return {"email_hash": s.lower()}
    if re.fullmatch(r"user_\d+", s):
        return {"user_id": s}
    if re.fullmatch(r"\+?[\d\s()-]{7,}", s):
        return {"phone": s}
    return {"name": s}


def lookup_record(ef: EvidenceFile, captured_at: str, system: str, rid: str, rtype: str, result: str, subject: str, source: str) -> Observation:
    rtype = rtype.upper()
    result = result.upper()
    cat = rtype if rtype in CATEGORIES else (default_category(rid) or USER_PROFILE)
    present = None
    if result in ABSENT_WORDS:
        present = False
    elif result in PRESENT_WORDS:
        present = True
    ids = classify_subject(subject)
    # states that mean "no direct identifier left"
    if result in ("ANONYMIZED", "REDACTED"):
        ids = {k: v for k, v in ids.items() if k == "user_id"}
    flags: Dict[str, object] = {"state": result}
    if cat == DATA_FLOW:
        flags["enabled"] = result == "ACTIVE"
    if cat == BACKUP_ARCHIVE:
        flags["restore_allowed"] = result == "ACTIVE"
    refs = subject_values(subject)
    return Observation(ef.file_id, captured_at, rid, cat, present, system_id=system or CATEGORY_SYSTEM.get(cat, ""),
                       identifiers=ids if present else {}, subject_refs=refs, flags=flags, source=source)


def collection_record(ef: EvidenceFile, captured_at: str, system: str, collection: str, rec: dict, source: str) -> Optional[Observation]:
    """Rows from snapshot bundles / replica record_json / inventory-like JSON collections."""
    rid = rec.get("resource_id")
    if not rid:
        return None
    c = collection
    if c == "customer_profiles":
        ids = ident_fields(rec.get("email"), rec.get("phone"), rec.get("email_hash"), rec.get("full_name"), rec.get("customer_reference") or rec.get("user_id"))
        return Observation(ef.file_id, captured_at, rid, USER_PROFILE, True, system_id="account_service", identifiers=ids,
                           subject_refs=subject_values(*ids.values()), flags={"account_status": rec.get("account_status")}, source=source)
    if c == "customer_analytics_reports" or c == "customer_analytics_profiles":
        ids = ident_fields(rec.get("email"), rec.get("phone"), rec.get("email_hash"), None, rec.get("user_id") or rec.get("customer_reference"))
        return Observation(ef.file_id, captured_at, rid, ANALYTICS_PROFILE, True, system_id="analytics_platform", identifiers=ids,
                           subject_refs=subject_values(*ids.values()), source=source)
    if c == "customer_search_documents":
        doc = rec.get("document") or rec
        ids = ident_fields(doc.get("email"), doc.get("phone"), doc.get("email_hash"), doc.get("display_name"), doc.get("customer_reference"))
        return Observation(ef.file_id, captured_at, rid, SEARCH_INDEX_ENTRY, True, system_id="search_service", identifiers=ids,
                           subject_refs=subject_values(*ids.values()), source=source)
    if c == "backup_catalog" or c == "backup_restore_entries":
        subj = rec.get("subject_reference") or rec.get("customer_email") or rec.get("customer_reference")
        refs = subject_values(rec.get("subject_reference"), rec.get("customer_email"), rec.get("customer_reference"))
        return Observation(ef.file_id, captured_at, rid, BACKUP_ARCHIVE, True, system_id="backup_service",
                           identifiers=classify_subject(subj), subject_refs=refs,
                           flags={"restore_allowed": to_bool(rec.get("restore_allowed"))}, source=source)
    if c == "customer_export_jobs" or c == "customer_exports":
        avail = rec.get("download_available")
        present = True if avail is None else bool(to_bool(avail))
        ids = ident_fields(rec.get("owner_email"), rec.get("owner_phone"), None, None, rec.get("customer_reference"))
        return Observation(ef.file_id, captured_at, rid, USER_EXPORT, present, system_id="export_service", identifiers=ids,
                           subject_refs=subject_values(*ids.values()), flags={"expires_at": rec.get("expires_at")}, source=source)
    if c == "deleted_customer_references":
        ids = ident_fields(rec.get("email"), rec.get("phone"), rec.get("email_hash"), rec.get("name"), rec.get("subject_reference"))
        return Observation(ef.file_id, captured_at, rid, ACCOUNT_TOMBSTONE, True, system_id="identity_service", identifiers=ids,
                           subject_refs=subject_values(*ids.values()), source=source)
    if c == "connector_configurations" or c == "connector_subscriptions":
        subj = rec.get("subject_selector") or rec.get("customer_email") or rec.get("customer_reference")
        refs = subject_values(rec.get("subject_selector"), rec.get("customer_email"), rec.get("customer_reference"))
        return Observation(ef.file_id, captured_at, rid, DATA_FLOW, True, system_id="integration_service",
                           identifiers=classify_subject(subj), subject_refs=refs,
                           flags={"enabled": to_bool(rec.get("enabled")), "mode": rec.get("mode")}, source=source)
    if c == "retained_order_records":
        subj = rec.get("subject_reference")
        return Observation(ef.file_id, captured_at, rid, LEGAL_HOLD_RECORD, True, system_id="legal_service",
                           identifiers=classify_subject(subj), subject_refs=subject_values(subj, rec.get("order_reference")),
                           flags={"retained_until": rec.get("retained_until"), "order_reference": rec.get("order_reference")}, source=source)
    if c == "security_audit_events" or c == "security_audit_records":
        subj = rec.get("subject_reference")
        return Observation(ef.file_id, captured_at, rid, SECURITY_AUDIT_LOG, True, system_id="security_service",
                           identifiers=classify_subject(subj), subject_refs=subject_values(subj),
                           flags={"retention_until": rec.get("retention_until"), "event": rec.get("event_name") or rec.get("event_code")}, source=source)
    if c == "support_conversations" or c == "support_records":
        text = rec.get("message_text") or rec.get("message_excerpt") or ""
        ids = ident_fields(rec.get("requester_email"), rec.get("requester_phone"), None, rec.get("requester_name"), rec.get("subject_reference") or rec.get("customer_reference"))
        emails_in_text = EMAIL.findall(text)
        if emails_in_text:
            ids.setdefault("email", emails_in_text[0])
        refs = subject_values(*ids.values(), *emails_in_text)
        return Observation(ef.file_id, captured_at, rid, SUPPORT_MESSAGE, True, system_id="support_platform", identifiers=ids,
                           subject_refs=refs, flags={"ticket_id": rec.get("ticket_id")}, source=source)
    if c == "ticket_attachments":
        up = rec.get("uploaded_by")
        return Observation(ef.file_id, captured_at, rid, SUPPORT_ATTACHMENT, True, system_id="support_platform",
                           identifiers=classify_subject(up), subject_refs=subject_values(up),
                           flags={"file_name": rec.get("file_name"), "ticket_id": rec.get("ticket_id")}, source=source)
    if c == "operational_record_history":
        return lookup_record(ef, rec.get("recorded_at") or captured_at, rec.get("system", ""), rid, rec.get("record_type", ""),
                             rec.get("result", ""), rec.get("lookup_value") or rec.get("subject_reference") or "", source=source)
    return None
