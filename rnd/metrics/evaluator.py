"""RND-004 local evaluator — deterministic, keyword-based comparison of a
Vision model's structured prediction against RND-002's human-verified
ground truth. No AI is used anywhere in this module: every judgment here
is a fixed rule a human can read and verify, not a model's opinion of
another model's output.

Known limitation, stated up front: keyword matching is inherently
approximate. "reply" appearing in a predicted control string will match
whether the model meant "Reply" or "Reply All" — this is a real precision
gap, not hidden, and is called out again in docs/06_Vision_Screen_Understanding.md.
"""

from __future__ import annotations

from typing import Optional

STATE_VOCABULARY = {
    "dialog_or_overlay",
    "email_open",
    "inbox",
    "outlook_closed",
    "outlook_home",
    "reply_editor_open",
    "sending",
    "sent_or_post_send",
    "unknown",
}

ACTION_VOCABULARY = {
    "click_reply",
    "click_send",
    "focus_reply_editor",
    "open_outlook",
    "select_existing_email",
    "stop",
    "type_reply",
    "verify_sent",
    "wait",
    "other",
}

# Ordered so more specific checks run before more general ones (e.g. a
# "reply editor" description would also match generic "email" keywords).
_STATE_RULES: list[tuple[str, list[str]]] = [
    ("dialog_or_overlay", ["dialog", "popup", "pop-up", "notification", "overlay", "alert box"]),
    ("reply_editor_open", ["reply editor", "compose", "draft", "text entry", "text-entry", "editing a reply", "reply box", "reply pane"]),
    ("sending", ["sending"]),
    ("sent_or_post_send", ["sent", "message sent", "delivered"]),
    ("inbox", ["inbox", "message list", "mail list", "list of emails", "list of messages"]),
    ("email_open", ["email open", "email selected", "reading pane", "viewing an email", "message open", "email viewing", "opened email"]),
    ("outlook_home", ["home screen", "outlook home"]),
    ("outlook_closed", ["closed", "not open", "not running"]),
]

_CONTROL_KEYWORDS = {
    "email_row": ["email", "message"],
    "reply": ["reply"],
    "reply_all": ["reply all"],
    "reply_editor": ["editor", "compose", "text entry", "text-entry", "draft", "reply box"],
    "send": ["send"],
}


def normalize_state(predicted_state_text: str) -> str:
    """Maps a free-text predicted screen_state to the controlled vocabulary
    via ordered keyword rules. Returns "unknown" if nothing matches — never
    guesses.
    """
    text = predicted_state_text.lower()
    for bucket, keywords in _STATE_RULES:
        if any(kw in text for kw in keywords):
            return bucket
    return "unknown"


def normalize_action(recommended_action: str, target: str) -> str:
    """Maps free-text (recommended_action, target) to the controlled action
    vocabulary via keyword rules. Returns "other" if nothing matches.
    """
    combined = f"{recommended_action} {target}".lower()
    if "send" in combined:
        return "click_send"
    if "reply" in combined:
        # Deliberately narrow: "compose"/"focus" are too generic — models
        # routinely say "click Reply to compose a reply", which describes
        # the *purpose* of clicking Reply, not an already-open editor.
        # A real evaluator bug here (both words originally included)
        # flipped two genuinely-correct RND-004 answers (OUTLOOK-003,
        # OUTLOOK-008) into false failures — caught by manual raw-response
        # inspection, not by a test, which is itself a documented gap.
        if any(kw in combined for kw in ("editor", "text box", "text field", "text area", "cursor in", "already open", "compose pane", "reply pane")):
            return "focus_reply_editor"
        return "click_reply"
    if "select" in combined and ("email" in combined or "message" in combined):
        return "select_existing_email"
    if "open" in combined and "outlook" in combined:
        return "open_outlook"
    if "verify" in combined or "confirm" in combined and "sent" in combined:
        return "verify_sent"
    if "wait" in combined:
        return "wait"
    return "other"


def _control_key_for(expected_name: str) -> str:
    name = expected_name.lower()
    if "row" in name or name == "email_row":
        return "email_row"
    if "editor" in name:
        return "reply_editor"
    if "send" in name:
        return "send"
    # Check "reply all" before the generic "reply" substring check —
    # otherwise "Reply All" collapses to the same loose "reply" keyword
    # as plain "Reply", making hallucination detection unable to tell
    # them apart (a real bug: it flagged Reply-only responses as having
    # hallucinated "Reply All" just because they mentioned "Reply").
    if "reply all" in name:
        return "reply_all"
    if "reply" in name:
        return "reply"
    return name


def control_detected(expected_name: str, predicted_controls: list[str]) -> bool:
    """Keyword check: does any predicted control string plausibly refer to
    the expected control? Not exact-string matching — deliberately loose,
    with the precision trade-off documented at module level.
    """
    key = _control_key_for(expected_name)
    keywords = _CONTROL_KEYWORDS.get(key, [expected_name.lower()])
    joined = " ".join(predicted_controls).lower()
    return any(kw in joined for kw in keywords)


def evaluate_case(
    expected_application: str,
    expected_state: str,
    expected_action: Optional[str],
    expected_relevant_controls: Optional[list[str]],
    predicted_application: Optional[str],
    predicted_state: Optional[str],
    predicted_relevant_controls: Optional[list[str]],
    predicted_action: Optional[str],
    predicted_target: Optional[str],
    known_absent_controls: Optional[list[str]] = None,
) -> dict:
    """Runs every comparison for one case. Returns a dict of the derived
    fields (application_correct, normalized_predicted_state, state_correct,
    controls_correct, normalized_predicted_action, action_correct,
    failure_types) to be merged into an RND004CaseResult.

    None inputs (e.g. a failed/unschema-valid prediction) propagate to None
    outputs rather than being scored as wrong — a case with no prediction
    is "not evaluable", not "incorrect".
    """
    result: dict = {}
    failure_types: list[str] = []

    if predicted_application is None:
        result["application_correct"] = None
    else:
        app_correct = expected_application.lower() in predicted_application.lower() or \
            predicted_application.lower() in expected_application.lower()
        result["application_correct"] = app_correct
        if not app_correct:
            failure_types.append("APP_RECOGNITION_ERROR")

    if predicted_state is None:
        result["normalized_predicted_state"] = None
        result["state_correct"] = None
    else:
        normalized = normalize_state(predicted_state)
        result["normalized_predicted_state"] = normalized
        state_correct = normalized == expected_state
        result["state_correct"] = state_correct
        if not state_correct:
            failure_types.append("STATE_CLASSIFICATION_ERROR")

    if expected_relevant_controls is None:
        result["controls_correct"] = None
    elif predicted_relevant_controls is None:
        result["controls_correct"] = None
    else:
        all_detected = all(
            control_detected(name, predicted_relevant_controls) for name in expected_relevant_controls
        )
        result["controls_correct"] = all_detected
        if not all_detected:
            failure_types.append("CONTROL_MISSED")
        for absent in known_absent_controls or []:
            if control_detected(absent, predicted_relevant_controls):
                failure_types.append("CONTROL_HALLUCINATED")

    if predicted_action is None:
        result["normalized_predicted_action"] = None
        result["action_correct"] = None
    else:
        normalized_action = normalize_action(predicted_action, predicted_target or "")
        result["normalized_predicted_action"] = normalized_action
        if expected_action is None:
            result["action_correct"] = None
        else:
            action_correct = normalized_action == expected_action
            result["action_correct"] = action_correct
            if not action_correct:
                failure_types.append("WRONG_NEXT_ACTION")

    result["failure_types"] = failure_types
    return result
