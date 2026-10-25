"""Common record emitted by every evidence parser.

An Observation is one statement an evidence file makes about one resource at
one capture time.  Parsers never decide policy; they only normalise what the
file says: which resource, which category of data, whether it still exists,
which direct identifiers it still carries, and the flags that matter for the
rules (enabled, restore_allowed, retention_until ...).

A ScopeObservation says "this file enumerated system X at time T and these are
all the identifiers it contained" so that the engine can prove absence
(negative evidence) when a subject's identifiers do not appear in a scope that
would have listed them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# Data categories (deletion_rules.csv vocabulary plus the ones the evidence uses)
USER_PROFILE = "USER_PROFILE"
SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
SEARCH_INDEX_ENTRY = "SEARCH_INDEX_ENTRY"
CACHE_ENTRY = "CACHE_ENTRY"
SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
ANALYTICS_PROFILE = "ANALYTICS_PROFILE"
PRODUCT_TELEMETRY = "PRODUCT_TELEMETRY"
USER_EXPORT = "USER_EXPORT"
MARKETING_PROFILE = "MARKETING_PROFILE"
EMAIL_DELIVERY_LOG = "EMAIL_DELIVERY_LOG"
SECURITY_AUDIT_LOG = "SECURITY_AUDIT_LOG"
LEGAL_HOLD_RECORD = "LEGAL_HOLD_RECORD"
BACKUP_ARCHIVE = "BACKUP_ARCHIVE"
ACCOUNT_TOMBSTONE = "ACCOUNT_TOMBSTONE"
DATA_FLOW = "DATA_FLOW"

CATEGORIES = {
    USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY, SUPPORT_MESSAGE,
    SUPPORT_ATTACHMENT, ANALYTICS_PROFILE, PRODUCT_TELEMETRY, USER_EXPORT, MARKETING_PROFILE,
    EMAIL_DELIVERY_LOG, SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD, BACKUP_ARCHIVE, ACCOUNT_TOMBSTONE,
    DATA_FLOW,
}

# resource id prefix -> (system_id, default category)
PREFIX_SYSTEM = {
    "account": ("account_service", USER_PROFILE),
    "search": ("search_service", SEARCH_INDEX_ENTRY),
    "analytics": ("analytics_platform", ANALYTICS_PROFILE),
    "support": ("support_platform", SUPPORT_MESSAGE),
    "export": ("export_service", USER_EXPORT),
    "backup": ("backup_service", BACKUP_ARCHIVE),
    "data_flow": ("integration_service", DATA_FLOW),
    "audit_record": ("security_service", SECURITY_AUDIT_LOG),
    "tombstone": ("identity_service", ACCOUNT_TOMBSTONE),
    "legal_hold": ("legal_service", LEGAL_HOLD_RECORD),
    "marketing": ("marketing_platform", MARKETING_PROFILE),
    "cache": ("cache_service", CACHE_ENTRY),
    "email_delivery": ("email_delivery_service", EMAIL_DELIVERY_LOG),
    "object": ("object_storage", SUPPORT_ATTACHMENT),
}

# Category -> system id for the output steps (origin system of the record)
CATEGORY_SYSTEM = {
    USER_PROFILE: "account_service",
    SECONDARY_IDENTIFIER: "analytics_platform",
    SEARCH_INDEX_ENTRY: "search_service",
    CACHE_ENTRY: "cache_service",
    SUPPORT_MESSAGE: "support_platform",
    SUPPORT_ATTACHMENT: "support_platform",
    ANALYTICS_PROFILE: "analytics_platform",
    PRODUCT_TELEMETRY: "analytics_platform",
    USER_EXPORT: "export_service",
    MARKETING_PROFILE: "marketing_platform",
    EMAIL_DELIVERY_LOG: "email_delivery_service",
    SECURITY_AUDIT_LOG: "security_service",
    LEGAL_HOLD_RECORD: "legal_service",
    BACKUP_ARCHIVE: "backup_service",
    ACCOUNT_TOMBSTONE: "identity_service",
    DATA_FLOW: "integration_service",
}


def resource_prefix(resource_id: str) -> str:
    return resource_id.rsplit("_", 1)[0]


def system_for_resource(resource_id: str) -> str:
    return PREFIX_SYSTEM.get(resource_prefix(resource_id), ("unknown", None))[0]


def default_category(resource_id: str) -> Optional[str]:
    return PREFIX_SYSTEM.get(resource_prefix(resource_id), (None, None))[1]


@dataclass
class Observation:
    file_id: str
    captured_at: str
    resource_id: str
    category: str
    present: Optional[bool]  # True: record exists; False: proven absent; None: unknown
    system_id: str = ""
    identifiers: Dict[str, str] = field(default_factory=dict)  # direct identifiers still on the record
    subject_refs: Set[str] = field(default_factory=set)  # every value that could name a subject (lookup keys, refs)
    flags: Dict[str, object] = field(default_factory=dict)  # enabled, restore_allowed, retention_until, ...
    source: str = ""  # parser/table/event, for traces
    sort_key: str = ""  # secondary ordering inside a file (line number / snapshot time)

    def __post_init__(self):
        if not self.system_id:
            self.system_id = CATEGORY_SYSTEM.get(self.category) or system_for_resource(self.resource_id)
        self.subject_refs = {s.strip().lower() for s in self.subject_refs if s and str(s).strip()}
        self.identifiers = {k: v for k, v in self.identifiers.items() if v not in (None, "", "null", "None")}

    @property
    def identifiable(self) -> bool:
        """True if the record still carries a direct identifier (name, email, phone, hash)."""
        return any(k in self.identifiers for k in ("email", "phone", "email_hash", "name", "user_id"))

    @property
    def direct_pii(self) -> bool:
        """Direct identifiers other than the stable user_id (what a tombstone must drop)."""
        return any(k in self.identifiers for k in ("email", "phone", "email_hash", "name"))


@dataclass
class ScopeObservation:
    """A file (or table) enumerated a system completely at capture time."""
    file_id: str
    captured_at: str
    system_id: str
    identifiers_seen: Set[str] = field(default_factory=set)
    resource_ids_seen: Set[str] = field(default_factory=set)
    source: str = ""


@dataclass
class ParseResult:
    observations: List[Observation] = field(default_factory=list)
    scopes: List[ScopeObservation] = field(default_factory=list)
    unparsed_lines: List[str] = field(default_factory=list)  # lines with ids/emails no parser understood
    notes: List[str] = field(default_factory=list)
