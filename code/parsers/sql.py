"""SQLite-compatible SQL snapshot exports (inventory, replicas, failover, search primary).

The dump is executed in an in-memory sqlite3 database; tables are then mapped
by name.  Tables with a `record_json` column (primary/replica captures) are
expanded with the collection normaliser so replication drift is visible as
several observations of the same resource (any copy present => present).
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List

from audit.data import EvidenceFile
from audit.observations import (SEARCH_INDEX_ENTRY, USER_PROFILE, Observation, ParseResult,
                                ScopeObservation)

from .common import ident_fields, subject_values
from .records import collection_record

INVENTORY_TABLES = {
    "customer_profiles": "customer_profiles",
    "customer_search_documents": "customer_search_documents",
    "customer_analytics_profiles": "customer_analytics_profiles",
    "support_records": "support_records",
    "customer_exports": "customer_exports",
    "connector_subscriptions": "connector_subscriptions",
    "backup_restore_entries": "backup_restore_entries",
    "security_audit_records": "security_audit_records",
}
TABLE_SYSTEM = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "support_records": "support_platform",
    "customer_exports": "export_service", "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
}


def _rows(con, table: str) -> List[dict]:
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    return [dict(zip(cols, row)) for row in con.execute(f'SELECT * FROM "{table}"')]


def parse(ef: EvidenceFile, text: str) -> ParseResult:
    res = ParseResult()
    captured = ef.captured_at
    m = re.search(r"Captured at:\s*(\S+)", text)
    if m:
        captured = m.group(1)
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
    except sqlite3.Error as exc:  # fall back: nothing parsed, report
        res.notes.append(f"sqlite load failed: {exc}")
        return res
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    scope_times: Dict[str, str] = {}
    if "snapshot_scope" in tables:
        for r in _rows(con, "snapshot_scope"):
            scope_times[r["origin_system"]] = r["captured_at"]
    seen_by_system: Dict[str, ScopeObservation] = {}

    def scope_for(system: str) -> ScopeObservation:
        if system not in seen_by_system:
            seen_by_system[system] = ScopeObservation(ef.file_id, scope_times.get(system, captured), system, source="sql")
        return seen_by_system[system]

    for t in tables:
        if t == "snapshot_scope":
            continue
        rows = _rows(con, t)
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        if "record_json" in cols:
            for r in rows:
                try:
                    rec = json.loads(r["record_json"])
                except json.JSONDecodeError:
                    res.notes.append(f"bad record_json in {t}")
                    continue
                rec.setdefault("resource_id", r.get("resource_id"))
                obs = collection_record(ef, r.get("updated_at") or captured, r.get("origin_system", ""), r.get("collection_name", ""), rec, source=f"sql:{t}/{r.get('collection_name')}")
                if obs:
                    obs.captured_at = captured  # table copies are as-of the capture, updated_at is the row's own time
                    obs.flags["updated_at"] = r.get("updated_at")
                    obs.flags["replica_table"] = t
                    res.observations.append(obs)
                    sc = scope_for(obs.system_id)
                    sc.identifiers_seen |= obs.subject_refs
                    sc.resource_ids_seen.add(obs.resource_id)
            continue
        if t in INVENTORY_TABLES:
            system = TABLE_SYSTEM[t]
            sc = scope_for(system)
            for r in rows:
                obs = collection_record(ef, scope_times.get(system, captured), system, t, r, source=f"sql:{t}")
                if obs:
                    res.observations.append(obs)
                    sc.identifiers_seen |= obs.subject_refs
                    sc.resource_ids_seen.add(obs.resource_id)
            continue
        if t.startswith("customer_profiles_"):  # accounts failover / primary
            sc = scope_for("account_service")
            for r in rows:
                ids = ident_fields(r.get("email"), r.get("phone"), r.get("email_hash"), r.get("full_name"), r.get("user_id") or r.get("customer_reference"))
                obs = Observation(ef.file_id, captured, r["resource_id"], USER_PROFILE, True, system_id="account_service", identifiers=ids,
                                  subject_refs=subject_values(*ids.values()), flags={"account_status": r.get("account_status"), "replica_table": t, "updated_at": r.get("updated_at")},
                                  source=f"sql:{t}")
                res.observations.append(obs)
                sc.identifiers_seen |= obs.subject_refs
                sc.resource_ids_seen.add(obs.resource_id)
            continue
        if t.startswith("search_documents"):
            sc = scope_for("search_service")
            for r in rows:
                ids = ident_fields(r.get("email"), r.get("phone"), r.get("email_hash"), r.get("display_name"), r.get("customer_reference"))
                obs = Observation(ef.file_id, captured, r["resource_id"], SEARCH_INDEX_ENTRY, True, system_id="search_service", identifiers=ids,
                                  subject_refs=subject_values(*ids.values()), flags={"linked_account": r.get("source_resource_id"), "replica_table": t, "indexed_at": r.get("indexed_at")},
                                  source=f"sql:{t}")
                res.observations.append(obs)
                sc.identifiers_seen |= obs.subject_refs
                sc.resource_ids_seen.add(obs.resource_id)
            continue
        res.notes.append(f"unmapped sql table {t}")
    # every system named in snapshot_scope is a complete enumeration, even when empty
    for system in scope_times:
        scope_for(system)
    res.scopes.extend(seen_by_system.values())
    con.close()
    return res
