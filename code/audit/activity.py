"""Workflow reconstruction from system_activity.csv.

The activity stream is a global, noisy log.  Rows from the deletion worker
describe what the deletion workflow tried to do (and claims about the result).
They never name the subject, so a case is linked to its workflow rows through
resource ids that evidence ties to the subject, plus an attribution step for
the account record (see attribution.py).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from .data import Activity

WORKER = "deletion_worker"
ACTION_EVENTS = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
                 "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS", "RETAIN_RECORD"}


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


class ActivityIndex:
    def __init__(self, activities: List[Activity]):
        self.rows = activities
        self.by_resource: Dict[str, List[Activity]] = defaultdict(list)
        self.worker_rows: List[Activity] = []
        self.account_deletes: List[Activity] = []
        self.link_rows: List[Activity] = []
        self.classify_rows: List[Activity] = []
        for a in activities:
            self.by_resource[a.resource_id].append(a)
            if a.source_system == WORKER and a.event_type in ACTION_EVENTS:
                self.worker_rows.append(a)
                if a.event_type == "DELETE_RECORD" and a.target_system == "account_service":
                    self.account_deletes.append(a)
            if a.event_type == "LINK_RESOURCE_IDENTITY":
                self.link_rows.append(a)
            if a.event_type == "CLASSIFY_REQUEST":
                self.classify_rows.append(a)
        self.worker_rows.sort(key=lambda a: a.event_time)
        self.account_deletes.sort(key=lambda a: a.event_time)

    def worker_actions(self, resource_id: str) -> List[Activity]:
        return [a for a in self.by_resource.get(resource_id, []) if a.source_system == WORKER]

    def last_worker_action(self, resource_id: str, before: Optional[str] = None) -> Optional[Activity]:
        rows = [a for a in self.worker_actions(resource_id) if (before is None or a.event_time <= before)]
        return rows[-1] if rows else None

    def recreation_events(self, resource_id: str, after: str, before: str) -> List[Activity]:
        """Sync/restore events that may have recreated a resource after the worker acted."""
        out = []
        for a in self.by_resource.get(resource_id, []):
            if a.event_type in ("SYNC_RECORD", "RESTORE_BACKUP", "INDEX_RECORD", "REFRESH_CACHE") and after <= a.event_time <= before:
                out.append(a)
        return out

    def links_for(self, keys: Set[str]) -> List[Activity]:
        """LINK_RESOURCE_IDENTITY rows whose details mention one of the subject keys."""
        out = []
        for a in self.link_rows:
            low = a.details.lower()
            if any(k and k in low for k in keys):
                out.append(a)
        return out

    def account_delete_candidates(self, reported_at: str, before_hours: float = 4.0, after_minutes: float = 20.0) -> List[Activity]:
        rep = parse_ts(reported_at)
        lo, hi = rep - timedelta(hours=before_hours), rep + timedelta(minutes=after_minutes)
        return [a for a in self.account_deletes if lo <= parse_ts(a.event_time) <= hi]
