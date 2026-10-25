"""audit_case: one request in, (status, steps, trace) out.

Pipeline
  intent gate -> identity set -> evidence selection (captured_at <= audit_at)
  -> subject-linked observations -> workflow resources from the activity stream
  -> account attribution -> latest observation per resource -> policy -> steps
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .activity import ActivityIndex
from .data import Dataset, EvidenceFile, Request
from .identity import IdentityResolver, IdentitySet
from .observations import (ACCOUNT_TOMBSTONE, ANALYTICS_PROFILE, SECURITY_AUDIT_LOG, USER_PROFILE,
                           Observation, ParseResult, ScopeObservation, default_category, system_for_resource)
from .policy import DELETE, Decision, decide
from .steps import build_steps, dedupe_decisions

log = logging.getLogger("audit")


@dataclass
class CaseTrace:
    case_id: str
    user_id: str = ""
    intent: str = ""
    intent_source: str = ""
    identifiers: Dict[str, List[str]] = field(default_factory=dict)
    files_in_scope: List[str] = field(default_factory=list)
    linked_observations: List[dict] = field(default_factory=list)
    account_source: str = ""
    account_candidates: List[str] = field(default_factory=list)
    workflow_rows: List[dict] = field(default_factory=list)
    decisions: List[dict] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    status: str = ""
    n_steps: int = 0


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: List[dict]
    trace: CaseTrace


class EvidenceStore:
    """Parsed observations for every text evidence file plus media observations."""

    def __init__(self, files: List[EvidenceFile], parsed: Dict[str, ParseResult]):
        self.files = {f.file_id: f for f in files}
        self.parsed = parsed
        self.obs_by_resource: Dict[str, List[Observation]] = defaultdict(list)
        for pr in parsed.values():
            for o in pr.observations:
                self.obs_by_resource[o.resource_id].append(o)

    def observations(self) -> List[Observation]:
        return [o for pr in self.parsed.values() for o in pr.observations]

    def scopes(self) -> List[ScopeObservation]:
        return [s for pr in self.parsed.values() for s in pr.scopes]


class Engine:
    def __init__(self, ds: Dataset, store: EvidenceStore, resolver: IdentityResolver, activity: ActivityIndex,
                 intent_fn, account_map: Dict[str, str], account_candidates: Dict[str, List[str]],
                 extra_resources: Optional[Dict[str, List[str]]] = None):
        self.ds = ds
        self.store = store
        self.resolver = resolver
        self.activity = activity
        self.intent_fn = intent_fn
        self.account_map = account_map  # case_id -> account id (from evidence or attribution)
        self.account_candidates = account_candidates
        self.extra_resources = extra_resources or {}
        self.extra_resources = extra_resources or {}
        self._all_obs = store.observations()
        self._scopes = store.scopes()
        # subject index: normalised ref -> observations (emails/hashes/user ids lower-cased, phones digits-only)
        from .identity import norm_phone
        self._by_ref: Dict[str, List[Observation]] = defaultdict(list)
        self._by_name: Dict[str, List[Observation]] = defaultdict(list)
        for o in self._all_obs:
            for ref in o.subject_refs:
                self._by_ref[ref].append(o)
                digits = norm_phone(ref)
                if len(digits) >= 7 and digits != ref:
                    self._by_ref[digits].append(o)
            if not o.subject_refs and o.identifiers.get("name"):
                from .identity import norm_name
                self._by_name[norm_name(o.identifiers["name"])].append(o)

    # ---- subject linking -------------------------------------------------
    def linked_observations(self, ids: IdentitySet, audit_at: str, since: str = "") -> List[Observation]:
        """Observations naming the subject, captured in (since, audit_at].

        `since` is the workflow's reported_at: files captured before the workflow ran
        describe the pre-deletion world and would leak stale resources (sample case_17).
        """
        cands: List[Observation] = []
        seen = set()
        for key in ids.all_keys() | ids.names:
            for o in self._by_ref.get(key, []) + self._by_name.get(key, []):
                if id(o) in seen:
                    continue
                seen.add(id(o))
                cands.append(o)
        out = []
        for o in cands:
            fcap = self.store.files[o.file_id].captured_at
            if o.captured_at > audit_at or fcap > audit_at:
                continue
            if since and fcap < since:
                continue
            if (o.subject_refs and ids.matches(*o.subject_refs)) or (not o.subject_refs and ids.matches_name(o.identifiers.get("name"))):
                out.append(o)
        return out

    def observations_for(self, resource_id: str, audit_at: str, since: str = "") -> List[Observation]:
        return [o for o in self.store.obs_by_resource.get(resource_id, [])
                if o.captured_at <= audit_at and self.store.files[o.file_id].captured_at <= audit_at
                and (not since or self.store.files[o.file_id].captured_at >= since)]

    # ---- main entry ------------------------------------------------------
    def audit_case(self, req: Request) -> CaseResult:
        tr = CaseTrace(case_id=req.case_id)
        intent, src = self.intent_fn(req.request_text)
        tr.intent, tr.intent_source = intent, src
        if intent != "DELETION":
            tr.status = "NOT_APPLICABLE"
            return CaseResult(req.case_id, "NOT_APPLICABLE", [], tr)

        ids = self.resolver.resolve(req.email)
        if ids is None:
            tr.flags.append("unknown_subject")
            tr.status = "COMPLETE"
            return CaseResult(req.case_id, "COMPLETE", [], tr)
        tr.user_id = ids.user_id
        tr.identifiers = {"emails": sorted(ids.emails), "phones": sorted(ids.phones), "hashes": [h[:12] + "..." for h in sorted(ids.hashes)]}

        linked = self.linked_observations(ids, req.audit_at, since=req.reported_at)
        if not linked:
            linked = self.linked_observations(ids, req.audit_at, since=req.requested_at)
            if linked:
                tr.flags.append("evidence_before_reported_at")
        tr.files_in_scope = sorted({o.file_id for o in linked})
        resources: Dict[str, str] = {}  # resource_id -> how it was linked
        for o in linked:
            resources.setdefault(o.resource_id, "evidence")
            la = o.flags.get("linked_account")
            if la:
                resources.setdefault(la, "linked_account")
        # identity-less resources attributed by batch rank (anonymised analytics etc.)
        for rid in self.extra_resources.get(req.case_id, []):
            if self.observations_for(rid, req.audit_at, since=req.reported_at):
                resources.setdefault(rid, "batch_rank")

        # media-only cases without a vision model: data-catalog links name the resource; the
        # workflow result gives the state; the screenshot/PDF captured for the case is cited
        import os as _os
        if _os.environ.get("AUDIT_DEBUG"):
            print("DEBUG resources before fallback:", resources, "extra:", self.extra_resources.get(req.case_id))
        if not resources:
            for rid, how, ev_file in self._catalog_fallback(ids, req):
                resources.setdefault(rid, how)
                tr.flags.append(f"catalog_fallback:{rid}:{ev_file or 'no-media'}")

        # account: evidence first, then attribution
        account = next((r for r, how in resources.items() if r.startswith("account_")), None)
        if account:
            tr.account_source = resources[account]
        else:
            account = self.account_map.get(req.case_id)
            if account:
                resources[account] = "attribution"
                tr.account_source = "attribution"
                tr.account_candidates = self.account_candidates.get(req.case_id, [])
                if len(tr.account_candidates) > 1:
                    tr.flags.append("ambiguous_account")
            else:
                tr.flags.append("no_account")

        # latest observation per resource -> decision
        decisions: List[Decision] = []
        primary_file = self._primary_file(linked, req.audit_at)
        for rid, how in sorted(resources.items()):
            obs = self.observations_for(rid, req.audit_at, since=req.reported_at) or self.observations_for(rid, req.audit_at, since=req.requested_at)
            if how == "evidence":
                obs = [o for o in obs if o in linked] or obs
            d = self._decide_resource(rid, obs, req, ids, primary_file)
            if d is None:
                continue
            tr.decisions.append({"resource": rid, "link": how, "category": d.category, "action": d.action, "compliant": d.compliant,
                                 "observed": d.observed, "evidence": d.evidence_id, "reason": d.reason})
            decisions.append(d)
            for a in self.activity.worker_actions(rid):
                tr.workflow_rows.append({"activity": a.activity_id, "time": a.event_time, "event": a.event_type, "resource": rid, "result": a.reported_result})

        decisions = dedupe_decisions(decisions)
        decisions = [d for d in decisions if not (d.action == "RETAIN" and not d.retained_valid)]
        status, steps = build_steps(decisions)
        if not decisions:
            tr.flags.append("no_resources")
        tr.status, tr.n_steps = status, len(steps)
        tr.linked_observations = [{"file": o.file_id, "resource": o.resource_id, "category": o.category, "present": o.present,
                                   "ids": sorted(o.identifiers), "source": o.source} for o in linked]
        return CaseResult(req.case_id, status, steps, tr)

    # ---- helpers ---------------------------------------------------------
    def _primary_file(self, linked: List[Observation], audit_at: str) -> str:
        if not linked:
            return ""
        counts: Dict[str, int] = defaultdict(int)
        for o in linked:
            counts[o.file_id] += 1
        # latest capture first, then most observations
        return sorted(counts, key=lambda f: (self.store.files[f].captured_at, counts[f]), reverse=True)[0]

    def _latest(self, obs: List[Observation]) -> Optional[Observation]:
        if not obs:
            return None
        obs = sorted(obs, key=lambda o: (o.captured_at, o.sort_key))
        latest_time = obs[-1].captured_at
        same = [o for o in obs if o.captured_at == latest_time]
        # within one capture (replica copies): any copy present wins
        present = [o for o in same if o.present]
        if present:
            # prefer the copy that still carries identifiers (worst case for the subject)
            present.sort(key=lambda o: (len(o.identifiers), o.sort_key))
            return present[-1]
        return same[-1]

    def _decide_resource(self, rid: str, obs: List[Observation], req: Request, ids: IdentitySet, primary_file: str) -> Optional[Decision]:
        latest = self._latest(obs)
        if latest is not None:
            d = decide(latest, req.audit_at)
            d.observed = True
            return d
        # no observation at/before audit_at: category from the id, state from the workflow
        cat = default_category(rid)
        if cat is None:
            return None
        system = system_for_resource(rid)
        last = self.activity.last_worker_action(rid, before=req.audit_at)
        if cat == USER_PROFILE:
            # negative evidence: a complete account_service scope that does not contain the subject
            if primary_file:
                return Decision(rid, system, cat, DELETE, compliant=True, evidence_id=primary_file, reason="absent from the subject's primary evidence file; workflow " + (f"{last.event_type}/{last.reported_result}" if last else "none"), observed=True)
            scope = self._absence_scope("account_service", ids, req.audit_at, since=req.reported_at)
            if scope is not None:
                return Decision(rid, system, cat, DELETE, compliant=True, evidence_id=scope.file_id, reason=f"absent from scope {scope.source}", observed=True)
            return Decision(rid, system, cat, DELETE, compliant=True, evidence_id="", reason="no observation; workflow " + (f"{last.event_type}/{last.reported_result}" if last else "none"), observed=False)
        if cat in (SECURITY_AUDIT_LOG,):
            from .policy import RETAIN
            return Decision(rid, system, cat, RETAIN, compliant=True, evidence_id=primary_file, reason="catalog/workflow only", observed=False)
        from .policy import REQUIRED_ACTION
        action = REQUIRED_ACTION[cat]
        ok = bool(last and last.reported_result == "SUCCESS" and last.event_type not in ("CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"))
        return Decision(rid, system, cat, action, compliant=ok, evidence_id=primary_file, reason="no observation; workflow " + (f"{last.event_type}/{last.reported_result}" if last else "none"), observed=False)

    def _catalog_fallback(self, ids: IdentitySet, req: Request):
        import re
        out = []
        for a in self.activity.links_for(ids.all_keys()):
            for rid in re.findall(r"\b[a-z_]+_\d+\b", a.details):
                if rid.startswith("user_") or rid in {x[0] for x in out}:
                    continue
                cat = default_category(rid)
                if cat is None:
                    continue
                # media file for this case: captured in (reported_at, audit_at], origin matching the system
                media = [f for f in self.store.files.values() if f.kind in ("image", "pdf") and req.reported_at <= f.captured_at <= req.audit_at]
                media.sort(key=lambda f: f.captured_at, reverse=True)
                ev_file = media[0].file_id if media else ""
                last = self.activity.last_worker_action(rid, before=req.audit_at)
                present = not (last and last.reported_result == "SUCCESS" and last.event_type not in ("CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS"))
                if ev_file:
                    from .observations import Observation as _O
                    o = _O(ev_file, self.store.files[ev_file].captured_at, rid, cat, present, identifiers=({"email": ids.current_email} if present and cat != SECURITY_AUDIT_LOG else {}),
                           subject_refs=set(ids.emails), flags={"state": "ACTIVE" if present else "DISABLED"} if cat in ("DATA_FLOW", "BACKUP_ARCHIVE") else {}, source="catalog_fallback")
                    self.store.obs_by_resource[rid].append(o)
                out.append((rid, "catalog_fallback", ev_file))
        return out

    def _absence_scope(self, system: str, ids: IdentitySet, audit_at: str, since: str = "") -> Optional[ScopeObservation]:
        best = None
        for s in self._scopes:
            fcap = self.store.files[s.file_id].captured_at
            if s.system_id != system or s.captured_at > audit_at or fcap > audit_at or (since and fcap < since):
                continue
            if ids.matches(*s.identifiers_seen):
                continue
            if best is None or s.captured_at > best.captured_at:
                best = s
        return best
