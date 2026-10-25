"""JSON evidence: service resource exports, snapshot bundles, manifest-style
record lists, analytics reports, export job lists."""
from __future__ import annotations

from typing import Any, Dict

from audit.data import EvidenceFile
from audit.observations import (ANALYTICS_PROFILE, BACKUP_ARCHIVE, DATA_FLOW, ParseResult,
                                SEARCH_INDEX_ENTRY, SECURITY_AUDIT_LOG, SUPPORT_MESSAGE, USER_EXPORT,
                                USER_PROFILE, Observation, ScopeObservation)

from .common import ident_fields, subject_values, to_bool
from .records import classify_subject, collection_record, lookup_record


def parse(ef: EvidenceFile, data: Dict[str, Any]) -> ParseResult:
    if "service_exports" in data:
        return parse_service_export(ef, data)
    if "snapshots" in data:
        return parse_bundle(ef, data)
    if "records" in data and isinstance(data["records"], list):
        recs = data["records"]
        if recs and "record_type" in recs[0] and "result" in recs[0]:
            return parse_manifest(ef, data)
        coll = data.get("collection") or ""
        return parse_collection(ef, data, coll)
    res = ParseResult()
    res.notes.append("unrecognised json layout")
    return res


def parse_service_export(ef: EvidenceFile, data: Dict[str, Any]) -> ParseResult:
    res = ParseResult()
    cap = data.get("exported_at") or ef.captured_at
    se = data["service_exports"]
    for item in se.get("account_service", {}).get("profile_lookups", []):
        prof = item.get("profile")
        present = item.get("status_code") == 200 or (prof is not None)
        ids = ident_fields(*(prof.get(k) for k in ("email", "phone", "email_hash", "full_name", "customer_reference"))) if prof else {}
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], USER_PROFILE, bool(present),
                                            system_id="account_service", identifiers=ids, subject_refs=subject_values(*ids.values()),
                                            flags={"http_status": item.get("status_code")}, source="export:profile_lookups"))
    for item in se.get("analytics_platform", {}).get("profiles", []):
        ids = ident_fields(item.get("email"), item.get("phone"), item.get("email_hash"), None, item.get("customer_reference"))
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], ANALYTICS_PROFILE, True,
                                            system_id="analytics_platform", identifiers=ids, subject_refs=subject_values(*ids.values()),
                                            source="export:analytics_profiles"))
    for item in se.get("backup_service", {}).get("restore_entries", []):
        subj = item.get("customer_email") or item.get("customer_reference")
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], BACKUP_ARCHIVE, True,
                                            system_id="backup_service", identifiers=classify_subject(subj),
                                            subject_refs=subject_values(item.get("customer_email"), item.get("customer_reference")),
                                            flags={"restore_allowed": to_bool(item.get("restore_allowed"))}, source="export:restore_entries"))
    for item in se.get("export_service", {}).get("exports", []):
        ids = ident_fields(item.get("owner_email"), item.get("owner_phone"), None, None, item.get("customer_reference"))
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], USER_EXPORT, True,
                                            system_id="export_service", identifiers=ids, subject_refs=subject_values(*ids.values()),
                                            source="export:exports"))
    for item in se.get("integration_service", {}).get("connectors", []):
        subj = item.get("customer_email") or item.get("customer_reference")
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], DATA_FLOW, True,
                                            system_id="integration_service", identifiers=classify_subject(subj),
                                            subject_refs=subject_values(item.get("customer_email"), item.get("customer_reference")),
                                            flags={"enabled": to_bool(item.get("enabled")), "last_delivery_status": item.get("last_delivery_status")},
                                            source="export:connectors"))
    for item in se.get("search_service", {}).get("index_queries", []):
        hits = item.get("hits") or {}
        docs = hits.get("documents") or []
        total = hits.get("total", len(docs))
        q = item.get("query")
        if docs:
            for doc in docs:
                ids = ident_fields(doc.get("email"), doc.get("phone"), doc.get("email_hash"), doc.get("display_name"), doc.get("customer_reference"))
                res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, doc.get("resource_id") or item["resource_id"], SEARCH_INDEX_ENTRY, True,
                                                    system_id="search_service", identifiers=ids, subject_refs=subject_values(q, *ids.values()),
                                                    flags={"query": q}, source="export:index_queries"))
        else:
            res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], SEARCH_INDEX_ENTRY, total > 0,
                                                system_id="search_service", subject_refs=subject_values(q), flags={"query": q}, source="export:index_queries"))
    for item in se.get("security_service", {}).get("audit_records", []):
        subj = item.get("subject_reference")
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], SECURITY_AUDIT_LOG, True,
                                            system_id="security_service", identifiers=classify_subject(subj), subject_refs=subject_values(subj),
                                            flags={"retention_until": item.get("retention_until"), "event": item.get("event_code")}, source="export:audit_records"))
    for item in se.get("support_platform", {}).get("tickets", []):
        ids = ident_fields(item.get("requester_email"), item.get("requester_phone"), None, item.get("requester_name"), item.get("customer_reference"))
        res.observations.append(Observation(ef.file_id, item.get("observed_at") or cap, item["resource_id"], SUPPORT_MESSAGE, True,
                                            system_id="support_platform", identifiers=ids, subject_refs=subject_values(*ids.values()),
                                            flags={"excerpt": item.get("message_excerpt")}, source="export:tickets"))
    return res


def parse_bundle(ef: EvidenceFile, data: Dict[str, Any]) -> ParseResult:
    res = ParseResult()
    for snap in data.get("snapshots", []):
        cap = snap.get("captured_at") or data.get("exported_at") or ef.captured_at
        system = snap.get("data_store", "")
        coll = snap.get("collection", "")
        for rec in snap.get("records", []):
            obs = collection_record(ef, cap, system, coll, rec, source=f"bundle:{system}/{coll}")
            if obs:
                res.observations.append(obs)
            else:
                res.notes.append(f"unmapped bundle collection {system}/{coll}")
    return res


def parse_manifest(ef: EvidenceFile, data: Dict[str, Any]) -> ParseResult:
    res = ParseResult()
    cap = data.get("exported_at") or data.get("captured_at") or ef.captured_at
    for rec in data["records"]:
        res.observations.append(lookup_record(ef, rec.get("recorded_at") or cap, rec.get("system", ""), rec["resource_id"], rec.get("record_type", ""),
                                              rec.get("result", ""), rec.get("subject_reference") or rec.get("lookup_value") or "",
                                              source=f"manifest:{rec.get('record_type')}:{rec.get('result')}"))
    return res


def parse_collection(ef: EvidenceFile, data: Dict[str, Any], coll: str) -> ParseResult:
    res = ParseResult()
    cap = data.get("captured_at") or data.get("exported_at") or ef.captured_at
    system = data.get("source") or ""
    if not coll:
        recs = data["records"]
        if recs and "download_available" in recs[0]:
            coll = "customer_export_jobs"
        elif recs and "reporting_window" in recs[0]:
            coll = "customer_analytics_reports"
    for rec in data["records"]:
        obs = collection_record(ef, cap, system, coll, rec, source=f"json:{coll}")
        if obs:
            res.observations.append(obs)
    return res
