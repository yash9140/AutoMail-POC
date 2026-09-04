# Scrolling and Long-Email Reading

**Status: implemented (Phase 3 — message-list scrolling; Phase 4 — long-email accumulation).** This document was written in Phase 1 to fix the design ahead of implementation; both phases now match it, with the refinements noted inline below.

## Scrolling never determines where to click

Scrolling only decides **when to look again** — it never changes how a click target is validated. Every post-scroll grounding call reuses the exact same `validate_grounding()` path (bbox self-consistency, bounds check, sidebar-fraction rejection, confidence threshold) as a non-scrolled call. Scroll increment and the anchor point passed to `pyautogui.scroll()` are named policy constants, expressed as device-independent scroll units / region-relative fractions — never fixed pixels — so scrolling doesn't become "hardcoded percentages" replacing "hardcoded pixels."

## Message-list scrolling (Phase 3)

When the configured target (sender required, subject optional) isn't visible in the current message-list view: scroll the message list in a controlled increment → wait → fresh screenshot → re-ground → repeat up to `MAX_MESSAGE_LIST_SCROLL_ATTEMPTS`. After the limit, fail safe with `TARGET_EMAIL_NOT_FOUND`.

### Matching rules (sender required, subject optional)

- **A. Sender + subject both provided** → a row must match both to be a candidate.
- **B. Sender only** → any row from that sender is a candidate.
- Exactly one candidate → that's the target.
- Multiple candidates → pick the latest **only if** recency can be confidently read from the screenshot; otherwise stop safely with `MULTIPLE_TARGET_EMAILS_FOUND`. This decision is deterministic Python over Vision's structured per-row output — Vision never picks between ambiguous matches itself.

## Long-email accumulation (Phase 4, implemented)

Vision sees exactly one screenshot per call, always — no multi-image call is introduced. Continuity is carried by **prompt-injected prior context** (`already_read_tail`, the last `EMAIL_SECTION_TAIL_CHARS` (200) characters of everything accumulated so far, verbatim, never summarized), not model memory. Implemented in `app/outlook/read_email.py::EmailUnderstandingSteps.understand_email()`, using `app/vision/prompts/email_section_understanding_v1.txt`.

Raw per-call Vision response (`app/vision/models.py::EmailSectionRawResponse`) vs. the stored record (`EmailSection`) are kept distinct — the raw response carries a transient `overlap_text` the caller strips before storing, matching the box_2d self-consistency precedent (never trust a model's claim that it "already handled" something):

```python
class EmailSectionRawResponse(BaseModel):
    extracted_visible_content: str
    overlap_text: str                    # caller strips this, never stored as-is
    important_points: list[str]
    requested_actions: list[str]
    names_entities: list[str]
    dates: list[str]
    commitments: list[str]
    more_content_below: bool
    requires_reply: bool                 # holistic, considers already_read_tail + this section
    sender_intent: str
    requested_action_summary: str
    confidence: float

class EmailSection(BaseModel):           # what's actually stored, one per section, never discarded
    section_index: int
    extracted_visible_content: str       # overlap already stripped
    important_points: list[str]
    requested_actions: list[str]
    names_entities: list[str]
    dates: list[str]
    commitments: list[str]
    overlap_with_previous: str | None
    more_content_below: bool
    requires_reply: bool
    sender_intent: str
    requested_action_summary: str
    confidence: float
```

Loop:
1. Capture visible reading-pane section.
2. Call Vision (provider-retried, see `03_SAFETY_MODEL.md`) with the prior section's verbatim tail interpolated into the prompt.
3. Caller-side (Python, deterministic, `_strip_overlap()`) strips the reported overlap from the new section's content **only if it actually verifies** against the raw text — never trusted blindly.
4. Append the full `EmailSection` record to `self.result.email_sections` (a new `ReplyDraftResult` field — a subclass of the historical `RND009DResult`, `rnd/` untouched).
5. If `more_content_below` and under `MAX_EMAIL_BODY_SCROLL_ATTEMPTS` (5): scroll the email body (`app/automation/scrolling.py::scroll_email_body()`, reading-pane region only, never the message list) → wait → repeat.
6. Stop: `more_content_below=False` → `content_complete=True`; attempt limit reached while still `more_content_below=True` → `content_complete=False`.
7. Final understanding (`email_understanding_summary`/`_sender_intent`/`requires_reply`/`_requested_action`/`_important_points`/`_confidence` — the same fields `generate_draft()` already consumed pre-Phase-4) is built from ALL accumulated sections: `email_understanding_summary` is the straight concatenation of every section's (overlap-stripped) content; `important_points` is a deduped union across all sections; the holistic fields (`sender_intent`/`requires_reply`/`requested_action`/`confidence`) come from the LAST section's assessment — the only one made with full context of everything read.
8. If `content_complete=False`: `CONTENT_NOT_FULLY_READ` — **draft generation is never invoked**. Enforced twice: the caller chain never reaches `generate_draft()` after a failed `understand_email()`, and `generate_draft()` itself raises `RuntimeError` defensively if `content_complete is False`.

A short email is simply the `sections_seen == 1` case of this same loop — there is no separate code path, which is what makes the four scenario families (short/long × visible/needs-scroll) branches of one playbook rather than four scripts.
