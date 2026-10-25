"""Print everything a human adjudicator needs for one case: request, identity set,
raw evidence lines in the audit window that mention the subject, deletion-worker
rows for the linked resources, and the engine's current answer.

    python code/scripts/show_case.py case_41 [case_42 ...]
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
REPO = CODE.parent
sys.path.insert(0, str(CODE))

from audit.activity import ActivityIndex  # noqa: E402
from audit.data import load_dataset  # noqa: E402
from audit.identity import IdentityResolver  # noqa: E402

RID = re.compile(r"\b(?:account|search|analytics|support|export|backup|data_flow|audit_record|tombstone|legal_hold|marketing|object)_\d+\b")


def main(argv):
    ds = load_dataset(REPO / "dataset")
    res = IdentityResolver(ds.identities, ds.audit_log)
    act = ActivityIndex(ds.activities)
    pred = {}
    if (REPO / "output.csv").exists():
        pred = {r["case_id"]: r for r in csv.DictReader(open(REPO / "output.csv", encoding="utf-8", newline=""))}
    for cid in argv:
        r = next((x for x in ds.requests + ds.sample_requests if x.case_id == cid), None)
        if not r:
            print("no such case", cid)
            continue
        ids = res.resolve(r.email)
        print("=" * 100)
        print(f"{cid} email={r.email} user={ids.user_id if ids else None} status_claim={r.reported_status} requested={r.requested_at} reported={r.reported_at} audit={r.audit_at}")
        print(f"text: {r.request_text[:160]}")
        if not ids:
            continue
        keys = sorted(ids.all_keys())
        print("identifiers:", [k for k in keys if '@' in k], "phones", sorted(ids.phones), "name", ids.name)
        seen_rids = set()
        for ef in sorted(ds.evidence, key=lambda e: e.captured_at):
            if ef.captured_at > r.audit_at or ef.captured_at < r.reported_at:
                continue
            if ef.kind == "image":
                continue
            try:
                if ef.kind == "pdf":
                    from parsers.pdf import pdf_text
                    text = pdf_text(ef.abs_path)
                else:
                    text = open(ef.abs_path, encoding="utf-8", errors="replace").read()
            except Exception as exc:
                print("  cannot read", ef.file_id, exc)
                continue
            lines = text.splitlines()
            hits = []
            for i, line in enumerate(lines):
                l = line.lower()
                if any(k in l for k in keys if k):
                    hits.append(i)
            if not hits:
                continue
            print(f"--- {ef.file_id} {ef.file_path} captured {ef.captured_at}")
            shown = set()
            for i in hits:
                for j in range(max(0, i - 6), min(len(lines), i + 6)):
                    if j in shown:
                        continue
                    if j == i or RID.search(lines[j]) or 'resource_id' in lines[j] or '"email"' in lines[j] or 'subject' in lines[j]:
                        shown.add(j)
                        print(f"   {j + 1:5d}: {lines[j][:230]}")
                        seen_rids.update(RID.findall(lines[j]))
        print("--- deletion worker rows for resources seen:")
        for rid in sorted(seen_rids):
            for a in act.worker_actions(rid):
                print(f"   {a.activity_id} {a.event_time} {a.event_type} {a.target_system} {rid} {a.reported_result}")
        print("--- account delete candidates in window:")
        for a in act.account_delete_candidates(r.reported_at):
            print(f"   {a.activity_id} {a.event_time} {a.resource_id} {a.reported_result}")
        if cid in pred:
            print("--- current prediction:", pred[cid]["deletion_status"])
            for s in json.loads(pred[cid]["steps_json"]):
                print(f"   {s['step_no']:2d} {s['phase']:12s} {s['system_id']:20s} {s['resource_id']:18s} {s['operation']:26s} {s['state']:10s} {s['evidence_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
