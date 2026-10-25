"""Identity resolution: request email -> user_id -> full identifier set.

The identifier set contains every value that may still name the subject in an
older system: current and historical emails, email hashes, phones, the stable
user_id and the full name.  Matching elsewhere is exact after normalisation
(lower-cased emails/hashes, digits-only phones, NFKD-folded names); no fuzzy or
substring matching, because the data contains near-miss decoys such as
`smartinez6461.jr@gmail.com` vs `smartinez6461@gmail.com`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


def norm_email(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def norm_phone(v: Optional[str]) -> str:
    return re.sub(r"\D", "", v or "")


def norm_name(v: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", v or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_hash(v: Optional[str]) -> str:
    return (v or "").strip().lower()


@dataclass
class IdentitySet:
    user_id: str
    name: str
    emails: Set[str] = field(default_factory=set)
    hashes: Set[str] = field(default_factory=set)
    phones: Set[str] = field(default_factory=set)
    current_email: str = ""

    @property
    def names(self) -> Set[str]:
        return {norm_name(self.name)} if self.name else set()

    def all_keys(self) -> Set[str]:
        """Every normalised value that identifies this subject (user_id included)."""
        return set(self.emails) | set(self.hashes) | set(self.phones) | {self.user_id}

    def matches(self, *values: Optional[str]) -> bool:
        """True if any value equals one of the subject's identifiers (exact, normalised)."""
        for v in values:
            if not v:
                continue
            s = str(v).strip()
            low = s.lower()
            if low == self.user_id:
                return True
            if "@" in low and low in self.emails:
                return True
            if re.fullmatch(r"[0-9a-f]{64}", low) and low in self.hashes:
                return True
            digits = norm_phone(s)
            if len(digits) >= 7 and digits in self.phones:
                return True
        return False

    def matches_name(self, v: Optional[str]) -> bool:
        return bool(v) and norm_name(v) in self.names


class IdentityResolver:
    def __init__(self, identities: List[dict], audit_log: List[dict]):
        self.by_user: Dict[str, dict] = {r["user_id"]: r for r in identities}
        self.email_to_user: Dict[str, str] = {}
        self.audit_by_user: Dict[str, List[dict]] = {}
        for r in identities:
            self.email_to_user[norm_email(r["email"])] = r["user_id"]
        for a in audit_log:
            self.audit_by_user.setdefault(a["user_id"], []).append(a)
            if a["field_name"] == "email":
                for v in (a["old_value"], a["new_value"]):
                    self.email_to_user.setdefault(norm_email(v), a["user_id"])
        self.name_to_users: Dict[str, List[str]] = {}
        for r in identities:
            self.name_to_users.setdefault(norm_name(r["name"]), []).append(r["user_id"])

    def resolve_email(self, email: str) -> Optional[str]:
        return self.email_to_user.get(norm_email(email))

    def identity_set(self, user_id: str) -> IdentitySet:
        r = self.by_user[user_id]
        ids = IdentitySet(user_id=user_id, name=r["name"], current_email=norm_email(r["email"]))
        ids.emails.add(norm_email(r["email"]))
        ids.hashes.add(norm_hash(r["email_hash"]))
        ids.phones.add(norm_phone(r["phone"]))
        for a in self.audit_by_user.get(user_id, []):
            for v in (a["old_value"], a["new_value"]):
                if a["field_name"] == "email":
                    ids.emails.add(norm_email(v))
                elif a["field_name"] == "email_hash":
                    ids.hashes.add(norm_hash(v))
                elif a["field_name"] == "phone":
                    ids.phones.add(norm_phone(v))
        ids.emails.discard("")
        ids.hashes.discard("")
        ids.phones.discard("")
        return ids

    def resolve(self, email: str) -> Optional[IdentitySet]:
        uid = self.resolve_email(email)
        return self.identity_set(uid) if uid else None
