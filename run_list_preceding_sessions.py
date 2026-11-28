"""List XDFs in a local-date window and report which are missing from session_summaries.

Usage:
    uv run --frozen python run_list_preceding_sessions.py
    uv run --frozen python run_list_preceding_sessions.py --start 2026-08-27 --end 2026-10-26
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from main_analyze_run import lab_recorder_output_path, outputs_root_folder


DEFAULT_START = "2026-08-27"
DEFAULT_END = "2026-10-26"
DEFAULT_TIMEZONE = "America/Los_Angeles"


def main() -> None:
	parser = argparse.ArgumentParser(description="List LabRecorder XDFs in a date window vs session_summaries coverage")
	parser.add_argument("--start", default=DEFAULT_START, help="Inclusive local start date YYYY-MM-DD")
	parser.add_argument("--end", default=DEFAULT_END, help="Inclusive local end date YYYY-MM-DD")
	parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="IANA timezone for weekday/date assignment")
	parser.add_argument("--lab-dir", type=Path, default=lab_recorder_output_path, help="LabRecorder XDF root")
	parser.add_argument("--summaries-dir", type=Path, default=outputs_root_folder / "session_summaries", help="Folder with *session_summaries.csv")
	parser.add_argument("--summary-csv", type=Path, default=None, help="Specific summary CSV (default: newest non-empty)")
	args = parser.parse_args()

	lab = Path(args.lab_dir)
	xdfs = list({p.resolve(): p for p in (list(lab.glob("*.xdf")) + list(lab.rglob("*.xdf")))}.values())
	tz = ZoneInfo(args.timezone)
	pat = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})")
	rows = []
	for p in xdfs:
		m = pat.search(p.name)
		if m:
			dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}").replace(tzinfo=timezone.utc).astimezone(tz)
		else:
			dt = datetime.fromtimestamp(p.stat().st_mtime, tz=tz)
		rows.append({"name": p.name, "local_date": dt.strftime("%Y-%m-%d"), "local_dt": dt, "path": str(p), "size_mb": round(p.stat().st_size / 1e6, 2)})
	## END for p in xdfs...

	rdf = pd.DataFrame(rows).sort_values("local_dt")
	window = rdf[(rdf["local_date"] >= args.start) & (rdf["local_date"] <= args.end)]
	print(f"XDFs in {args.start}..{args.end}: {len(window)}")
	if not window.empty:
		print(window.groupby(window["local_dt"].dt.tz_localize(None).dt.to_period("M")).size().to_string())

	summaries_dir = Path(args.summaries_dir)
	if args.summary_csv is not None:
		sum_path = Path(args.summary_csv)
	else:
		candidates = sorted(summaries_dir.glob("*session_summaries.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
		candidates = [p for p in candidates if not p.name.endswith("_empty.csv") and not p.name.startswith("_")]
		if not candidates:
			raise SystemExit(f"No *session_summaries.csv under {summaries_dir}")
		sum_path = candidates[0]
	print(f"Summary CSV: {sum_path.as_posix()}")
	sdf = pd.read_csv(sum_path)
	known = set(sdf["xdf_filename"].dropna().astype(str)) if "xdf_filename" in sdf.columns else set()
	missing = window[~window["name"].isin(known)].copy()
	print(f"already in summary: {int(window['name'].isin(known).sum())}")
	print(f"missing from summary: {len(missing)}")
	print("missing by date:")
	print(missing.groupby("local_date").size().to_string() if not missing.empty else "(none)")
	print("missing files:")
	for _, r in missing.iterrows():
		print(f"  {r['local_date']}  {r['size_mb']:>7} MB  {r['name']}")
	## END for _, r in missing.iterrows()...

	out_csv = summaries_dir / "_missing_preceding_two_months.csv"
	summaries_dir.mkdir(parents=True, exist_ok=True)
	missing[["name", "local_date", "path", "size_mb"]].to_csv(out_csv, index=False)
	print(f"wrote {out_csv.as_posix()}")


if __name__ == "__main__":
	main()
