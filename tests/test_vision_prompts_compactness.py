"""Static content checks for the compact/strict-output prompt hardening
(2026-09-06 — TARGET_EMAIL_SEARCH/REPLY_SEARCH schema-invalid-response
follow-up). Checks for the PRESENCE of required concepts using loose
keyword matching deliberately, not exact wording — a future prompt
rewording that keeps the same intent should not need to touch these.

No provider/network calls anywhere in this file — pure static text
checks against the prompt files actually loaded by the live runtime.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "app" / "vision" / "prompts"


def _text(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").lower()


# --- TARGET_EMAIL_SEARCH (email_search_v1.txt) ---

def test_target_email_search_prompt_requires_json_only_no_markdown():
    text = _text("email_search_v1.txt")
    assert "json" in text
    assert "markdown" in text
    assert "no prose" in text


def test_target_email_search_prompt_forbids_omitting_candidates_for_brevity():
    text = _text("email_search_v1.txt")
    assert "never omit" in text
    assert "candidate" in text


def test_target_email_search_prompt_requires_compact_reason():
    text = _text("email_search_v1.txt")
    assert "10 words" in text
    assert "reason" in text


def test_target_email_search_prompt_keeps_tight_row_bbox_and_excludes_reading_pane():
    text = _text("email_search_v1.txt")
    assert "row_bbox" in text
    assert "tight" in text
    assert "reading pane" in text
    assert "empty" in text and "right" in text  # excludes large empty right-side region


# --- TARGET_EMAIL_ROW_BBOX_REFINE (email_row_bbox_refine_v1.txt) ---

def test_row_bbox_refine_prompt_keeps_same_confirmed_candidate():
    text = _text("email_row_bbox_refine_v1.txt")
    assert "already" in text
    assert "identified" in text or "confirmed" in text or "accepted" in text


def test_row_bbox_refine_prompt_keeps_horizontal_tightening_and_no_research():
    text = _text("email_row_bbox_refine_v1.txt")
    assert "right edge" in text
    assert "reading pane" in text
    assert "re-search" in text


def test_row_bbox_refine_prompt_requires_compact_json_only():
    text = _text("email_row_bbox_refine_v1.txt")
    assert "json" in text
    assert "markdown" in text
    assert "10 words" in text


# --- TARGET_EMAIL_ROW_IDENTITY_REFINE (email_row_identity_refine_v1.txt) ---
# 2026-09-06, same-day follow-up: fixes candidate-identity <-> row-bbox
# mismatch (Vision correctly accepted a sender+subject but attached a
# DIFFERENT row's bbox) — distinct concern from the geometry-only
# email_row_bbox_refine_v1.txt above.

def test_row_identity_refine_prompt_keeps_same_candidate_no_research():
    text = _text("email_row_identity_refine_v1.txt")
    assert "already" in text
    assert "identified" in text or "confirmed" in text or "accepted" in text
    assert "do not reconsider" in text or "do not switch" in text or "not in question" in text


def test_row_identity_refine_prompt_requires_sender_and_subject_binding():
    text = _text("email_row_identity_refine_v1.txt")
    assert "sender" in text
    assert "subject" in text
    assert "same sender" in text or "several rows" in text or "share" in text


def test_row_identity_refine_prompt_requires_grounded_fields_and_json_only():
    text = _text("email_row_identity_refine_v1.txt")
    assert "grounded_sender" in text
    assert "grounded_subject" in text
    assert "json" in text
    assert "markdown" in text


# --- REPLY_SEARCH (reply_search_v1.txt) ---

def test_reply_search_prompt_requires_json_only_no_markdown():
    text = _text("reply_search_v1.txt")
    assert "json" in text
    assert "markdown" in text
    assert "no prose" in text


def test_reply_search_prompt_distinguishes_reply_from_reply_all():
    text = _text("reply_search_v1.txt")
    assert "reply all" in text
    assert "reply" in text


def test_reply_search_prompt_is_visual_grounding_only():
    text = _text("reply_search_v1.txt")
    assert "grounding" in text
    assert "conversation history" in text or "email body" in text  # explicitly out of scope here


def test_reply_search_prompt_requires_compact_reason_and_bbox_convention():
    text = _text("reply_search_v1.txt")
    assert "10 words" in text
    assert "0-1000" in text or "normalized" in text


# --- SEND_SEARCH — stable single-object prompt, deliberately untouched ---

def test_send_search_prompt_unchanged_single_object_stable():
    """send_search_v1.txt is a single-object (not candidate-list) schema
    with no reported live schema-validation failures — per this task's
    own instruction not to rewrite an already-stable prompt, it keeps
    its existing (still JSON-only) output contract unchanged."""
    text = _text("send_search_v1.txt")
    assert "json" in text
    assert "candidates" not in text  # confirms it never became list-based


# --- Provider-neutral: no prompt file has a Claude-specific or
# Gemini-specific variant/branch ---

def test_no_claude_or_gemini_specific_prompt_variants_exist():
    import os

    filenames = set(os.listdir(PROMPTS_DIR))
    assert not any("claude" in f.lower() for f in filenames)
    assert not any("gemini" in f.lower() for f in filenames)
