"""RND-009B failure classification vocabulary.

Kept as one explicit, named set — never collapsed into a generic FAIL —
so a result's failure_reason always says specifically what went wrong.
"""

from __future__ import annotations


class LaunchFailureReason:
    WINDOWS_SEARCH_NOT_VISIBLE = "WINDOWS_SEARCH_NOT_VISIBLE"
    OUTLOOK_RESULT_NOT_FOUND = "OUTLOOK_RESULT_NOT_FOUND"
    GROUNDING_INVALID = "GROUNDING_INVALID"
    GROUNDING_OUT_OF_BOUNDS = "GROUNDING_OUT_OF_BOUNDS"
    # The screenshot mss captured and pyautogui's own reported screen size
    # disagree — any coordinate computed from the screenshot and then fed
    # to pyautogui would be systematically wrong regardless of how correct
    # the Vision grounding itself is. Detected via
    # app.automation.screen_capture.get_environment_info() before the
    # click point is ever trusted. See app/outlook/launch.py.
    COORDINATE_SPACE_MISMATCH = "COORDINATE_SPACE_MISMATCH"
    # A search result IS visible and IS labeled Outlook, but its
    # semantic target_type is not "desktop_app" (e.g. a web result,
    # Settings, Help, or a Store suggestion) — distinct from
    # OUTLOOK_RESULT_NOT_FOUND (nothing matching at all) so logs/tests
    # can tell "found the wrong thing" apart from "found nothing".
    OUTLOOK_RESULT_WRONG_TYPE = "OUTLOOK_RESULT_WRONG_TYPE"
    # visible_sublabel was reported but doesn't look like an app
    # indicator (e.g. it reads like a web/settings/help hint instead) —
    # a secondary identity signal disagreeing with target_type/visible_label.
    OUTLOOK_RESULT_SUBLABEL_MISMATCH = "OUTLOOK_RESULT_SUBLABEL_MISMATCH"
    # The first-pass bbox was self-reported as not tightly scoped to the
    # clickable row (bbox_tightly_scoped=False), and the bounded,
    # same-screenshot second-pass refine call still couldn't produce a
    # confident, valid tight bbox. Never falls back to the known-loose
    # first-pass bbox — that would defeat the whole point of the check.
    OUTLOOK_RESULT_BBOX_NOT_TIGHT = "OUTLOOK_RESULT_BBOX_NOT_TIGHT"
    # The pyautogui move/click call itself raised (e.g. a FAILSAFE trip
    # or an OS-level input error) — never allowed to propagate as an
    # uncaught exception out of the step layer; always converted to this
    # explicit, safe failure. See app/outlook/launch.py::click_outlook_result().
    PHYSICAL_ACTION_FAILED = "PHYSICAL_ACTION_FAILED"
    HUMAN_REJECTED_TARGET = "HUMAN_REJECTED_TARGET"
    SEARCH_STATE_LOST_BEFORE_CLICK = "SEARCH_STATE_LOST_BEFORE_CLICK"
    OUTLOOK_LAUNCH_TIMEOUT = "OUTLOOK_LAUNCH_TIMEOUT"
    OUTLOOK_FOREGROUND_VERIFICATION_FAILED = "OUTLOOK_FOREGROUND_VERIFICATION_FAILED"
    VISION_VERIFICATION_FAILED = "VISION_VERIFICATION_FAILED"
    OUTLOOK_READY_TIMEOUT = "OUTLOOK_READY_TIMEOUT"

    # RND-009C — find + open email
    TARGET_EMAIL_NOT_VISIBLE = "TARGET_EMAIL_NOT_VISIBLE"
    TARGET_EMAIL_MISMATCH = "TARGET_EMAIL_MISMATCH"
    EMAIL_GROUNDING_INVALID = "EMAIL_GROUNDING_INVALID"
    EMAIL_GROUNDING_OUT_OF_BOUNDS = "EMAIL_GROUNDING_OUT_OF_BOUNDS"
    OUTLOOK_FOREGROUND_LOST = "OUTLOOK_FOREGROUND_LOST"
    EMAIL_OPEN_VERIFICATION_FAILED = "EMAIL_OPEN_VERIFICATION_FAILED"
    WRONG_EMAIL_OPENED = "WRONG_EMAIL_OPENED"
    EMAIL_GROUNDING_SIDEBAR_REJECTED = "EMAIL_GROUNDING_SIDEBAR_REJECTED"
    EMAIL_GROUNDING_POINT_OUTSIDE_BBOX = "EMAIL_GROUNDING_POINT_OUTSIDE_BBOX"

    # RND-009D — reply + draft
    EMAIL_UNDERSTANDING_FAILED = "EMAIL_UNDERSTANDING_FAILED"
    REPLY_NOT_REQUIRED = "REPLY_NOT_REQUIRED"  # declared, reserved — superseded by REPLY_NOT_APPROPRIATE below
    # Reply-expectation policy (see app.vision.models.ReplyExpectation):
    # the only genuine safe-stop is SHOULD_NOT_REPLY (a normal Reply
    # would be inappropriate/unsafe/non-conversational) — MUST_REPLY and
    # OPTIONAL_REPLY both continue in this POC, since it is inherently a
    # user-initiated targeted reply run.
    REPLY_NOT_APPROPRIATE = "REPLY_NOT_APPROPRIATE"
    REPLY_TARGET_NOT_FOUND = "REPLY_TARGET_NOT_FOUND"
    REPLY_GROUNDING_INVALID = "REPLY_GROUNDING_INVALID"
    REPLY_EDITOR_NOT_OPEN = "REPLY_EDITOR_NOT_OPEN"
    DRAFT_GENERATION_FAILED = "DRAFT_GENERATION_FAILED"
    DRAFT_VALIDATION_FAILED = "DRAFT_VALIDATION_FAILED"
    FOREGROUND_CHANGED_DURING_TYPING = "FOREGROUND_CHANGED_DURING_TYPING"
    TEXT_ENTRY_FAILED = "TEXT_ENTRY_FAILED"
    DRAFT_VERIFICATION_FAILED = "DRAFT_VERIFICATION_FAILED"
    TECHNICAL_PROVIDER_ERROR = "TECHNICAL_PROVIDER_ERROR"
    USER_ABORTED = "USER_ABORTED"

    # Final POC — target matching (Phase 3: sender required / subject
    # optional). Vision never arbitrarily picks between ambiguous
    # matches — this is the deterministic-code safe-stop when recency
    # cannot be confidently determined.
    MULTIPLE_TARGET_EMAILS_FOUND = "MULTIPLE_TARGET_EMAILS_FOUND"

    # Phase 3 — bounded message-list scrolling exhausted without finding
    # a matching candidate. Distinct from TARGET_EMAIL_NOT_VISIBLE (which
    # meant "not in this one view, not scrolling" during RND-009C/D) —
    # this means "searched, including bounded scrolling, still nothing."
    TARGET_EMAIL_NOT_FOUND = "TARGET_EMAIL_NOT_FOUND"

    # Final POC — declared now, wired in by the phase noted, so the
    # generic failure vocabulary is visible from Phase 1 onward even
    # though these specific reasons aren't reachable until their phase
    # lands.
    CONTENT_NOT_FULLY_READ = "CONTENT_NOT_FULLY_READ"                # Phase 4
    REPLY_NOT_FOUND = "REPLY_NOT_FOUND"                              # Phase 5
    SEND_TARGET_INVALID = "SEND_TARGET_INVALID"                      # Phase 7 (declared, reserved)

    # Phase 7 — Send once + verify sent
    SEND_NOT_APPROVED = "SEND_NOT_APPROVED"
    SEND_PRECONDITION_FAILED = "SEND_PRECONDITION_FAILED"
    SEND_GROUNDING_INVALID = "SEND_GROUNDING_INVALID"
    SEND_VERIFICATION_FAILED = "SEND_VERIFICATION_FAILED"
    # Send was physically clicked exactly once, but post-send visual
    # confirmation was inconclusive after every bounded observation
    # attempt. This is NEVER "not sent" — it is genuine uncertainty, and
    # it NEVER authorizes a resend. See app/outlook/send.py.
    SEND_VERIFICATION_UNCERTAIN = "SEND_VERIFICATION_UNCERTAIN"


# Provider-technical failures (HTTP/network/timeout/auth/rate-limit) —
# these route to the PROVIDER_ERROR terminal state, never FAILED_SAFE,
# so a technical outage is never conflated with a semantic/grounding/
# verification/content failure. See app/fallback/classifications.py.
PROVIDER_ERROR_REASONS = {
    LaunchFailureReason.TECHNICAL_PROVIDER_ERROR,
}

ALL_FAILURE_REASONS = {
    v for k, v in vars(LaunchFailureReason).items() if not k.startswith("_")
}
