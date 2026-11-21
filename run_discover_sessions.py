"""Lightweight XDF discovery into the session catalog (no EEG processing)."""

from pathlib import Path

from phoofflineeeganalysis.analysis.session_catalog import get_or_create_catalog

LAB_RECORDER_DIR = Path(r"E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001")
PICKLED_DATA_PATH = Path(r"E:/Dropbox (Personal)/Databases/AnalysisData/MNE_preprocessed/PICKLED_COLLECTION")

if __name__ == "__main__":
	assert LAB_RECORDER_DIR.exists(), f"'{LAB_RECORDER_DIR.as_posix()}' does not exist!"
	catalog = get_or_create_catalog(pickled_data_path=PICKLED_DATA_PATH)
	n = catalog.discover_and_upsert(LAB_RECORDER_DIR, recordings_extensions=[".xdf"])
	print(f"Discovered/upserted {n} sessions → {catalog.db_path.as_posix()}")
