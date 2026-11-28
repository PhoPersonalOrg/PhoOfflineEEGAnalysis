from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

lab = Path(r"E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001")
xdfs = list({p.resolve(): p for p in (list(lab.glob("*.xdf")) + list(lab.rglob("*.xdf")))}.values())
tz = ZoneInfo("America/Los_Angeles")
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
start, end = "2026-08-27", "2026-10-26"
window = rdf[(rdf["local_date"] >= start) & (rdf["local_date"] <= end)]
print(f"XDFs in {start}..{end}: {len(window)}")
print(window.groupby(window["local_dt"].dt.to_period("M")).size().to_string())

sum_path = Path(r"C:/AITEMP/PhoOfflineEEGAnalysisOutputs/session_summaries/2026-11-27_session_summaries.csv")
sdf = pd.read_csv(sum_path)
known = set(sdf["xdf_filename"].dropna().astype(str))
missing = window[~window["name"].isin(known)].copy()
print(f"already in summary: {int(window['name'].isin(known).sum())}")
print(f"missing from summary: {len(missing)}")
print("missing by date:")
print(missing.groupby("local_date").size().to_string())
print("missing files:")
for _, r in missing.iterrows():
	print(f"  {r['local_date']}  {r['size_mb']:>7} MB  {r['name']}")
## END for _, r in missing.iterrows()...

missing[["name", "local_date", "path", "size_mb"]].to_csv(Path("C:/AITEMP/PhoOfflineEEGAnalysisOutputs/session_summaries/_missing_preceding_two_months.csv"), index=False)
print("wrote missing list csv")
