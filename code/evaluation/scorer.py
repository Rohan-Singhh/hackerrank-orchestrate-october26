"""Compare a predictions CSV with a gold CSV.

Metrics
  case-level  : deletion_status accuracy
  step-level  : exact-step precision / recall / F1 (a step = all fields except step_no)
  field-level : per-field accuracy over gold steps aligned to the closest prediction
  sequence    : fraction of cases whose ordered step list matches exactly
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from typing import Dict, List, Tuple

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def load(path) -> Dict[str, Tuple[str, List[dict]]]:
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            steps = json.loads(row["steps_json"] or "[]")
            out[row["case_id"]] = (row["deletion_status"], steps)
    return out


def _key(s: dict) -> tuple:
    return tuple(str(s.get(f, "")) for f in FIELDS)


def score(pred: Dict[str, Tuple[str, List[dict]]], gold: Dict[str, Tuple[str, List[dict]]]) -> dict:
    cases = sorted(gold)
    status_ok = 0
    tp = fp = fn = 0
    field_hits = Counter()
    field_tot = Counter()
    seq_ok = 0
    per_case = []
    for cid in cases:
        gs, gsteps = gold[cid]
        ps, psteps = pred.get(cid, ("MISSING", []))
        s_ok = gs == ps
        status_ok += s_ok
        gk = [_key(s) for s in gsteps]
        pk = [_key(s) for s in psteps]
        gset, pset = Counter(gk), Counter(pk)
        inter = sum((gset & pset).values())
        tp += inter
        fp += sum(pset.values()) - inter
        fn += sum(gset.values()) - inter
        exact = gk == pk and s_ok
        seq_ok += exact
        # field-level: align each gold step to the prediction with the same (system, resource) and phase if possible
        used = set()
        for g in gsteps:
            best, best_score = None, -1
            for i, p in enumerate(psteps):
                if i in used:
                    continue
                sc = sum(str(g.get(f)) == str(p.get(f)) for f in FIELDS)
                if sc > best_score:
                    best, best_score = i, sc
            if best is not None:
                used.add(best)
                for f in FIELDS:
                    field_tot[f] += 1
                    field_hits[f] += str(g.get(f)) == str(psteps[best].get(f))
            else:
                for f in FIELDS:
                    field_tot[f] += 1
        per_case.append({"case_id": cid, "gold_status": gs, "pred_status": ps, "exact": exact,
                         "missing": sorted(set(gk) - set(pk)), "extra": sorted(set(pk) - set(gk))})
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "cases": len(cases),
        "status_accuracy": status_ok / len(cases) if cases else 0.0,
        "status_correct": status_ok,
        "step_precision": prec, "step_recall": rec, "step_f1": f1,
        "step_tp": tp, "step_fp": fp, "step_fn": fn,
        "field_accuracy": {f: (field_hits[f] / field_tot[f] if field_tot[f] else 0.0) for f in FIELDS},
        "sequence_accuracy": seq_ok / len(cases) if cases else 0.0,
        "sequence_exact": seq_ok,
        "per_case": per_case,
    }


def format_report(res: dict, show_cases: bool = True) -> str:
    lines = [
        f"cases={res['cases']}  status_acc={res['status_accuracy']:.3f} ({res['status_correct']}/{res['cases']})  "
        f"sequence_exact={res['sequence_accuracy']:.3f} ({res['sequence_exact']}/{res['cases']})",
        f"steps: P={res['step_precision']:.3f} R={res['step_recall']:.3f} F1={res['step_f1']:.3f}  (tp={res['step_tp']} fp={res['step_fp']} fn={res['step_fn']})",
        "fields: " + "  ".join(f"{k}={v:.3f}" for k, v in res["field_accuracy"].items()),
    ]
    if show_cases:
        for c in res["per_case"]:
            if not c["exact"]:
                lines.append(f"  {c['case_id']}: gold={c['gold_status']} pred={c['pred_status']}")
                for m in c["missing"]:
                    lines.append(f"     - missing {m}")
                for e in c["extra"]:
                    lines.append(f"     + extra   {e}")
    return "\n".join(lines)
