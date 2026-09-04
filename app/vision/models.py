"""Leaner, forward-looking Vision response schemas for the final POC.

Phase 1 scope: declares EmailSection (the structured per-scroll-section
record the long-email accumulation loop will populate — see
docs/architecture/04_SCROLLING_AND_LONG_EMAIL.md) ahead of Phase 4's actual
read_email.py implementation, so app/playbook/context.py can reference
a real type instead of `Any`. Not yet populated by any step this phase.

The existing RND-stage-numbered response schemas (EmailGroundingResponse,
EmailUnderstandingResponse, ReplyGenerationResponse, etc., under
rnd/models/*.py) are reused as-is by the moved step modules in this
phase — they are not yet duplicated here. A later cleanup pass may
promote them here once every phase that touches them has landed, per
the plan's "leave the per-stage models as R&D-verbose for now, revisit
once the full chain is built" note.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ReplyExpectation:
    """Semantic classification of whether/how a reply is expected —
    replaces the too-restrictive binary requires_reply (Phase 4/5
    live-edge-case fix, 2026-09-02: an informational/status email with
    no explicit request was correctly NOT obligating a reply, but the
    user had explicitly targeted it for a reply run — "does the sender
    require a reply" and "does the user intend to reply" are different
    questions). See app/outlook/read_email.py::_finalize_understanding()
    for the deterministic policy that consumes this — only
    SHOULD_NOT_REPLY is ever a safe stop; MUST_REPLY and OPTIONAL_REPLY
    both continue, because this POC is inherently a user-initiated
    targeted reply run."""

    MUST_REPLY = "MUST_REPLY"       # explicitly asks for a response/confirmation/answer/decision/information
    OPTIONAL_REPLY = "OPTIONAL_REPLY"  # no explicit ask, but a normal human reply is reasonable (FYI, status, thanks)
    SHOULD_NOT_REPLY = "SHOULD_NOT_REPLY"  # a normal Reply would be inappropriate/unsafe/non-conversational


ReplyExpectationValue = Literal["MUST_REPLY", "OPTIONAL_REPLY", "SHOULD_NOT_REPLY"]


OUTLOOK_SEARCH_TARGET_TYPES = {"desktop_app", "web_result", "settings", "help", "other"}


class OutlookSearchGroundingResponse(BaseModel):
    """Windows-Search Outlook-result grounding (live coordinate-contract
    fix, 2026-09-02; semantic-quality hardening, 2026-09-03). Replaces
    the RND-009B-era single-loose-point WindowsSearchGroundingResponse
    (rnd/models/outlook_launch.py — historical, never modified, and
    never used by the live runtime anymore) with a FULL BBOX, per the
    "bbox over loose points" rule app/safety/validators.py already
    documents: a validated bbox lets the click point be verified as
    genuinely inside the target region, where a bare (x, y) claim can
    never be self-checked.

    bbox uses the SAME [y_min, x_min, y_max, x_max], 0-1000-normalized
    convention as every other bbox in this project (EmailCandidate.
    row_bbox, ReplySearchResponse.bbox, SendSearchResponse.bbox) —
    deliberately NOT introducing a second convention.

    target_type (2026-09-03 hardening): a live bug survived the bbox fix
    — the cursor now landed inside SOME bbox, but not necessarily the
    single clickable desktop-app result, because the old schema only
    asked "is an Outlook result visible" with no way to distinguish the
    desktop app tile from a web result / Settings / Help / Store
    suggestion that also visually mentions "Outlook". Vision still only
    REPORTS what it sees (search_visible/target_visible/target_type/
    visible_label/bbox/confidence) — it never decides whether to click;
    app/outlook/launch.py::ground_search_result() is the one place that
    deterministically requires target_visible AND target_type ==
    "desktop_app" AND visible_label identifies Outlook AND a valid bbox
    AND confidence above threshold before any physical action is even
    considered eligible.

    visible_sublabel / bbox_tightly_scoped (2026-09-03, second live-
    evidence round: a debug-overlay screenshot showed target_type/
    visible_label/confidence were all correctly reported, but the bbox
    itself covered the "Best match" section header above the Outlook row
    instead of the row itself — a semantic BBOX-SCOPING bug, distinct
    from the target-identity bug target_type/visible_label already
    catch). visible_sublabel is the secondary text under the result
    label (e.g. "App") — an independent identity signal a header or
    container would never have. bbox_tightly_scoped is Claude's own
    explicit answer to a narrower, separate question the prompt asks
    it to self-check before answering: "does your bbox contain ONLY the
    icon+label+sublabel row, or does it also include the header/
    container above it?" — kept distinct from target_type/visible_label
    because identity and bbox-scoping are different claims that can
    disagree (exactly what happened live). Like every other field here,
    it is still just Vision reporting what it believes, never a
    click-authorization — see ground_search_result()'s optional,
    strictly-bounded single same-screenshot refine pass for what happens
    when this is False."""

    search_visible: bool
    # Whether the single clickable Outlook desktop-app search result is
    # visible — deliberately NOT "is anything Outlook-related visible"
    # (see target_type for why that distinction matters).
    target_visible: bool = False
    target_type: str = "other"  # desktop_app | web_result | settings | help | other
    visible_label: str = ""
    visible_sublabel: str = ""  # e.g. "App" — secondary text under the label, if visible
    bbox: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    # Claude's own answer to "is this bbox tightly scoped to ONLY the
    # clickable row, excluding any header/container above/around it?" —
    # a narrower, separate self-check from target_type/visible_label.
    bbox_tightly_scoped: bool = True
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v

    @field_validator("target_type")
    @classmethod
    def target_type_known(cls, v: str) -> str:
        # An unrecognized value is coerced to "other" (never actionable)
        # rather than raising — a minor label variance from the provider
        # should degrade to "not actionable", not blow up the whole
        # grounding response as a technical error. Vision still never
        # gets to authorize a click either way.
        return v if v in OUTLOOK_SEARCH_TARGET_TYPES else "other"


class OutlookSearchRefineResponse(BaseModel):
    """Second-pass, SAME-screenshot bbox-tightening response (2026-09-03)
    — see ground_search_result()'s refine-pass docstring. Deliberately
    minimal: identity (target_type/visible_label) was already trusted
    from pass 1; this call asks ONLY for a tighter bbox for the row
    already identified, or an honest "still can't confidently localize
    it" (target_visible=false) — never fabricated coordinates."""

    target_visible: bool = False
    bbox: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailCandidate(BaseModel):
    """One message-list row Vision reports as a possible match for the
    playbook-supplied target criteria (sender required, subject
    optional — see app/outlook/find_email.py). row_bbox must be the
    FULL CLICKABLE ROW, never just the sender/subject text or an icon —
    the prompt (app/vision/prompts/email_search_v1.txt) says so
    explicitly, and app/safety/validators.py::validate_grounding()
    rejects a degenerate (zero-area) or out-of-range box regardless.

    subject is ALWAYS only the text actually visible in the row — never
    reconstructed or guessed. subject_truncated=True means Outlook's own
    UI is visibly clipping/ellipsizing that text (narrow column, long
    subject) — the playbook (app/outlook/find_email.py) treats a
    truncated subject as, at most, a PROVISIONAL prefix match, never a
    confirmed one; only a fresh full-text read after the email is
    actually opened can confirm it."""

    sender: str = ""
    subject: str = ""
    subject_truncated: bool = False
    date_or_order: str = ""
    row_bbox: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    confidence: float = 0.0

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailSearchResponse(BaseModel):
    """Structured, provider-neutral search result — Vision reports
    everything it sees that could match; the playbook (deterministic
    Python) decides which candidate, if any, is unambiguous enough to
    click. Vision is never asked to pick "the" email itself."""

    outlook_visible: bool
    message_list_visible: bool
    target_visible: bool
    candidate_count: int = 0
    candidates: list[EmailCandidate] = Field(default_factory=list)
    more_content_below: bool = False
    reason: str = ""


class ReplySearchResponse(BaseModel):
    """Structured, provider-neutral Reply-control search result (Phase
    5). Vision reports the ONE control it believes is Reply (if any) —
    the playbook (deterministic Python) still checks control_identity
    is an exact match for "reply" (not "reply all"/"forward"/etc, and
    not merely a label containing the word) before ever trusting it,
    and bbox always goes through the same validate_grounding() path as
    every other click target."""

    outlook_visible: bool
    reply_visible: bool
    control_identity: str = ""
    control_type: str = ""
    bbox: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    more_content_below: bool = False
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class SendSearchResponse(BaseModel):
    """Structured, provider-neutral Send-control search result (Phase 7).
    Same shape/contract as ReplySearchResponse above — Vision reports the
    ONE control it believes is Send itself (if any); the playbook
    (deterministic Python) still checks control_identity is an exact
    match for "send" (not "schedule send"/"send later"/a body-text
    mention/etc, and not merely a label containing the word) before ever
    trusting it, and bbox always goes through the same
    validate_grounding() path as every other click target."""

    outlook_visible: bool
    send_visible: bool
    control_identity: str = ""
    control_type: str = ""
    bbox: Optional[list[float]] = None  # [y_min, x_min, y_max, x_max], 0-1000 normalized
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailSectionExtractionResponse(BaseModel):
    """Raw Vision response for the EXTRACTION half of one email-body
    section read (2026-09-04 latency fix). The original single-call
    design bundled extraction (10 fields) AND a holistic reply/sender
    assessment (5 more fields) into ONE call per section — a live run
    proved this 16-field mega-request was the single heaviest call in
    the whole pipeline and a repeatable cause of Gemini timeouts (60s,
    twice). Splitting it: this schema is requested on EVERY section
    (still light — pure "what do you see" extraction, no interpretive
    reasoning); EmailHolisticAssessmentResponse below is requested only
    ONCE, after the true end is confirmed — mirroring what
    app/outlook/read_email.py::_finalize_understanding() already only
    ever used anyway (only the LAST section's holistic fields were ever
    authoritative; every earlier section's were discarded).

    Vision is stateless per call — continuity is carried by the prompt-
    injected `already_read_tail`, not model memory. overlap_text is the
    exact verbatim substring Vision believes overlaps with that tail;
    the CALLER strips it from extracted_visible_content before storing
    (app/outlook/read_email.py) — never trusted as "already handled" by
    Vision itself, matching the box_2d self-consistency precedent.

    more_content_below/end_of_message_visible are two DELIBERATELY
    separate pieces of evidence (live long-email fix, 2026-09-02: Vision
    was reporting more_content_below=false prematurely — e.g. on seeing
    a Reply control, a signature, or believing "enough" context was
    read — so the reader stopped and Reply became reachable before the
    true end of a long email was ever seen). content_complete is NEVER
    read directly from Vision; app/outlook/read_email.py computes it
    deterministically as `(not more_content_below) AND
    end_of_message_visible` — requiring BOTH signals to agree is a
    strictly stronger bar than trusting either alone, and avoids a
    second, possibly-conflicting source of truth for completion."""

    extracted_visible_content: str = ""
    overlap_text: str = ""
    important_points: list[str] = Field(default_factory=list)
    requested_actions: list[str] = Field(default_factory=list)
    names_entities: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    commitments: list[str] = Field(default_factory=list)
    more_content_below: bool = False
    end_of_message_visible: bool = False
    no_new_content: bool = False
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailHolisticAssessmentResponse(BaseModel):
    """Raw Vision response for the HOLISTIC half of email understanding
    (2026-09-04 latency fix) — requested exactly ONCE per email, after
    the full text has been extracted (see EmailSectionExtractionResponse's
    docstring for why). Given the complete, already-extracted email text
    as prompt context rather than re-reading the screenshot from
    scratch — a much lighter classification task than bundling this
    into every section's extraction call."""

    reply_expectation: ReplyExpectationValue
    requires_user_decision: bool = False
    sender_intent: str = ""
    requested_action_summary: str = ""
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence {v} is outside the valid range [0.0, 1.0]")
        return v


class EmailSection(BaseModel):
    """One captured+understood section of a (possibly long) email body.
    Every section is retained in full — see docs/architecture/04 — so a
    date/commitment/name mentioned only in an early section is never
    lost when later sections are accumulated on top of it.

    reply_expectation/requires_user_decision are the authoritative
    reply-necessity classification (see ReplyExpectation). requires_reply
    is a DERIVED, backward-compatible legacy alias only — always computed
    by the caller (app/outlook/read_email.py) as
    `reply_expectation != SHOULD_NOT_REPLY`, never an independent source
    of truth Vision reports directly."""

    section_index: int
    extracted_visible_content: str
    important_points: list[str] = Field(default_factory=list)
    requested_actions: list[str] = Field(default_factory=list)
    names_entities: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    commitments: list[str] = Field(default_factory=list)
    overlap_with_previous: Optional[str] = None
    more_content_below: bool = False
    end_of_message_visible: bool = False
    no_new_content: bool = False  # true when a scroll produced zero new text vs. the accumulated tail
    reply_expectation: str = ReplyExpectation.OPTIONAL_REPLY
    requires_user_decision: bool = False
    requires_reply: bool = True  # DEPRECATED — derived legacy alias, see class docstring
    sender_intent: str = ""
    requested_action_summary: str = ""
    confidence: float = 0.0
