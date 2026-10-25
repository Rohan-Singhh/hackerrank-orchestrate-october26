"""Evidence parsers: one module per format family, dispatched by file signature.

Every parser returns a ParseResult of Observation / ScopeObservation records.
Parsers never consult identities or policy; they only normalise the file.
"""
from __future__ import annotations

import json
from typing import Callable, Dict

from audit.data import EvidenceFile
from audit.observations import ParseResult

from . import jsonrec, logs, pdf, sql, txt


class UnknownFormat(Exception):
    pass


def read_text(path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def parse_file(ef: EvidenceFile) -> ParseResult:
    kind = ef.kind
    if kind == "image":
        raise UnknownFormat("screenshots are read by llm.vision (or the data-catalog fallback)")
    if kind == "pdf":
        return pdf.parse(ef, pdf.pdf_text(ef.abs_path))
    text = read_text(ef.abs_path)
    if kind == "sql":
        return sql.parse(ef, text)
    if kind == "txt":
        return txt.parse(ef, text)
    if kind == "json":
        return jsonrec.parse(ef, json.loads(text))
    if kind == "log":
        return logs.parse(ef, text)
    raise UnknownFormat(ef.file_path)
