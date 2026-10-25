"""Generate evaluation/evaluation_report.md: sample metrics, custom cases, pseudo-gold,
attribution self-evaluation, intent coverage, failures and fixes log.

    python code/evaluation/report.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
REPO = CODE.parent
sys.path.insert(0, str(CODE))

from evaluation import custom_cases  # noqa: E402
from evaluation.scorer import load, score  # noqa: E402

OUT = CODE / "evaluation" / "evaluation_report.md"
FIXLOG = CODE / "evaluation" / "failures_and_fixes.md"
PSEUDO = CODE / "evaluation" / "pseudo_gold.json"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO)).stdout


def metrics_table(res: dict, title: str) -> str:
    fa = res["field_accuracy"]
    return "\n".join([
        f"### {title}", "",
        "| Metric | Value |", "|---|---|",
        f"| Cases | {res['cases']} |",
        f"| Case-level status accuracy | {res['status_accuracy']:.3f} ({res['status_correct']}/{res['cases']}) |",
        f"| Step-level precision / recall / F1 (exact step match) | {res['step_precision']:.3f} / {res['step_recall']:.3f} / {res['step_f1']:.3f} (tp={res['step_tp']}, fp={res['step_fp']}, fn={res['step_fn']}) |",
        f"| Sequence accuracy (whole ordered list exact) | {res['sequence_accuracy']:.3f} ({res['sequence_exact']}/{res['cases']}) |",
        "| Field accuracy: phase | " + f"{fa['phase']:.3f} |",
        "| Field accuracy: system_id | " + f"{fa['system_id']:.3f} |",
        "| Field accuracy: resource_id | " + f"{fa['resource_id']:.3f} |",
        "| Field accuracy: operation | " + f"{fa['operation']:.3f} |",
        "| Field accuracy: state | " + f"{fa['state']:.3f} |",
        "| Field accuracy: evidence_id | " + f"{fa['evidence_id']:.3f} |",
    ])


def per_case_block(res: dict) -> str:
    lines = ["", "| Case | Gold status | Predicted | Exact | Missing steps | Extra steps |", "|---|---|---|---|---|---|"]
    for c in res["per_case"]:
        miss = "; ".join(f"{m[3]} {m[2]}" for m in c["missing"]) or "-"
        extra = "; ".join(f"{m[3]} {m[2]}" for m in c["extra"]) or "-"
        lines.append(f"| {c['case_id']} | {c['gold_status']} | {c['pred_status']} | {'yes' if c['exact'] else 'no'} | {miss} | {extra} |")
    return "\n".join(lines)


def main() -> int:
    sample_out = run([sys.executable, str(CODE / "main.py"), "--sample", "--no-trace", "--offline"])
    gold = {cid: (row["deletion_status"], json.loads(row["steps_json"])) for cid, row in
            ((r["case_id"], r) for r in __import__("csv").DictReader(open(REPO / "dataset" / "sample_output.csv", encoding="utf-8", newline="")))}
    sample_res = score(load(REPO / "output_sample.csv"), gold)
    custom_res, _ = custom_cases.run()
    pseudo_block = ""
    if PSEUDO.exists():
        pg = json.loads(PSEUDO.read_text(encoding="utf-8"))
        pred = load(REPO / "output.csv")
        pgold = {k: (v["status"], v["steps"]) for k, v in pg.items()}
        pres = score({k: pred[k] for k in pgold if k in pred}, pgold)
        pseudo_block = metrics_table(pres, f"Hand-adjudicated pseudo-gold ({len(pgold)} real non-sample cases)") + per_case_block(pres) + "\n\n" + \
            "These cases were adjudicated by hand from the evidence files before the engine was run on them; they are the only test of generalisation beyond the public samples.\n"
    attribution = ""
    cov = CODE / "evaluation" / "coverage_report.json"
    cov_block = ""
    if cov.exists():
        c = json.loads(cov.read_text(encoding="utf-8"))
        files = c.get("files", [])
        cov_block = "\n".join([
            "| Evidence files | Parsed | Observations | Subject-linked | Unparsed identifier lines |", "|---|---|---|---|---|",
            f"| {len(files)} | {sum(1 for f in files if f.get('observations', 0) > 0)} | {sum(f.get('observations', 0) for f in files)} | {sum(f.get('linked', 0) for f in files)} | {sum(f.get('unparsed_lines', 0) for f in files)} |",
            "", f"Intent gate sources on the full run: `{c.get('intent_stats')}`; review-list items: {len(c.get('review', []))} "
            f"(ambiguous account attribution, zero-evidence cases, catalog fallbacks).",
        ])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = "\n".join([
        "# Evaluation report", "", f"Generated {now} by `python code/evaluation/report.py`.", "",
        "## 1. Method", "",
        "Predictions are compared with gold answers as parsed JSON (key order and whitespace ignored, array order kept).",
        "- **Case-level**: `deletion_status` equality.",
        "- **Step-level**: a step is the tuple (phase, system_id, resource_id, operation, state, evidence_id) without `step_no`; precision/recall/F1 over multiset intersection.",
        "- **Field-level**: each gold step is aligned to the most similar unused predicted step; accuracy per field.",
        "- **Sequence**: the whole ordered list (and status) must match exactly.", "",
        "## 2. Public samples (32 cases, `dataset/sample_output.csv`)", "",
        metrics_table(sample_res, "Sample metrics"), per_case_block(sample_res), "",
        "Sample-specific error floor: 3 of 121 gold resources (`audit_record_6645`, `audit_record_5569`, `account_004`) occur in no participant-visible file; several others only in files captured after `audit_at`. Those steps cannot be produced from the supplied data.", "",
        "## 3. Custom evaluation cases (5 synthetic worlds, `code/evaluation/custom_cases.py`)", "",
        "| Case | What it pins down | Gold |", "|---|---|---|",
        "| custom_01 | historical email as request email; tombstone still carrying a phone | INCOMPLETE: REPORT_EXPOSED_PII tombstone → REDACT; verify account absent, audit record present, tombstone redacted |",
        "| custom_02 | marketing opt-out with a decoy DELETE_RECORD row in the activity stream | NOT_APPLICABLE, [] |",
        "| custom_03 | replica drift (primary absent, regional replica present) + restorable backup | INCOMPLETE: remaining record + restore risk; BLOCK_RESTORE before DELETE_RECORD |",
        "| custom_04 | evidence captured one minute after audit_at must be ignored | COMPLETE on the earlier proof |",
        "| custom_05 | sync job recreated the account after a successful delete (ghost) | INCOMPLETE: REPORT_REMAINING_RECORD on the account |",
        "", metrics_table(custom_res, "Custom-case metrics"), per_case_block(custom_res), "",
        "## 4. Generalisation checks", "", pseudo_block, cov_block, "",
        "## 5. Failures found and improvements made", "",
        FIXLOG.read_text(encoding="utf-8") if FIXLOG.exists() else "_(see failures_and_fixes.md)_", "",
    ])
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
