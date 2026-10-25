"""Handover notes: pipe-delimited `time | system | resource | record_type | result | subject`."""
from __future__ import annotations

import re

from audit.data import EvidenceFile
from audit.observations import ParseResult

from .records import lookup_record

LINE = re.compile(r"^\s*-?\s*(\S+)\s*\|\s*([a-z_]+)\s*\|\s*([a-z_]+_\d+)\s*\|\s*([A-Z_]+)\s*\|\s*([A-Z_]+)\s*\|\s*(.*)$")


def parse(ef: EvidenceFile, text: str) -> ParseResult:
    res = ParseResult()
    for n, line in enumerate(text.splitlines()):
        m = LINE.match(line)
        if not m:
            if "@" in line or re.search(r"[a-z_]+_\d+", line):
                res.unparsed_lines.append(line.strip())
            continue
        ts, system, rid, rtype, result, subject = m.groups()
        obs = lookup_record(ef, ts if ts.endswith("Z") else ef.captured_at, system, rid, rtype, result, subject.strip(), source=f"handover:{rtype}:{result}")
        obs.sort_key = f"{n:06d}"
        res.observations.append(obs)
    return res
