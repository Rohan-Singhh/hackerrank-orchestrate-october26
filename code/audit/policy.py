"""Apply deletion_rules.csv to an observed resource state.

The policy maps (category, observed state) to the required treatment and to
the finding / remediation / verification operations of the output grammar.
The table below is the whole policy; everything else in the engine is
plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .observations import (ACCOUNT_TOMBSTONE, ANALYTICS_PROFILE, BACKUP_ARCHIVE, CACHE_ENTRY,
                           DATA_FLOW, EMAIL_DELIVERY_LOG, LEGAL_HOLD_RECORD, MARKETING_PROFILE,
                           PRODUCT_TELEMETRY, SEARCH_INDEX_ENTRY, SECONDARY_IDENTIFIER,
                           SECURITY_AUDIT_LOG, SUPPORT_ATTACHMENT, SUPPORT_MESSAGE, USER_EXPORT,
                           USER_PROFILE, Observation)

DELETE, REDACT, ANONYMIZE, RETAIN, DISABLE, BLOCK = "DELETE", "REDACT", "ANONYMIZE", "RETAIN", "DISABLE", "BLOCK"

# category -> required action when the record is present / attributable
REQUIRED_ACTION: Dict[str, str] = {
    USER_PROFILE: DELETE,
    SECONDARY_IDENTIFIER: DELETE,
    SEARCH_INDEX_ENTRY: DELETE,
    CACHE_ENTRY: DELETE,
    SUPPORT_MESSAGE: REDACT,
    SUPPORT_ATTACHMENT: DELETE,
    ANALYTICS_PROFILE: ANONYMIZE,
    PRODUCT_TELEMETRY: ANONYMIZE,
    USER_EXPORT: DELETE,
    MARKETING_PROFILE: DELETE,
    EMAIL_DELIVERY_LOG: REDACT,
    SECURITY_AUDIT_LOG: RETAIN,
    LEGAL_HOLD_RECORD: RETAIN,
    BACKUP_ARCHIVE: BLOCK,
    ACCOUNT_TOMBSTONE: REDACT,
    DATA_FLOW: DISABLE,
}

# action -> (finding op/state, remediation op/state, verification op/state)
ACTION_OPS = {
    DELETE: (("REPORT_REMAINING_RECORD", "PRESENT"), ("DELETE_RECORD", "ABSENT"), ("VERIFY_RECORD_ABSENT", "ABSENT")),
    REDACT: (("REPORT_EXPOSED_PII", "PRESENT"), ("REDACT_RECORD", "REDACTED"), ("VERIFY_RECORD_REDACTED", "REDACTED")),
    ANONYMIZE: (("REPORT_EXPOSED_PII", "PRESENT"), ("ANONYMIZE_RECORD", "ANONYMIZED"), ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED")),
    DISABLE: (("REPORT_ACTIVE_DATA_FLOW", "ACTIVE"), ("DISABLE_DATA_FLOW", "DISABLED"), ("VERIFY_DATA_FLOW_DISABLED", "DISABLED")),
    BLOCK: (("REPORT_RESTORE_RISK", "ACTIVE"), ("BLOCK_RESTORE", "BLOCKED"), ("VERIFY_RESTORE_BLOCKED", "BLOCKED")),
    RETAIN: (None, None, ("VERIFY_RECORD_PRESENT", "PRESENT")),
}


@dataclass
class Decision:
    resource_id: str
    system_id: str
    category: str
    action: str
    compliant: bool  # True: already in the required state
    evidence_id: str  # file that establishes the current state ("" if from activity only)
    reason: str = ""
    observed: bool = True  # False when the state comes from the activity stream only
    retained_valid: bool = True  # for RETAIN: whether the record must be kept (within retention)

    @property
    def finding(self):
        return ACTION_OPS[self.action][0]

    @property
    def remediation(self):
        return ACTION_OPS[self.action][1]

    @property
    def verification(self):
        return ACTION_OPS[self.action][2]


def _truthy(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "enabled", "active", "allowed", "y"):
        return True
    if s in ("0", "false", "no", "disabled", "blocked", "stopped", "inactive", "n"):
        return False
    return None


def decide(obs: Observation, audit_at: str) -> Decision:
    """Turn the latest observation of a resource into a policy decision."""
    cat = obs.category
    action = REQUIRED_ACTION[cat]
    f = obs.flags
    ev = obs.file_id
    if action == DELETE:
        present = obs.present if obs.present is not None else True
        return Decision(obs.resource_id, obs.system_id, cat, action, compliant=not present, evidence_id=ev,
                        reason=f"present={present} via {obs.source}")
    if action == REDACT:
        if obs.present is False:
            # record gone entirely: it cannot expose anything; verification still REDACTED? no - absent.
            return Decision(obs.resource_id, obs.system_id, cat, DELETE, compliant=True, evidence_id=ev,
                            reason=f"absent via {obs.source}")
        exposed = obs.direct_pii
        state_flag = f.get("state")
        if state_flag:
            exposed = str(state_flag).upper() in ("IDENTIFIABLE", "FOUND", "PRESENT", "EXPOSED")
        return Decision(obs.resource_id, obs.system_id, cat, action, compliant=not exposed, evidence_id=ev,
                        reason=f"identifiers={sorted(obs.identifiers)} via {obs.source}")
    if action == ANONYMIZE:
        if obs.present is False:
            return Decision(obs.resource_id, obs.system_id, cat, DELETE, compliant=True, evidence_id=ev,
                            reason=f"absent via {obs.source}")
        attributable = obs.identifiable
        state_flag = f.get("state")
        if state_flag:
            attributable = str(state_flag).upper() in ("IDENTIFIABLE", "FOUND", "PRESENT", "ATTRIBUTABLE")
        return Decision(obs.resource_id, obs.system_id, cat, action, compliant=not attributable, evidence_id=ev,
                        reason=f"identifiers={sorted(obs.identifiers)} via {obs.source}")
    if action == DISABLE:
        enabled = _truthy(f.get("enabled"))
        if enabled is None:
            enabled = _truthy(f.get("state"))
        if enabled is None:
            enabled = True if obs.present is not False else False
        return Decision(obs.resource_id, obs.system_id, cat, action, compliant=not enabled, evidence_id=ev,
                        reason=f"enabled={enabled} via {obs.source}")
    if action == BLOCK:
        allowed = _truthy(f.get("restore_allowed"))
        if allowed is None and f.get("state") is not None:
            allowed = str(f["state"]).upper() in ("ACTIVE", "ALLOWED", "ELIGIBLE", "RESTORABLE")
        if allowed is None:
            allowed = True if obs.present is not False else False
        return Decision(obs.resource_id, obs.system_id, cat, action, compliant=not allowed, evidence_id=ev,
                        reason=f"restore_allowed={allowed} via {obs.source}")
    if action == RETAIN:
        until = f.get("retention_until") or f.get("retained_until")
        valid = True
        if until and audit_at and str(until) < audit_at:
            valid = False
        present = obs.present if obs.present is not None else True
        return Decision(obs.resource_id, obs.system_id, cat, action, compliant=present, evidence_id=ev,
                        reason=f"present={present} until={until} via {obs.source}", retained_valid=valid)
    raise ValueError(f"no policy for category {cat}")


def load_rules(rows: List[dict]) -> Dict[str, dict]:
    """Keep the CSV rules at hand (sanity-checked against REQUIRED_ACTION)."""
    out = {}
    for r in rows:
        out[r["data_category"]] = r
        exp = REQUIRED_ACTION.get(r["data_category"])
        if exp and exp != r["required_action"] and not (exp == BLOCK and r["required_action"] == RETAIN):
            raise ValueError(f"policy table disagrees with deletion_rules.csv for {r['data_category']}: {exp} vs {r['required_action']}")
    return out
