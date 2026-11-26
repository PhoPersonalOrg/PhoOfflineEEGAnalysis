---
name: Calendar reveal copy
overview: Add right-click actions on the session calendar list to reveal the recording XDF in Explorer and copy its path to the clipboard, reusing the existing `xdf_path` UserRole payload.
todos:
  - id: context-menu
    content: Add QListWidget context menu (Open / Reveal / Copy) in SessionCalendarWindow
    status: completed
  - id: path-helper
    content: Extract _current_session_xdf_path and implement reveal + clipboard copy handlers
    status: completed
  - id: docs
    content: Document right-click actions in SESSION_CALENDAR.md
    status: completed
isProject: false
---

# Calendar: reveal + copy path

## Scope

Two actions on the day session list in [`SessionCalendarWindow`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\src\phoofflineeeganalysis\analysis\UI\CustomCalendarWidget.py):

1. **Reveal in Explorer** — select the XDF in Windows Explorer (`explorer /select,"<path>"`)
2. **Copy path** — put the absolute `xdf_path` string on the clipboard

UX default: **right-click context menu** on `session_list` (keeps the existing **Open in Timeline** button/double-click; avoids button clutter). Include **Open in Timeline** in the same menu for consistency.

## Implementation (single file)

Edit [`src/phoofflineeeganalysis/analysis/UI/CustomCalendarWidget.py`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\src\phoofflineeeganalysis\analysis\UI\CustomCalendarWidget.py) only (legacy re-export under `phoofflineeeganalysis.UI` needs no change).

### Context menu wiring

In `SessionCalendarWindow.__init__`:

- `self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)`
- Connect `customContextMenuRequested` → `_on_session_list_context_menu`

Menu builds with `QMenu` + actions:

- Open in Timeline → existing `_on_session_activated`
- Reveal in Explorer → new handler
- Copy Path → new handler

On right-click, select the item under the cursor (`itemAt`) before showing the menu so actions apply to that row.

### Shared path helper

Extract a small `_current_session_xdf_path(item=None) -> Optional[Path]` that reads `Qt.UserRole` the same way `_on_session_activated` already does (dict with `xdf_path`, or legacy bare string). Reuse it from timeline / reveal / copy handlers.

### Reveal

```python
subprocess.run(["explorer", f"/select,{path}"], check=False)
```

- Require path exists; otherwise `QMessageBox.warning`
- Windows-first (this machine / LabRecorder paths are Windows); no cross-platform branches unless already present nearby

### Copy path

```python
QApplication.clipboard().setText(str(path))
self.statusBar().showMessage(f"Copied: {path}", 3000)
```

Use absolute string form of `xdf_path` (already catalog-resolved). Warn if missing path.

### Docs (light)

One short bullet under the day-list section in [`SESSION_CALENDAR.md`](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\PhoOfflineEEGAnalysis\src\phoofflineeeganalysis\analysis\SESSION_CALENDAR.md): right-click → Reveal / Copy path.

## Out of scope

- Reveal for `fif_path` / `mat_path` exports
- New toolbar buttons
- OpenSpec change proposal (small UI affordance on existing calendar capability)
