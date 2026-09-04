# POC Overview

## Purpose

Build a clean Windows POC proving that Vision AI can operate Microsoft Outlook through visible UI interaction — screen capture + mouse/keyboard only, no COM/Graph/SMTP/Office.js — using a controlled playbook, safety/fallback logic, and measurable metrics.

This is not a new R&D cycle. The R&D phase (RND-000 through RND-009D, see `docs/00_RND_INDEX.md`) already proved the core capability works: launch Outlook, find and open a target email, understand it, reply, generate and type a draft, and (in a separately-proven stage) send with verification. This POC converts those proven findings into one clean, demo-ready application.

## Architecture in one paragraph

Six layers, strictly separated: **Playbook** decides what step is next; **Vision** determines what is currently visible and where; **Automation** performs the one instructed mouse/keyboard action; **Safety** determines whether an action is allowed before it happens; **Fallback** handles bounded recovery (never unbounded retry); **Metrics** records every call, click, and outcome. See `01_ARCHITECTURE.md` for the full breakdown.

## Status

Implementation is proceeding phase-by-phase (see the project's implementation plan). Phase 1 (architecture refactor — reorganizing the proven RND-009A–D code into this final structure, no new behavior) is complete. Later phases add: Outlook maximize enforcement, dynamic email finding with bounded scrolling, long-email reading, dynamic Reply discovery, draft generation/typing/verification, single-Send integration, UI/result polish, and scenario tests.

No real live Outlook/Send run has been made against this refactored code yet — that requires a separate, explicit approval once implementation is complete.
