"""Loaders for the participant-facing CSV files.

Everything is read as UTF-8 with newline="" so the code behaves identically on
Windows (cp1252 default codec) and Linux.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


def read_csv(path: Path) -> List[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Request:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str

    @classmethod
    def from_row(cls, row: dict) -> "Request":
        return cls(
            case_id=row["case_id"],
            email=row["email"],
            requested_at=row["requested_at"],
            reported_status=row["reported_status"],
            reported_at=row["reported_at"],
            audit_at=row["audit_at"],
            request_text=row["request_text"],
        )


@dataclass
class Activity:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class EvidenceFile:
    file_id: str
    file_path: str  # relative to dataset/
    origin: str
    captured_at: str

    @property
    def abs_path(self) -> Path:
        p = (DATASET_DIR / self.file_path).resolve()
        if not p.is_relative_to(DATASET_DIR.resolve()):
            raise ValueError(f"evidence path escapes dataset/: {self.file_path}")
        return p

    @property
    def kind(self) -> str:
        return p_kind(self.file_path)


def p_kind(path: str) -> str:
    low = path.lower()
    if low.endswith(".png"):
        return "image"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".sql"):
        return "sql"
    if low.endswith(".json"):
        return "json"
    if low.endswith(".log"):
        return "log"
    if low.endswith(".txt"):
        return "txt"
    return "unknown"


@dataclass
class Dataset:
    requests: List[Request]
    identities: List[dict]
    audit_log: List[dict]
    activities: List[Activity]
    rules: List[dict]
    evidence: List[EvidenceFile]
    sample_requests: List[Request] = field(default_factory=list)
    sample_output: Dict[str, dict] = field(default_factory=dict)


def load_dataset(dataset_dir: Path = DATASET_DIR, requests_file: str = "deletion_requests.csv") -> Dataset:
    d = Path(dataset_dir)
    missing = [n for n in ("user_identities.csv", "user_audit_log.csv", "system_activity.csv", "deletion_rules.csv", "evidence_files.csv", requests_file) if not (d / n).exists()]
    if missing:
        raise FileNotFoundError(
            f"dataset files missing: {missing}\n"
            f"cause: code.zip excludes dataset/ by rule\n"
            f"fix: place the organizer dataset at {d} or pass --dataset <dir>"
        )
    requests = [Request.from_row(r) for r in read_csv(d / requests_file)]
    identities = read_csv(d / "user_identities.csv")
    audit_log = read_csv(d / "user_audit_log.csv")
    activities = [Activity(**{k: r[k] for k in Activity.__dataclass_fields__}) for r in read_csv(d / "system_activity.csv")]
    rules = read_csv(d / "deletion_rules.csv")
    evidence = [EvidenceFile(r["file_id"], r["file_path"], r["origin"], r["captured_at"]) for r in read_csv(d / "evidence_files.csv")]
    sample_requests = [Request.from_row(r) for r in read_csv(d / "sample_deletion_requests.csv")] if (d / "sample_deletion_requests.csv").exists() else []
    sample_output = {r["case_id"]: r for r in read_csv(d / "sample_output.csv")} if (d / "sample_output.csv").exists() else {}
    return Dataset(requests, identities, audit_log, activities, rules, evidence, sample_requests, sample_output)
