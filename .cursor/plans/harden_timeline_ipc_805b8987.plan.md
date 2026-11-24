---
name: Harden timeline IPC
overview: Fix the offline timeline never starting its QLocalServer (wrong main-window attribute), then harden client/server so a live viewer is reused instead of cold-starting on every calendar click.
todos:
  - id: fix-main-window-lookup
    content: Use current_main_windows[-1] (+ sync _current_main_window on create) so OfflineTimelineCommandServer actually starts
    status: completed
  - id: harden-server
    content: Fix readyRead race, louder listen failure, early IPC forward+exit on --show-session
    status: completed
  - id: harden-client
    content: Spawn only on connect failure; retry; surface ipc_error in calendar UI
    status: completed
  - id: docs-verify
    content: Update SESSION_CALENDAR.md and verify listen/reuse/no-second-window manually
    status: completed
isProject: false
---

# Harden calendar → timeline IPC

## Root cause

[`main_offline_timeline.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\pyPhoTimeline\main_offline_timeline.py) wires the command server from `builder._current_main_window`, but that field is **never assigned** after init — windows only go into `current_main_windows`:

```231:238:c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\pyPhoTimeline\main_offline_timeline.py
    main_window = builder._current_main_window
    ...
    if main_window is not None and hasattr(main_window, "show_session"):
        command_server = OfflineTimelineCommandServer(...)
```

```128:128:c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\pyPhoTimeline\pypho_timeline\timeline_builder.py
        self._current_main_window = None
```

Result: no `"Offline command server listening..."` print, calendar `waitForConnected` always fails, every click spawns another `--show-session` process. Confirmed by [Explore IPC timeline](bc0ec5f2-237e-496a-8840-d78143df8a48) and the cold-start logs in the session-calendar terminal.

```mermaid
flowchart TD
  click[Calendar activate] --> ipc[try_send_show_session]
  ipc -->|connect fails forever| spawn[spawn --show-session]
  spawn --> build[Build window into current_main_windows]
  build --> badLookup["reads _current_main_window which is None"]
  badLookup --> noServer[No QLocalServer]
  noServer --> click
```

## Changes

### 1. Start the server from the real window (pyPhoTimeline)

In [`main_offline_timeline.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\pyPhoTimeline\main_offline_timeline.py):

- Resolve `main_window` as `builder.current_main_windows[-1]` when the list is non-empty (fallback: `_current_main_window` if ever set).
- Keep existing `show_session(...)` cold-start zoom and `OfflineTimelineCommandServer(...)` after that lookup.
- Also set `builder._current_main_window = main_window` in [`create_new_timeline_window_from_widget`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\pyPhoTimeline\pypho_timeline\timeline_builder.py) when appending, so other call sites that still use the singular field stay consistent.

### 2. Harden the server socket path

In [`offline_command_server.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\pyPhoTimeline\pypho_timeline\offline_command_server.py):

- After `nextPendingConnection`, if `bytesAvailable() > 0`, call `_read_command` immediately (same pattern as whisper’s single-instance server) so a fast client write is not missed when `readyRead` already fired.
- On listen failure, log loudly (and print) so a silent dead server is obvious.
- At the top of `main()` when `--show-session` is set: try `send_show_session_command` first; if a server accepts, **exit 0** without building a second window (stops cascade cold-starts).

### 3. Harden the calendar client

In [`timeline_handoff.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\src\phoofflineeeganalysis\analysis\timeline_handoff.py):

- Split outcomes: spawn **only** when connect fails; if connected but `ok: false`, return a failure status (e.g. `"ipc_error"`) and do **not** spawn.
- Retry connect 2–3 times with a short backoff before spawning (covers brief startup gaps once the server exists).
- Surface `"ipc_error"` in [`CustomCalendarWidget._on_session_activated`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\src\phoofflineeeganalysis\analysis\UI\CustomCalendarWidget.py) status/warning.

### 4. Docs

Update the handoff note in [`SESSION_CALENDAR.md`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\src\phoofflineeeganalysis\analysis\SESSION_CALENDAR.md): running viewer must print the listen line; second launches forward via IPC.

## Verification

1. Launch calendar, open one session (cold-start once) — expect `Offline command server listening as 'pypho_timeline_offline'`.
2. Activate another session (and the same one again) — status `Zoomed/loaded in running timeline`, **no** new `Cold-start show-session` process.
3. With timeline already open, run `main_offline_timeline.py --show-session <path>` manually — should hand off and exit without a second window.
4. Force `show_session` failure (bad path over IPC) — calendar shows error, does not spawn another timeline.

No OpenSpec change: this restores intended handoff behavior (bugfix).
