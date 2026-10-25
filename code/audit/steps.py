"""Build and order the steps_json array from policy decisions."""
from __future__ import annotations

import json
from typing import Dict, List

from .policy import Decision

PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
OP_ORDER = {
    "REPORT_REMAINING_RECORD": 0, "REPORT_EXPOSED_PII": 1, "REPORT_ACTIVE_DATA_FLOW": 2, "REPORT_RESTORE_RISK": 3,
    "DISABLE_DATA_FLOW": 0, "BLOCK_RESTORE": 1, "DELETE_RECORD": 2, "REDACT_RECORD": 3, "ANONYMIZE_RECORD": 4,
    "VERIFY_RECORD_ABSENT": 0, "VERIFY_RECORD_PRESENT": 1, "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3, "VERIFY_DATA_FLOW_DISABLED": 4, "VERIFY_RESTORE_BLOCKED": 5,
}


def _step(phase: str, system_id: str, resource_id: str, op: str, state: str, evidence_id: str) -> dict:
    return {"phase": phase, "system_id": system_id, "resource_id": resource_id, "operation": op,
            "state": state, "evidence_id": evidence_id}


def sort_steps(steps: List[dict]) -> List[dict]:
    steps = sorted(steps, key=lambda s: (PHASE_ORDER[s["phase"]], OP_ORDER[s["operation"]], s["system_id"], s["resource_id"]))
    out = []
    for i, s in enumerate(steps, 1):
        out.append({"step_no": i, **s})
    return out


def build_steps(decisions: List[Decision]) -> (str, List[dict]):
    """Return (deletion_status, ordered steps) for an authorised deletion request."""
    violations = [d for d in decisions if not d.compliant and d.finding and d.observed]
    status = "INCOMPLETE" if violations else "COMPLETE"
    steps: List[dict] = []
    if status == "COMPLETE":
        for d in decisions:
            op, state = d.verification
            steps.append(_step("VERIFICATION", d.system_id, d.resource_id, op, state, d.evidence_id))
    else:
        for d in violations:
            op, state = d.finding
            steps.append(_step("FINDING", d.system_id, d.resource_id, op, state, d.evidence_id))
            op, state = d.remediation
            steps.append(_step("REMEDIATION", d.system_id, d.resource_id, op, state, ""))
        for d in decisions:
            op, state = d.verification
            steps.append(_step("VERIFICATION", d.system_id, d.resource_id, op, state, ""))
    return status, sort_steps(steps)


def steps_to_json(steps: List[dict]) -> str:
    return json.dumps(steps, separators=(",", ":"), ensure_ascii=False)


def dedupe_decisions(decisions: List[Decision]) -> List[Decision]:
    seen: Dict[str, Decision] = {}
    for d in decisions:
        key = d.resource_id
        if key in seen:
            # keep the non-compliant / observed one
            cur = seen[key]
            if (not cur.observed and d.observed) or (cur.compliant and not d.compliant):
                seen[key] = d
        else:
            seen[key] = d
    return list(seen.values())
