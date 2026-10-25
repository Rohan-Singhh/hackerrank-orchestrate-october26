"""Attribute identity-less workflow resources to cases.

Evidence proves which resources belong to a subject only while the record
still carries an identifier.  Two kinds of records lose that link exactly when
the deletion worked: a deleted account (404, `profile: null`) and an anonymised
analytics profile (every identifier null).  The deletion worker's activity rows
name those resources but not the person, so we attribute them with the
structure of the activity stream:

  * cases reported at the same timestamp form a batch; the worker deletes the
    batch's accounts 40-70 minutes before `reported_at`, one every ~10 minutes,
    then runs the downstream actions;
  * the worker's result for the account delete matches the workflow's claimed
    `reported_status` (COMPLETE -> SUCCESS, NOT_STARTED -> FAILED,
    PARTIALLY_COMPLETE -> PARTIAL) in 194/198 evidence-linked cases;
  * rows already proven to belong to another subject are eliminated;
  * remaining ties are broken by closeness to `reported_at - 60 min`.

`self_evaluate` hides each evidence-known account in turn and reports how
often the rule recovers it; the number goes in the evaluation report.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional, Set, Tuple

from .activity import ActivityIndex, parse_ts
from .data import Activity, Request

TARGET_OFFSET = timedelta(minutes=60)
STATUS_RESULT = {"COMPLETE": "SUCCESS", "NOT_STARTED": "FAILED", "PARTIALLY_COMPLETE": "PARTIAL"}


def _cost(a: Activity, req: Request) -> float:
    target = parse_ts(req.reported_at) - TARGET_OFFSET
    return abs((parse_ts(a.event_time) - target).total_seconds())


def _candidates(req: Request, idx: ActivityIndex, owned: Set[str], strict_result: bool) -> List[Activity]:
    c = [a for a in idx.account_delete_candidates(req.reported_at) if a.resource_id not in owned]
    if strict_result:
        want = STATUS_RESULT.get(req.reported_status)
        c = [a for a in c if a.reported_result == want]
    return sorted(c, key=lambda a: _cost(a, req))


def assign_accounts(requests: List[Request], known: Dict[str, str], idx: ActivityIndex) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """Return (case_id -> account_id, case_id -> candidate ids) for cases not in `known`."""
    owned = set(known.values())
    result: Dict[str, str] = {}
    cand_ids: Dict[str, List[str]] = {}
    todo = [r for r in requests if r.case_id not in known]
    taken = set(owned)
    # pass 1: result-matched candidates, fewest first; pass 2: any candidate in window
    for strict in (True, False):
        pending = [r for r in todo if r.case_id not in result]
        cands = {r.case_id: [a for a in _candidates(r, idx, owned, strict) if a.resource_id not in taken] for r in pending}
        for cid, c in cands.items():
            cand_ids.setdefault(cid, [a.resource_id for a in c])
        order = sorted(pending, key=lambda r: (len(cands[r.case_id]), r.reported_at))
        for r in order:
            for a in cands[r.case_id]:
                if a.resource_id not in taken:
                    result[r.case_id] = a.resource_id
                    taken.add(a.resource_id)
                    break
    return result, cand_ids


def self_evaluate(requests: List[Request], known: Dict[str, str], idx: ActivityIndex) -> Dict[str, float]:
    """Hide each evidence-known account (leave-one-out) and test the rule."""
    hits = total = 0
    by_id = {r.case_id: r for r in requests}
    for cid, acc in known.items():
        if cid not in by_id:
            continue
        hidden = {k: v for k, v in known.items() if k != cid}
        res, _ = assign_accounts(list(by_id.values()), hidden, idx)
        total += 1
        hits += res.get(cid) == acc
    return {"evaluated": total, "correct": hits, "accuracy": round(hits / total, 3) if total else 0.0}


def assign_batch_resources(requests: List[Request], account_map: Dict[str, str], idx: ActivityIndex,
                           candidates: Dict[str, List[Tuple[str, str]]], has_category: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    """Attribute identity-less observed resources (e.g. anonymised analytics) to cases.

    candidates: category -> list of (resource_id, worker activity time) that no
    subject is linked to.  Within a batch (same reported_at) the worker handles
    cases in account-delete order, so resources of one category are assigned by
    rank to the batch's cases that lack that category.
    """
    out: Dict[str, List[str]] = defaultdict(list)
    batches: Dict[str, List[Request]] = defaultdict(list)
    for r in requests:
        batches[r.reported_at].append(r)
    acc_time = {}
    for r in requests:
        acc = account_map.get(r.case_id)
        rows = [a for a in idx.account_deletes if a.resource_id == acc] if acc else []
        acc_time[r.case_id] = rows[0].event_time if rows else r.reported_at
    for cat, items in candidates.items():
        for rep, cases in batches.items():
            first_acc = min(acc_time[r.case_id] for r in cases)
            lo = parse_ts(min(first_acc, rep)) - timedelta(minutes=30)
            hi = parse_ts(rep) + timedelta(minutes=120)
            in_batch = sorted([(t, rid) for rid, t in items if lo <= parse_ts(t) <= hi])
            if not in_batch:
                continue
            needy = sorted([r for r in cases if cat not in has_category.get(r.case_id, set())], key=lambda r: acc_time[r.case_id])
            if not needy:
                continue
            if len(in_batch) == len(needy):
                for (t, rid), r in zip(in_batch, needy):
                    out[r.case_id].append(rid)
            elif len(needy) == 1 and len(in_batch) >= 1:
                # one case lacks the category: take the resource closest after its account delete
                r = needy[0]
                after = [x for x in in_batch if x[0] >= acc_time[r.case_id]] or in_batch
                out[r.case_id].append(after[0][1])
    return out


def assign_by_cadence(requests: List[Request], owned_actions: Dict[str, str], idx: ActivityIndex,
                      candidates: Dict[str, List[Tuple[str, str]]], has_category: Dict[str, Set[str]],
                      window_minutes: int = 8) -> Dict[str, List[str]]:
    """Attribute identity-less resources by the worker's action cadence.

    The worker runs one case's downstream actions about seven minutes apart.  An
    anonymised analytics profile (no identifier left) whose ANONYMIZE row sits
    within `window_minutes` of exactly one case's linked downstream action is
    assigned to that case, provided the case does not already have that category.

    owned_actions: activity_id -> case_id for worker rows already linked to a case
    (through evidence-linked resources), account deletes excluded.
    """
    out: Dict[str, List[str]] = defaultdict(list)
    taken: Set[str] = set()
    timeline = sorted((parse_ts(a.event_time), a.activity_id, owned_actions[a.activity_id]) for a in idx.worker_rows if a.activity_id in owned_actions)
    for cat, items in candidates.items():
        for rid, t in sorted(items, key=lambda x: x[1]):
            if rid in taken:
                continue
            ts = parse_ts(t)
            near = {case for (tt, _aid, case) in timeline if abs((tt - ts).total_seconds()) <= window_minutes * 60}
            if len(near) != 1:
                continue
            case = next(iter(near))
            if cat in has_category.get(case, set()) or any(x for x in out[case]):
                continue
            out[case].append(rid)
            taken.add(rid)
    return out
