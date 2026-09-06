"""app/safety/sender_identity.py unit tests (2026-09-06).

Live evidence: a run correctly opened and read the target email
(sender AND subject both semantically confirmed by Vision), yet
EMAIL_OPEN_VERIFICATION rejected it as sender_mismatch — target_sender
"Yash" was compared as an opaque string against the full display text
"Yash<yashdhanraj9140@gmail.com>". sender_matches() fixes this by
parsing both sides into (display_name, email_address) before comparing
— covers cases A-M from that task's own test matrix, plus the
explicitly-forbidden substring/fuzzy-match examples.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safety.sender_identity import parse_sender, sender_matches, sender_mode  # noqa: E402


# --- A-M: the task's own required matrix ---

def test_A_plain_name_exact_match():
    assert sender_matches("Yash", "Yash") is True


def test_B_name_only_target_matches_name_plus_email_detected():
    assert sender_matches("Yash", "Yash <yash@example.com>") is True


def test_C_name_only_target_matches_name_plus_email_no_space():
    assert sender_matches("Yash", "Yash<yash@example.com>") is True


def test_D_case_insensitive_display_name_match():
    assert sender_matches("YASH", "Yash <yash@example.com>") is True


def test_E_multi_word_display_name_with_collapsed_whitespace():
    assert sender_matches("Yash Dhanraj", "Yash   Dhanraj <yash@example.com>") is True


def test_F_similar_but_different_display_name_never_matches():
    assert sender_matches("Yash", "Yashwant <yashwant@example.com>") is False


def test_G_email_only_target_matches_same_email_regardless_of_display_name():
    assert sender_matches("yash@example.com", "Yash <yash@example.com>") is True


def test_H_email_only_target_rejects_different_email_despite_similar_name():
    assert sender_matches("yash@example.com", "Yash <different@example.com>") is False


def test_I_name_and_email_target_matches_identical_name_and_email():
    assert sender_matches("Yash <yash@example.com>", "Yash <yash@example.com>") is True


def test_J_name_and_email_target_rejects_different_email_same_name():
    assert sender_matches("Yash <yash@example.com>", "Yash <different@example.com>") is False


def test_K_empty_detected_never_matches():
    assert sender_matches("Yash", "") is False


def test_L_empty_target_never_matches_defensively():
    """target_sender is required/non-empty by construction everywhere
    upstream (FindOpenEmailSteps.__init__ raises ValueError on an empty
    target_sender) — sender_matches() itself still defines a safe,
    deterministic answer (never True) rather than leaving this
    undefined, in case it is ever called defensively."""
    assert sender_matches("", "Yash") is False
    assert sender_matches("", "") is False


def test_M_live_regression_no_space_before_angle_bracket():
    """The exact live-failure shape: target_sender='Yash', Vision-
    reported sender_detected='Yash<yashdhanraj9140@gmail.com>' (no space
    before '<') — must now match."""
    assert sender_matches("Yash", "Yash<yashdhanraj9140@gmail.com>") is True


# --- Explicitly forbidden substring/fuzzy examples from the task spec ---

def test_forbidden_substring_yash_does_not_match_yashwant():
    assert sender_matches("Yash", "Yashwant") is False


def test_forbidden_substring_john_does_not_match_johnson_with_email():
    assert sender_matches("John", "Johnson <johnson@example.com>") is False


def test_forbidden_email_target_never_accepted_on_display_name_alone():
    """Section 4: an email-address target must FAIL even if some
    display-name text appears similar, when the emails differ."""
    assert sender_matches("alice@example.com", "Alice Smith <bob@example.com>") is False


def test_genuine_identity_mismatch_still_rejected():
    assert sender_matches("Yash", "Rahul <rahul@example.com>") is False


# --- quoted display name / parseaddr edge cases ---

def test_quoted_display_name_is_handled():
    assert sender_matches("Yash", '"Yash" <yash@example.com>') is True


def test_case_insensitive_email_match():
    assert sender_matches("Yash@Example.com", "Yash <yash@example.com>") is True


# --- parse_sender() component-level tests ---

def test_parse_sender_plain_name_has_no_email():
    parsed = parse_sender("Yash")
    assert parsed.display_name == "yash"
    assert parsed.email_address == ""
    assert parsed.has_email is False


def test_parse_sender_name_and_email():
    parsed = parse_sender("Yash <yash@example.com>")
    assert parsed.display_name == "yash"
    assert parsed.email_address == "yash@example.com"
    assert parsed.has_email is True


def test_parse_sender_no_space_before_bracket():
    parsed = parse_sender("Yash<yashdhanraj9140@gmail.com>")
    assert parsed.display_name == "yash"
    assert parsed.email_address == "yashdhanraj9140@gmail.com"


def test_parse_sender_email_only_no_display_name():
    parsed = parse_sender("yash@example.com")
    assert parsed.display_name == ""
    assert parsed.email_address == "yash@example.com"
    assert parsed.has_email is True


def test_parse_sender_multi_word_plain_name_is_never_truncated():
    """The exact pitfall parse_sender() guards against: parseaddr()
    given a no-'@' string keeps only its first token and drops the
    rest (empirically parseaddr("Yash Dhanraj Extra") == ("", "Yash"))
    — parse_sender() must never inherit that truncation for a plain
    display name."""
    parsed = parse_sender("Yash Dhanraj Extra Words")
    assert parsed.display_name == "yash dhanraj extra words"
    assert parsed.email_address == ""


def test_parse_sender_empty_string():
    parsed = parse_sender("")
    assert parsed.display_name == ""
    assert parsed.email_address == ""
    assert parsed.has_email is False


def test_parse_sender_collapses_internal_whitespace():
    parsed = parse_sender("Yash   Dhanraj")
    assert parsed.display_name == "yash dhanraj"


# --- sender_mode() diagnostic classification ---

def test_sender_mode_name_only():
    assert sender_mode(parse_sender("Yash")) == "name"


def test_sender_mode_email_only():
    assert sender_mode(parse_sender("yash@example.com")) == "email"


def test_sender_mode_name_and_email():
    assert sender_mode(parse_sender("Yash <yash@example.com>")) == "name_email"
