"""Log evidence.

Three families share one tokenizer:
  * readback traces (`resource_lookup_trace_*`): started/completed pairs with
    explicit fields on the completed line;
  * observability exports (service_events, structured_service_events,
    application_trace, cloud_runtime_export, syslog_archive, operations_stream):
    the same event vocabulary in six syntaxes; the subject key sits on the
    `*_started` line and the result on a later line with the same trace id;
  * record_lookup exports (notification_delivery, search_index_diagnostic) and
    the support replica log.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from audit.data import EvidenceFile
from audit.observations import (ANALYTICS_PROFILE, BACKUP_ARCHIVE, DATA_FLOW, SEARCH_INDEX_ENTRY,
                                SECONDARY_IDENTIFIER, SECURITY_AUDIT_LOG, SUPPORT_ATTACHMENT,
                                SUPPORT_MESSAGE, USER_EXPORT, USER_PROFILE, Observation, ParseResult)

from .common import EMAIL, RESOURCE_ID, event_name, ident_fields, is_null, subject_values, to_bool, tokens, trace_id
from .records import classify_subject, lookup_record

STARTED_KEYS = ("lookup_key", "match_key", "subject_key", "subject_ref", "owner_key", "scan_key", "query")


def parse(ef: EvidenceFile, text: str) -> ParseResult:
    res = ParseResult()
    lines = text.splitlines()
    # trace id -> subject key seen on a *_started line
    started: Dict[str, Dict[str, str]] = {}
    for n, line in enumerate(lines):
        d = tokens(line)
        ev = event_name(line, d)
        if not ev:
            continue
        tid = trace_id(line, d) or f"line{n}"
        ts = d.get("_ts") or ef.captured_at
        if ev.endswith("_started"):
            key = next((d[k] for k in STARTED_KEYS if d.get(k)), None)
            started[tid] = {"key": key or "", "event": ev, "resource_id": d.get("resource_id", "")}
            if ev == "export_started":
                started.pop(tid, None)
            continue
        if ev == "record_lookup":
            res.observations.append(lookup_record(ef, ts, d.get("system", ""), d["resource_id"], d.get("record_type", ""), d.get("result", ""), d.get("subject_reference", ""), source=f"log:record_lookup:{d.get('result')}"))
            continue
        obs = _event_obs(ef, ts, ev, d, started.get(tid, {}), n)
        if obs is not None:
            res.observations.append(obs)
        elif d.get("resource_id") or EMAIL.search(line):
            if ev not in NOISE_EVENTS:
                res.unparsed_lines.append(line.strip())
    return res


NOISE_EVENTS = {"export_started", "export_completed", "connection_acquired", "dependency_retry", "dependency_recovered", "cache_lookup",
                "cache_refresh", "consumer_lag", "pool_sample", "connection_pool_pressure", "properties_reloaded", "lease_extended",
                "telemetry_flushed", "flag_snapshot_loaded", "slow_call_rate", "delivery_abandoned", "batch_started", "checkpoint_saved",
                "thumbnail_failed", "flag_evaluated", "gc_pause", "heartbeat", "deletion_marker_check"}


def _subject_refs(d: Dict[str, str], st: Dict[str, str], *extra) -> set:
    vals = [st.get("key"), d.get("correlation_key"), d.get("matched_value"), d.get("subject_ref"), d.get("subject_reference"),
            d.get("lookup_key"), d.get("customer_email"), d.get("customer_reference"), d.get("owner_email"), d.get("email"), d.get("email_hash"), d.get("phone")]
    vals.extend(extra)
    refs = subject_values(*vals)
    # a lookup key that is itself a resource id is not a subject
    return {v for v in refs if not RESOURCE_ID.fullmatch(v)}


def _event_obs(ef: EvidenceFile, ts: str, ev: str, d: Dict[str, str], st: Dict[str, str], n: int) -> Optional[Observation]:
    rid = d.get("resource_id") or st.get("resource_id")
    src = f"log:{ev}"
    key = st.get("key") or d.get("lookup_key") or ""
    refs = _subject_refs(d, st)
    if ev == "profile_lookup_completed":
        if not rid:
            return None
        present = d.get("http_status") == "200" or (d.get("rows") not in (None, "", "0"))
        ids = ident_fields(d.get("email"), d.get("phone"), d.get("email_hash"), d.get("full_name"), d.get("customer_reference")) if present else {}
        if present and not ids and key and not RESOURCE_ID.fullmatch(key):
            ids = classify_subject(key)
        return Observation(ef.file_id, ts, rid, USER_PROFILE, present, system_id="account_service", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"http_status": d.get("http_status")}, source=src, sort_key=f"{n:06d}")
    if ev == "index_query_completed":
        if not rid:
            return None
        result = (d.get("result") or "").lower()
        present = result == "hit" or (d.get("total_hits") not in (None, "", "0") and result != "miss")
        ids = ident_fields(d.get("email"), d.get("phone"), d.get("email_hash"), d.get("display_name"), d.get("customer_reference")) if present else {}
        if present and not ids and key and not RESOURCE_ID.fullmatch(key):
            ids = classify_subject(key)
        return Observation(ef.file_id, ts, rid, SEARCH_INDEX_ENTRY, present, system_id="search_service", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), source=src, sort_key=f"{n:06d}")
    if ev in ("analytics_profile_read", "analytics_profile_read_completed"):
        if not rid:
            return None
        ids = ident_fields(d.get("email"), d.get("phone"), d.get("email_hash"), None, d.get("customer_reference"))
        di = d.get("direct_identifiers")
        if di not in (None, "", "0") and not ids and key and not RESOURCE_ID.fullmatch(key):
            ids = classify_subject(key)
        return Observation(ef.file_id, ts, rid, ANALYTICS_PROFILE, True, system_id="analytics_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"direct_identifiers": di, "audience_cohort": d.get("audience_cohort")}, source=src, sort_key=f"{n:06d}")
    if ev == "analytics_identity_scan":
        if not rid:
            return None
        ids = classify_subject(d.get("matched_value"))
        return Observation(ef.file_id, ts, rid, ANALYTICS_PROFILE, True, system_id="analytics_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"direct_identifiers": d.get("direct_identifiers")}, source=src, sort_key=f"{n:06d}")
    if ev == "contact_lookup_completed":
        if not rid:
            return None
        present = d.get("rows") not in (None, "", "0")
        ids = classify_subject(d.get("correlation_key")) if present else {}
        return Observation(ef.file_id, ts, rid, SECONDARY_IDENTIFIER, present, system_id="analytics_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"email_opt_in": d.get("email_opt_in")}, source=src, sort_key=f"{n:06d}")
    if ev == "message_pii_scan":
        if not rid:
            return None
        ids = classify_subject(d.get("matched_value"))
        return Observation(ef.file_id, ts, rid, SUPPORT_MESSAGE, True, system_id="support_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"state": "IDENTIFIABLE"}, source=src, sort_key=f"{n:06d}")
    if ev == "message_read":
        if not rid:
            return None
        text = d.get("message_text") or ""
        emails = EMAIL.findall(text)
        ids = ident_fields(emails[0] if emails else None, None, None, None, d.get("subject_ref"))
        redacted = text.strip().upper() == "[REDACTED]" or not emails
        return Observation(ef.file_id, ts, rid, SUPPORT_MESSAGE, True, system_id="support_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"state": "REDACTED" if redacted else "IDENTIFIABLE"}, source=src, sort_key=f"{n:06d}")
    if ev in ("support_ticket_read_completed",):
        if not rid:
            return None
        ids = ident_fields(d.get("requester_email"), d.get("requester_phone"), None, d.get("requester_name"), d.get("customer_reference"))
        return Observation(ef.file_id, ts, rid, SUPPORT_MESSAGE, True, system_id="support_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), source=src, sort_key=f"{n:06d}")
    if ev == "attachment_pii_scan":
        if not rid:
            return None
        ids = classify_subject(d.get("matched_value"))
        return Observation(ef.file_id, ts, rid, SUPPORT_ATTACHMENT, True, system_id="support_platform", identifiers=ids,
                           subject_refs=refs | subject_values(*ids.values()), flags={"file": d.get("file")}, source=src, sort_key=f"{n:06d}")
    if ev in ("audit_record_read_completed",):
        if not rid:
            return None
        present = d.get("rows") not in (None, "", "0")
        subj = d.get("subject_reference") or d.get("correlation_key") or key
        ids = classify_subject(subj) if present and subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, SECURITY_AUDIT_LOG, present, system_id="security_service", identifiers=ids,
                           subject_refs=refs, flags={"retention_until": d.get("retention_until"), "event": d.get("event_code")}, source=src, sort_key=f"{n:06d}")
    if ev in ("connector_config_loaded", "connector_heartbeat", "connector_config_read_completed"):
        if not rid:
            return None
        enabled = to_bool(d.get("enabled"))
        subj = d.get("customer_email") or d.get("customer_reference") or d.get("correlation_key") or key
        ids = classify_subject(subj) if subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, DATA_FLOW, True, system_id="integration_service", identifiers=ids, subject_refs=refs,
                           flags={"enabled": enabled, "last_run": d.get("last_run"), "last_delivery_status": d.get("last_delivery_status"), "connector_name": d.get("connector_name")},
                           source=src, sort_key=f"{n:06d}")
    if ev == "restore_candidate_evaluated":
        if not rid:
            return None
        eligible = to_bool(d.get("eligible"))
        block = to_bool(d.get("deletion_block"))
        allowed = bool(eligible) and not bool(block)
        subj = d.get("correlation_key") or key
        ids = classify_subject(subj) if subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, BACKUP_ARCHIVE, True, system_id="backup_service", identifiers=ids, subject_refs=refs,
                           flags={"restore_allowed": allowed, "eligible": eligible, "deletion_block": block}, source=src, sort_key=f"{n:06d}")
    if ev == "restore_authorization_completed":
        if not rid:
            return None
        decision = (d.get("decision") or "").lower()
        allowed = decision in ("allow", "allowed", "approve", "approved", "grant", "granted")
        subj = d.get("correlation_key") or key
        ids = classify_subject(subj) if subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, BACKUP_ARCHIVE, True, system_id="backup_service", identifiers=ids, subject_refs=refs,
                           flags={"restore_allowed": allowed, "decision": decision, "policy": d.get("policy")}, source=src, sort_key=f"{n:06d}")
    if ev == "restore_policy_read_completed":
        if not rid:
            return None
        allowed = to_bool(d.get("restore_allowed"))
        present = to_bool(d.get("archive_present"))
        subj = d.get("customer_email") or d.get("customer_reference") or key
        ids = classify_subject(subj) if subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, BACKUP_ARCHIVE, True if present is None else present, system_id="backup_service", identifiers=ids, subject_refs=refs,
                           flags={"restore_allowed": allowed, "archive_key": d.get("archive_key")}, source=src, sort_key=f"{n:06d}")
    if ev in ("export_download_check_completed", "export_metadata_read_completed"):
        if not rid:
            return None
        avail = to_bool(d.get("available"))
        subj = d.get("owner_email") or d.get("correlation_key") or key
        ids = classify_subject(subj) if subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, USER_EXPORT, True if avail is None else avail, system_id="export_service", identifiers=ids, subject_refs=refs,
                           flags={"file": d.get("file") or d.get("object_key")}, source=src, sort_key=f"{n:06d}")
    if ev == "contact_query_completed":
        if not rid:
            return None
        present = d.get("rows") not in (None, "", "0")
        subj = d.get("lookup_key") or key
        ids = classify_subject(subj) if present and subj else {}
        return Observation(ef.file_id, ts, rid, SECONDARY_IDENTIFIER, present, system_id="support_platform", identifiers=ids,
                           subject_refs=refs | subject_values(subj), flags={"source": d.get("source")}, source=src, sort_key=f"{n:06d}")
    if ev in ("retained_order_loaded", "legal_hold_loaded", "retention_record_loaded"):
        if not rid:
            return None
        from audit.observations import LEGAL_HOLD_RECORD
        subj = d.get("subject_reference") or d.get("correlation_key") or key
        ids = classify_subject(subj) if subj and not RESOURCE_ID.fullmatch(subj) else {}
        return Observation(ef.file_id, ts, rid, LEGAL_HOLD_RECORD, True, system_id="legal_service", identifiers=ids, subject_refs=refs,
                           flags={"retained_until": d.get("retained_until") or d.get("retention_until"), "order_reference": d.get("order_reference")}, source=src, sort_key=f"{n:06d}")
    if ev == "deletion_marker_check":
        return None  # qualifies the contact_query row; marker_found=false means the copy is live
    return None
