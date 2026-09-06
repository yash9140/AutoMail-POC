"""Deterministic sender-identity parsing and matching (2026-09-06).

Outlook (and Vision's own transcription of what Outlook shows) can
represent the identical sender identity as any of:

    Yash
    Yash <yashdhanraj9140@gmail.com>
    Yash<yashdhanraj9140@gmail.com>        (no space before '<')
    "Yash" <yashdhanraj9140@gmail.com>

A live run's EMAIL_OPEN_VERIFICATION treated the whole string as one
opaque value, so a target_sender of "Yash" was rejected as a
sender_mismatch against a visible "Yash<yashdhanraj9140@gmail.com>" —
even though the correct email had already been opened (sender AND
subject both semantically confirmed by Vision) and was fully readable.
This module fixes that by parsing BOTH the target and the detected
sender into (display_name, email_address) components — via the standard
library's email.utils.parseaddr, never a hand-rolled regex — before
comparing.

This is NOT a relaxation of sender matching: every comparison below is
still exact normalized-string equality. There is no substring/
startswith/contains check and no fuzzy-similarity ratio anywhere in this
module — this project has already been burned once by exactly that kind
of shortcut (a live incident elsewhere: target_sender="Yash" matched
candidate_sender="Yash Dhanraj" via substring containment and opened the
wrong email). Vision's own sender_match/subject_match self-assessment is
never consulted here either — see callers for how that stays audit-only.

Used ONLY by app/outlook/find_email.py::verify_email_opened() (the
EMAIL_OPEN_VERIFICATION accept/reject decision). TARGET_EMAIL_SEARCH's
own candidate sender/subject matching (_evaluate_candidate) and the
identity-refinement re-check (_refine_row_identity) are unaffected —
those compare a sender as reported in the message-LIST row, which this
project's existing evidence shows is normally a plain display name, not
this "Name <email>" shape, and reworking that behavior is out of scope
for this fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr


def _normalize(text: str) -> str:
    """Deterministic normalization: trim, collapse internal whitespace,
    casefold. No fuzzy/similarity matching of any kind."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


@dataclass(frozen=True)
class ParsedSender:
    """display_name / email_address are both already normalized (trimmed,
    internal whitespace collapsed, casefolded) — an empty string means
    that component was not present in the source text."""

    display_name: str
    email_address: str

    @property
    def has_email(self) -> bool:
        return bool(self.email_address)


def parse_sender(raw: str) -> ParsedSender:
    """Parses a raw Outlook/Vision-reported sender string into its
    display-name and email-address components.

    email.utils.parseaddr() is used ONLY when the raw text actually
    contains an '@' character. parseaddr's own tokenizer, given a string
    with no '@' and no angle brackets at all (a plain display name),
    unreliably keeps only the FIRST whitespace-separated token as its
    "address" slot and silently drops the rest — empirically,
    parseaddr("Yash Dhanraj") == ("", "Yash Dhanraj") but
    parseaddr("Yash Dhanraj Extra") == ("", "Yash") — never trusted for
    a plain name with no email marker present. In that case the entire
    raw string is normalized and treated as the display name instead,
    exactly matching this project's pre-existing plain-name comparison
    behavior for Case D (see module docstring)."""
    raw = raw or ""
    if "@" in raw:
        realname, addr = parseaddr(raw)
        if "@" in addr:
            return ParsedSender(display_name=_normalize(realname), email_address=_normalize(addr))
    return ParsedSender(display_name=_normalize(raw), email_address="")


def sender_mode(parsed: ParsedSender) -> str:
    """Classifies a parsed sender for diagnostic logging only — never
    used in the matching decision itself. One of "name", "email",
    "name_email"."""
    if parsed.has_email and parsed.display_name:
        return "name_email"
    if parsed.has_email:
        return "email"
    return "name"


def sender_matches(target_sender: str, detected_sender: str) -> bool:
    """Deterministic, non-fuzzy sender-identity match.

    - Target supplies an email address (an email alone, or "Name
      <email>"): that email address must equal the detected sender's own
      email address exactly — email is the authoritative identity
      signal whenever the target specifies one, regardless of how
      similar any display name looks. If the target ALSO supplies a
      non-empty display name AND the detected sender also has one, both
      must additionally match; a target given as a bare email address is
      never rejected over a display-name difference it never expressed
      an opinion on.
    - Target supplies no email address (a plain display name): the
      detected sender's own display name must equal the target exactly.
      An empty target display name never matches anything.

    Never a substring/startswith/contains check, never a fuzzy-ratio
    comparison, never influenced by Vision's own sender_match claim."""
    target = parse_sender(target_sender)
    detected = parse_sender(detected_sender)

    if target.has_email:
        if target.email_address != detected.email_address:
            return False
        if target.display_name and detected.display_name:
            return target.display_name == detected.display_name
        return True

    return bool(target.display_name) and target.display_name == detected.display_name
