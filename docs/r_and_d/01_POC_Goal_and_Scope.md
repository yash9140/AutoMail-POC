# 01 — POC Goal and Scope

## Objective

Define, precisely and durably, what this POC is trying to prove and what it
deliberately does not attempt — so every later experiment can be checked
against a fixed target instead of scope drifting over time.

## The Goal

Build and objectively evaluate a working **Outlook Vision AI Automation
POC** in which the system visually operates Microsoft Outlook — through
human-like screen observation, mouse movement, and keyboard input only — to:

1. Open Outlook by recognizing it on screen (no window-handle shortcuts).
2. Observe and understand the current Outlook screen state.
3. Identify and open an existing email.
4. Read and understand that email's content.
5. Locate and click Reply.
6. Detect the reply editor.
7. Generate a contextually appropriate reply from the email content.
8. Type the reply.
9. Locate and click Send.
10. Observe the result and verify the reply was actually sent.

This exact workflow (see the full numbered sequence in the project brief,
mirrored in `README.md`) is the final destination. Nothing beyond it is in
scope.

## The Real Research Question

This POC is not answering "can Python send an Outlook email" — that is
trivial via SMTP/Graph/COM. It is answering:

> **How accurately can Vision AI visually understand Outlook and perform a
> real email-reply workflow using human-like screen, mouse, and keyboard
> interaction?**

Concretely, we are measuring:

- Screen understanding (application/state recognition)
- Outlook recognition accuracy
- UI element identification
- Visual grounding (predicting usable pixel coordinates)
- Coordinate accuracy against manually verified ground truth
- Next-action reasoning
- Email-content understanding
- Reply generation quality
- Post-action state verification
- Retry/recovery capability
- End-to-end success rate
- Latency
- Number of Vision AI calls per workflow
- Approximate API cost per successful workflow

## Why No Outlook-Specific Integration

Using Microsoft Graph, Entra, the Outlook COM object model, Office.js, or
SMTP would let the automation succeed without the Vision AI actually seeing
or understanding anything — defeating the entire purpose of the R&D. The
system must operate Outlook the way a human would: by looking at the screen
and controlling the mouse/keyboard. If the Vision AI cannot find a button,
that is a recorded failure, not something to be patched around with a
non-visual shortcut.

Explicitly excluded for this reason:

```
Microsoft Graph
Microsoft Entra
Outlook API
Outlook COM (pywin32 Outlook.Application)
Office.js
SMTP
Outlook Add-in
Windows UI Automation / pywinauto for locating controls
Template matching that already knows the target
```

## Why No Hardcoded Coordinates or Shortcuts

During pure Vision AI testing, hardcoding Reply/Send coordinates, or using
any mechanism that hands the AI the answer, would hide the real accuracy
and failure modes of the model. If grounding fails, that failure is the
data point we need — not something to be engineered away.

## Scope Exclusions (project-wide)

This project will **not** include:

```
Chrome / browser automation
Notepad automation
ERP integration
Generic multi-application platform support
Production architecture
Cloud backend
Enterprise deployment
Large-scale user management
An MVP roadmap beyond this POC
```

Only Microsoft Outlook, on Windows, via screen/mouse/keyboard, is in scope.

## Technology Boundaries

**Use:** Python 3.11+, `mss`, `Pillow`, `pyautogui`, `python-dotenv`,
`pydantic`, `pytest`, and the SDK(s) of the Vision AI provider(s) actually
tested. Final UI: `PySide6` (added only after Vision AI experiments prove
the workflow — not built first).

**Do not use:** Django, Flask, FastAPI, Docker, Redis, Celery, PostgreSQL,
React, Electron, microservices.

## Conclusion

The POC has one destination and one evaluation question, both fixed above.
Every experiment from here on exists to answer the research question with
real, measured evidence — not to expand the system into a general-purpose
computer-use agent.

## Next Step

See [docs/r_and_d/02_RND_Methodology.md](02_RND_Methodology.md) for how experiments
are sequenced, executed, and measured, then proceed to
[docs/r_and_d/03_Screen_Capture.md](03_Screen_Capture.md) (RND-001).
