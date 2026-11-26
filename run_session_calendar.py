"""Deprecated alias that launches the Offline Analysis Manager.

Use ``run_offline_analysis_manager.py`` or ``phoofflineeeganalysis`` directly.
"""

from __future__ import annotations

import sys

from run_offline_analysis_manager import main as _manager_main


if __name__ == "__main__":
    print("Note: run_session_calendar.py is deprecated; launching Offline Analysis Manager (run_offline_analysis_manager.py).", file=sys.stderr)
    raise SystemExit(_manager_main())
