"""Pydantic schema for the RND-002 Outlook screenshot dataset manifest.

Defines the controlled state/action vocabulary and the structure every
dataset case (screenshot + human-verified ground truth) must follow. Used
by the annotation tool, the dataset validator, and the annotated-preview
generator so all three agree on one schema.

No AI is involved anywhere in this module.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Controlled vocabularies (RND-002 spec sections 13 and 14).
# Kept intentionally small — only states/actions that actually appear in
# this dataset should be used; this set is the ceiling, not a target.
STATE_VOCABULARY = {
    "outlook_closed",
    "outlook_home",
    "inbox",
    "email_open",
    "reply_editor_open",
    "sending",
    "sent_or_post_send",
    "dialog_or_overlay",
    "unknown",
}

ACTION_VOCABULARY = {
    "open_outlook",
    "select_existing_email",
    "click_reply",
    "focus_reply_editor",
    "type_reply",
    "click_send",
    "verify_sent",
    "wait",
    "stop",
}

TARGET_TYPES = {"button", "row", "text_area", "control", "other"}


class BoundingBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int

    @model_validator(mode="after")
    def check_ordering(self) -> "BoundingBox":
        if self.x1 >= self.x2:
            raise ValueError(f"x1 ({self.x1}) must be < x2 ({self.x2})")
        if self.y1 >= self.y2:
            raise ValueError(f"y1 ({self.y1}) must be < y2 ({self.y2})")
        return self

    def contains_point(self, x: int, y: int) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


class Target(BaseModel):
    name: str
    type: str
    bbox: BoundingBox

    @field_validator("type")
    @classmethod
    def type_in_vocab(cls, v: str) -> str:
        if v not in TARGET_TYPES:
            raise ValueError(f"target type '{v}' not in {sorted(TARGET_TYPES)}")
        return v


class ScreenInfo(BaseModel):
    width: int
    height: int
    windows_scaling_percent: int


class ExpectedInfo(BaseModel):
    application: str
    state: str
    recommended_action: Optional[str] = None

    @field_validator("state")
    @classmethod
    def state_in_vocab(cls, v: str) -> str:
        if v not in STATE_VOCABULARY:
            raise ValueError(f"state '{v}' not in {sorted(STATE_VOCABULARY)}")
        return v

    @field_validator("recommended_action")
    @classmethod
    def action_in_vocab(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ACTION_VOCABULARY:
            raise ValueError(f"recommended_action '{v}' not in {sorted(ACTION_VOCABULARY)}")
        return v


class EmailInfo(BaseModel):
    test_email_id: Optional[str] = None
    length_class: Optional[Literal["short", "long"]] = None
    subject: Optional[str] = None


class DatasetCase(BaseModel):
    test_id: str
    filename: Optional[str] = None
    status: Literal["planned", "captured", "skipped"] = "planned"
    skip_reason: Optional[str] = None
    screen: Optional[ScreenInfo] = None
    expected: Optional[ExpectedInfo] = None
    targets: list[Target] = Field(default_factory=list)
    email: Optional[EmailInfo] = None
    window_mode: Optional[str] = None
    notes: str = ""

    @model_validator(mode="after")
    def captured_requires_filename_and_screen(self) -> "DatasetCase":
        if self.status == "captured":
            if not self.filename:
                raise ValueError(f"{self.test_id}: status is 'captured' but filename is missing")
            if self.screen is None:
                raise ValueError(f"{self.test_id}: status is 'captured' but screen info is missing")
        if self.status == "skipped" and not self.skip_reason:
            raise ValueError(f"{self.test_id}: status is 'skipped' but skip_reason is missing")
        return self


class DatasetManifest(BaseModel):
    dataset_id: str
    created: str
    state_vocabulary: list[str] = Field(default_factory=lambda: sorted(STATE_VOCABULARY))
    action_vocabulary: list[str] = Field(default_factory=lambda: sorted(ACTION_VOCABULARY))
    cases: list[DatasetCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_test_ids(self) -> "DatasetManifest":
        ids = [c.test_id for c in self.cases]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate test_id(s) in manifest: {sorted(dupes)}")
        return self
