"""Launch the PyQt session calendar backed by the discovery/compute catalog."""

from pathlib import Path

from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import launch_session_calendar

LAB_RECORDER_DIR = Path(r"E:/Dropbox (Personal)/Databases/UnparsedData/LabRecorderStudies/sub-P001")
PICKLED_DATA_PATH = Path(r"E:/Dropbox (Personal)/Databases/AnalysisData/MNE_preprocessed/PICKLED_COLLECTION")

if __name__ == "__main__":
	discovery_dirs = [LAB_RECORDER_DIR] if LAB_RECORDER_DIR.exists() else None
	launch_session_calendar(discovery_dirs=discovery_dirs, pickled_data_path=PICKLED_DATA_PATH)
