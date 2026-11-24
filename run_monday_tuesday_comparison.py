"""Compare Monday vs paired next-day Tuesday EEG session summary metrics.

Usage:
    uv run --frozen python run_monday_tuesday_comparison.py
    uv run --frozen python run_monday_tuesday_comparison.py --summary-csv path1.csv --summary-csv path2.csv --timezone America/Los_Angeles
"""

import argparse
from datetime import datetime
from pathlib import Path

from phoofflineeeganalysis.analysis.weekday_pair_comparison import DEFAULT_OUTPUTS_DIR, DEFAULT_SUMMARIES_DIR, DEFAULT_TIMEZONE, MondayTuesdayComparison, find_newest_summary_csv


def main() -> None:
	parser = argparse.ArgumentParser(description="Paired Monday-vs-Tuesday comparison of EEG session summary metrics")
	parser.add_argument("--summary-csv", action="append", default=None, help="Session summary CSV path (repeatable). Default: newest *session_summaries.csv in the session_summaries folder")
	parser.add_argument("--summaries-dir", type=Path, default=DEFAULT_SUMMARIES_DIR, help="Folder scanned for the newest summary CSV when --summary-csv is not given")
	parser.add_argument("--outputs-dir", type=Path, default=DEFAULT_OUTPUTS_DIR, help="Root output folder (a date-prefixed subfolder is created)")
	parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="IANA timezone used for weekday assignment (default: America/Los_Angeles)")
	parser.add_argument("--no-pngs", action="store_true", help="Skip Matplotlib PNG exports")
	parser.add_argument("--no-html", action="store_true", help="Skip interactive HTML report export")
	args = parser.parse_args()

	if args.summary_csv:
		summary_csv_paths = [Path(p) for p in args.summary_csv]
	else:
		summary_csv_paths = [find_newest_summary_csv(args.summaries_dir)]
	for csv_path in summary_csv_paths:
		print(f"Summary CSV: {csv_path.as_posix()}")
	## END for csv_path in summary_csv_paths....

	output_dir = args.outputs_dir / f"{datetime.now().strftime('%Y-%m-%d')}_monday_tuesday_comparison"
	result = MondayTuesdayComparison.run_comparison(summary_csv_paths=summary_csv_paths, output_dir=output_dir, timezone_name=args.timezone, should_export_pngs=not args.no_pngs, should_export_html=not args.no_html)

	print(f"\n=== Monday vs Tuesday comparison complete ===")
	for label, csv_path in result["csv_paths"].items():
		print(f"{label}: {csv_path.as_posix()}")
	## END for label, csv_path in result["csv_paths"].items()....

	print(f"Paired-line PNGs: {len(result['png_paths'])} file(s) under {(output_dir / 'plots').as_posix()}")
	if result["html_path"] is not None:
		print(f"Interactive HTML report: {result['html_path'].as_posix()}")
	if not result["stats_df"].empty:
		print("\nPaired statistics (Tuesday − Monday):")
		print(result["stats_df"].to_string(index=False))


if __name__ == "__main__":
	main()
