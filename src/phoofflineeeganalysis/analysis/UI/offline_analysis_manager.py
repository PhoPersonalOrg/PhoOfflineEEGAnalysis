"""PyQt Offline Analysis Manager application.

Hosts ``SessionCalendarPanel`` as its central view and integrates:
- Session discovery / catalog refresh (``SessionCatalog``)
- Video metadata ingest / duplicate report / consolidate (``VideoCatalog``)
- Toggleable video overlay on the session calendar
- Background EEG processing (``SpectogramPlottingHelper.process_XDFs_main``) with live progress,
  active-session labels, catalog status polling, and cooperative cancel
- Centralized path configuration (``AppPaths`` / ``config/offline_analysis_paths.yaml``)
"""

from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import pandas as pd
from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from phoofflineeeganalysis.analysis.app_paths import AppPaths, default_repo_root
from phoofflineeeganalysis.analysis.session_catalog import SessionCatalog, get_or_create_catalog
from phoofflineeeganalysis.analysis.UI.CustomCalendarWidget import CalendarDatasource, SessionCalendarPanel
from phoofflineeeganalysis.analysis.video_catalog import VideoCatalog, load_source_specs


class _CallableWorker(QObject):
    """Run a Python callable on a background ``QThread``."""

    progress = pyqtSignal(str)
    progress_detail = pyqtSignal(object)  # dict: message/current/total/active_sessions
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[Callable[..., None]], Any]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            def _emit(message: str, *, current: Optional[int] = None, total: Optional[int] = None, active_sessions: Optional[Sequence[str]] = None) -> None:
                self.progress.emit(str(message))
                detail: Dict[str, Any] = {"message": str(message)}
                if current is not None:
                    detail["current"] = int(current)
                if total is not None:
                    detail["total"] = int(total)
                if active_sessions is not None:
                    detail["active_sessions"] = [str(s) for s in active_sessions]
                self.progress_detail.emit(detail)

            result = self._fn(_emit)
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ProcessSessionsDialog(QDialog):
    """Configure parameters for ``SpectogramPlottingHelper.process_XDFs_main``."""

    def __init__(self, defaults: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Process EEG Sessions")
        self.resize(440, 220)

        form = QFormLayout(self)
        self.n_recent_spin = QSpinBox(self)
        self.n_recent_spin.setRange(0, 500)
        self.n_recent_spin.setSpecialValueText("All")
        self.n_recent_spin.setValue(int(defaults.get("n_most_recent_sessions_to_preprocess") or 5))
        form.addRow("Most recent sessions (0 = all):", self.n_recent_spin)

        self.filenames_edit = QLineEdit(self)
        self.filenames_edit.setPlaceholderText("Optional comma-separated XDF filenames")
        form.addRow("Included XDF filenames:", self.filenames_edit)

        self.write_fif_check = QCheckBox("Write final merged EEG FIF", self)
        self.write_fif_check.setChecked(bool(defaults.get("should_write_final_merged_eeg_fif", True)))
        form.addRow(self.write_fif_check)

        self.use_cache_check = QCheckBox("Use computation cache", self)
        self.use_cache_check.setChecked(bool(defaults.get("use_computation_cache", True)))
        form.addRow(self.use_cache_check)

        self.workers_spin = QSpinBox(self)
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(int(defaults.get("absolute_max_workers") or 2))
        form.addRow("Max workers:", self.workers_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def get_params(self) -> Dict[str, Any]:
        raw_names = self.filenames_edit.text().strip()
        included = [name.strip() for name in raw_names.split(",") if name.strip()] if raw_names else None
        n_recent_val = self.n_recent_spin.value()
        return {
            "n_most_recent_sessions_to_preprocess": None if n_recent_val == 0 else n_recent_val,
            "included_xdf_file_names": included,
            "should_write_final_merged_eeg_fif": self.write_fif_check.isChecked(),
            "use_computation_cache": self.use_cache_check.isChecked(),
            "absolute_max_workers": self.workers_spin.value(),
        }


class ConsolidateVideosDialog(QDialog):
    """Configure destination and dry-run flag for ``VideoCatalog.consolidate``."""

    def __init__(self, default_dest: Optional[Path] = None, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Consolidate Videos")
        self.resize(480, 150)

        form = QFormLayout(self)
        dest_row = QHBoxLayout()
        self.dest_edit = QLineEdit(str(default_dest) if default_dest is not None else "", self)
        browse_btn = QPushButton("Browse...", self)
        browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.dest_edit)
        dest_row.addWidget(browse_btn)
        form.addRow("Destination root:", dest_row)

        self.dry_run_check = QCheckBox("Dry run (report planned copies without writing files)", self)
        self.dry_run_check.setChecked(True)
        form.addRow(self.dry_run_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_dest(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Consolidation Destination", self.dest_edit.text())
        if chosen:
            self.dest_edit.setText(chosen)

    def get_params(self) -> Dict[str, Any]:
        dest_text = self.dest_edit.text().strip()
        return {
            "dest_root": Path(dest_text) if dest_text else None,
            "dry_run": self.dry_run_check.isChecked(),
        }


class EditPathsDialog(QDialog):
    """Edit ``AppPaths`` fields and save to ``config/offline_analysis_paths.local.yaml``."""

    def __init__(self, paths: AppPaths, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setWindowTitle("Edit Offline Analysis Paths")
        self.resize(620, 360)
        self._paths = paths

        layout = QVBoxLayout(self)
        note = QLabel(f"Changes save to local override: {paths.local_yaml_path.as_posix()}", self)
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        layout.addLayout(form)

        lab_box = QVBoxLayout()
        self.lab_dirs_edit = QPlainTextEdit(self)
        self.lab_dirs_edit.setPlaceholderText("One LabRecorder recording directory per line")
        self.lab_dirs_edit.setPlainText("\n".join(p.as_posix() for p in paths.lab_recorder_dirs))
        self.lab_dirs_edit.setFixedHeight(78)
        add_dir_btn = QPushButton("Add LabRecorder Folder...", self)
        add_dir_btn.clicked.connect(self._add_lab_dir)
        lab_box.addWidget(self.lab_dirs_edit)
        lab_box.addWidget(add_dir_btn)
        form.addRow("LabRecorder dirs:", lab_box)

        self.pickled_edit = self._make_path_row(form, "Pickled data path:", paths.pickled_data_path, is_dir=True)
        self.session_db_edit = self._make_path_row(form, "Session catalog SQLite (optional):", paths.session_catalog_path, is_dir=False)
        self.video_db_edit = self._make_path_row(form, "Video catalog SQLite:", paths.video_catalog_path, is_dir=False)
        self.video_yaml_edit = self._make_path_row(form, "Video sources YAML:", paths.video_sources_yaml, is_dir=False)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_path_row(self, form: QFormLayout, label: str, initial: Optional[Path], is_dir: bool) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(initial.as_posix() if initial is not None else "", self)
        btn = QPushButton("Browse...", self)
        btn.clicked.connect(lambda: self._browse_field(edit, is_dir=is_dir))
        row.addWidget(edit)
        row.addWidget(btn)
        form.addRow(label, row)
        return edit

    def _add_lab_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Add LabRecorder Directory")
        if chosen:
            current = self.lab_dirs_edit.toPlainText().strip()
            updated = f"{current}\n{chosen}" if current else chosen
            self.lab_dirs_edit.setPlainText(updated)

    def _browse_field(self, edit: QLineEdit, is_dir: bool) -> None:
        if is_dir:
            chosen = QFileDialog.getExistingDirectory(self, "Select Directory", edit.text())
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Select File", edit.text())
        if chosen:
            edit.setText(chosen)

    def build_updated_paths(self) -> AppPaths:
        lab_dirs = [Path(line.strip()) for line in self.lab_dirs_edit.toPlainText().splitlines() if line.strip()]
        pickled_text = self.pickled_edit.text().strip()
        session_db_text = self.session_db_edit.text().strip()
        video_db_text = self.video_db_edit.text().strip()
        video_yaml_text = self.video_yaml_edit.text().strip()

        return AppPaths(
            lab_recorder_dirs=lab_dirs,
            pickled_data_path=Path(pickled_text) if pickled_text else None,
            session_catalog_path=Path(session_db_text) if session_db_text else None,
            video_catalog_path=Path(video_db_text) if video_db_text else self._paths.video_catalog_path,
            video_sources_yaml=Path(video_yaml_text) if video_yaml_text else self._paths.video_sources_yaml,
            process_defaults=dict(self._paths.process_defaults),
            config_yaml_path=self._paths.config_yaml_path,
            local_yaml_path=self._paths.local_yaml_path,
        )


class OfflineAnalysisManagerWindow(QMainWindow):
    """Main PyQt manager shell hosting the session calendar, video catalog, and processing controls."""

    def __init__(self, paths: Optional[AppPaths] = None, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.paths: AppPaths = paths if paths is not None else AppPaths.load()
        self.session_catalog: Optional[SessionCatalog] = None
        self.video_catalog: Optional[VideoCatalog] = None

        self._active_thread: Optional[QThread] = None
        self._active_worker: Optional[_CallableWorker] = None
        self._cancel_event: Optional[threading.Event] = None
        self._last_job_cancelled: bool = False
        self._job_cancelable: bool = False
        self._job_actions: List[QAction] = []
        self._job_name: str = ""

        self.setWindowTitle("Pho Offline Analysis Manager")
        self.resize(1120, 700)

        self.calendar_panel = SessionCalendarPanel(parent=self, show_video_overlay=True)
        self.setCentralWidget(self.calendar_panel)
        self.calendar_panel.statusMessage.connect(self.statusBar().showMessage)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._on_poll_catalog)

        self._build_job_log_dock()
        self._build_menus_and_toolbar()
        self.reload_catalogs()

    def _build_job_log_dock(self) -> None:
        self.job_log_dock = QDockWidget("Job Log", self)
        self.job_log_dock.setObjectName("JobLogDock")
        container = QWidget(self.job_log_dock)
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)

        progress_row = QHBoxLayout()
        self.job_progress_bar = QProgressBar(container)
        self.job_progress_bar.setMinimum(0)
        self.job_progress_bar.setMaximum(0)  # indeterminate until total known
        self.job_progress_bar.setValue(0)
        self.job_progress_bar.setTextVisible(True)
        self.job_progress_bar.setFormat("Idle")
        progress_row.addWidget(self.job_progress_bar, stretch=1)

        self.btn_cancel_job = QPushButton("Cancel", container)
        self.btn_cancel_job.setEnabled(False)
        self.btn_cancel_job.setToolTip("Cancel the running job after in-flight session(s) finish")
        self.btn_cancel_job.clicked.connect(self.on_cancel_job)
        progress_row.addWidget(self.btn_cancel_job)
        vbox.addLayout(progress_row)

        self.job_progress_label = QLabel("No job running.", container)
        self.job_progress_label.setWordWrap(True)
        self.job_progress_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        vbox.addWidget(self.job_progress_label)

        self.job_log = QPlainTextEdit(container)
        self.job_log.setReadOnly(True)
        self.job_log.setMaximumBlockCount(1000)
        self.job_log.setMinimumHeight(110)
        vbox.addWidget(self.job_log)

        self.job_log_dock.setWidget(container)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.job_log_dock)

    def _build_menus_and_toolbar(self) -> None:
        menu_bar = self.menuBar()
        toolbar = QToolBar("Actions", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Sessions menu
        sessions_menu = menu_bar.addMenu("&Sessions")
        self.action_discover_sessions = QAction("Discover / Refresh Catalog", self)
        self.action_discover_sessions.setShortcut("Ctrl+R")
        self.action_discover_sessions.triggered.connect(self.on_discover_sessions)
        sessions_menu.addAction(self.action_discover_sessions)

        self.action_process_sessions = QAction("Process Recent...", self)
        self.action_process_sessions.triggered.connect(self.on_process_sessions)
        sessions_menu.addAction(self.action_process_sessions)

        self.action_cancel_job = QAction("Cancel Running Job", self)
        self.action_cancel_job.setShortcut("Esc")
        self.action_cancel_job.setEnabled(False)
        self.action_cancel_job.triggered.connect(self.on_cancel_job)
        sessions_menu.addAction(self.action_cancel_job)

        sessions_menu.addSeparator()
        self.action_reload_catalogs = QAction("Reload Catalogs From Disk", self)
        self.action_reload_catalogs.triggered.connect(self.reload_catalogs)
        sessions_menu.addAction(self.action_reload_catalogs)

        # Video menu
        video_menu = menu_bar.addMenu("&Video")
        self.action_video_ingest = QAction("Ingest Video Metadata", self)
        self.action_video_ingest.triggered.connect(self.on_video_ingest)
        video_menu.addAction(self.action_video_ingest)

        self.action_video_report = QAction("Duplicate Report", self)
        self.action_video_report.triggered.connect(self.on_video_report)
        video_menu.addAction(self.action_video_report)

        self.action_video_consolidate = QAction("Consolidate Videos...", self)
        self.action_video_consolidate.triggered.connect(self.on_video_consolidate)
        video_menu.addAction(self.action_video_consolidate)

        # View menu
        view_menu = menu_bar.addMenu("&View")
        self.action_toggle_video_overlay = QAction("Show Video Overlay", self)
        self.action_toggle_video_overlay.setCheckable(True)
        self.action_toggle_video_overlay.setChecked(True)
        self.action_toggle_video_overlay.toggled.connect(self.calendar_panel.set_show_video_overlay)
        view_menu.addAction(self.action_toggle_video_overlay)

        view_menu.addAction(self.job_log_dock.toggleViewAction())
        clear_log_action = QAction("Clear Job Log", self)
        clear_log_action.triggered.connect(self.job_log.clear)
        view_menu.addAction(clear_log_action)

        # Config menu
        config_menu = menu_bar.addMenu("&Config")
        self.action_edit_paths = QAction("Edit Paths...", self)
        self.action_edit_paths.triggered.connect(self.on_edit_paths)
        config_menu.addAction(self.action_edit_paths)

        # Toolbar
        toolbar.addAction(self.action_discover_sessions)
        toolbar.addAction(self.action_process_sessions)
        toolbar.addSeparator()
        toolbar.addAction(self.action_video_ingest)
        toolbar.addAction(self.action_video_report)
        toolbar.addAction(self.action_toggle_video_overlay)
        toolbar.addSeparator()
        toolbar.addAction(self.action_edit_paths)

        self._job_actions = [
            self.action_discover_sessions,
            self.action_process_sessions,
            self.action_video_ingest,
            self.action_video_report,
            self.action_video_consolidate,
            self.action_edit_paths,
        ]

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.job_log.appendPlainText(f"[{stamp}] {message}")

    def _set_job_running(self, running: bool, label: str = "") -> None:
        for action in self._job_actions:
            action.setEnabled(not running)
        ## END for action in self._job_actions....

        cancel_enabled = running and self._job_cancelable
        self.btn_cancel_job.setEnabled(cancel_enabled)
        self.action_cancel_job.setEnabled(cancel_enabled)
        if running:
            self._job_name = label or self._job_name
            self.job_progress_bar.setMaximum(0)
            self.job_progress_bar.setValue(0)
            self.job_progress_bar.setFormat("%p% — working...")
            self.job_progress_label.setText(f"Running: {label}..." if label else "Running...")
            self.statusBar().showMessage(f"Running: {label}...")
        else:
            self.job_progress_bar.setMaximum(1)
            self.job_progress_bar.setValue(1 if label else 0)
            self.job_progress_bar.setFormat("Idle" if not label else "Done")
            if label:
                self.job_progress_label.setText(label)
                self.statusBar().showMessage(label, 5000)
            else:
                self.job_progress_label.setText("No job running.")
            self._job_name = ""

    def _on_progress_detail(self, detail: object) -> None:
        if not isinstance(detail, dict):
            return
        current = detail.get("current")
        total = detail.get("total")
        active = detail.get("active_sessions") or []
        message = str(detail.get("message") or "")

        if isinstance(total, int) and total > 0 and isinstance(current, int):
            self.job_progress_bar.setMaximum(total)
            self.job_progress_bar.setValue(max(0, min(current, total)))
            self.job_progress_bar.setFormat(f"%v/%m sessions (%p%)")
        elif isinstance(total, int) and total == 0:
            self.job_progress_bar.setMaximum(1)
            self.job_progress_bar.setValue(1)
            self.job_progress_bar.setFormat("No sessions")

        active_txt = ", ".join(active) if active else "(none)"
        parts = []
        if self._job_name:
            parts.append(self._job_name)
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            parts.append(f"{current}/{total} done")
        parts.append(f"active: {active_txt}")
        if message and message not in parts:
            parts.append(message)
        self.job_progress_label.setText(" — ".join(parts))
        if active:
            self.statusBar().showMessage(f"{self._job_name}: {', '.join(active)}")

    def on_cancel_job(self) -> None:
        if self._cancel_event is None or self._active_thread is None:
            return
        if self._cancel_event.is_set():
            self.log("Cancel already requested; waiting for in-flight work to finish...")
            return
        self._cancel_event.set()
        self.btn_cancel_job.setEnabled(False)
        self.btn_cancel_job.setText("Cancelling...")
        self.log(f"Cancel requested for job: {self._job_name or '(unnamed)'}")
        self.job_progress_label.setText(f"Cancelling {self._job_name or 'job'}… in-flight session(s) will finish, pending sessions skipped.")
        self.statusBar().showMessage("Cancelling job...")

    def reload_catalogs(self) -> None:
        """Open configured ``SessionCatalog`` and ``VideoCatalog`` and refresh the calendar."""
        session_db = self.paths.resolved_session_catalog_path()
        self.session_catalog = get_or_create_catalog(db_path=session_db, pickled_data_path=self.paths.pickled_data_path)
        sessions_df = self.session_catalog.load_calendar_dataframe()

        video_df: Optional[pd.DataFrame] = None
        if self.paths.video_catalog_path is not None and self.paths.video_catalog_path.exists():
            self.video_catalog = VideoCatalog(self.paths.video_catalog_path)
            video_df = self.video_catalog.load_calendar_dataframe()
        else:
            self.video_catalog = VideoCatalog(self.paths.video_catalog_path)
            video_df = pd.DataFrame()

        datasource = CalendarDatasource(xdf_stream_infos_df=sessions_df, video_infos_df=video_df)
        self.calendar_panel.set_datasource(datasource)
        n_sessions = len(sessions_df)
        n_videos = len(video_df) if video_df is not None else 0
        self.statusBar().showMessage(f"Loaded {n_sessions} session(s), {n_videos} video(s)", 4000)

    def _on_poll_catalog(self) -> None:
        if self.session_catalog is None:
            return
        try:
            sessions_df = self.session_catalog.load_calendar_dataframe()
            video_df = self.calendar_panel.calendar.datasource.video_infos_df if self.calendar_panel.calendar.datasource is not None else None
            self.calendar_panel.set_datasource(CalendarDatasource(xdf_stream_infos_df=sessions_df, video_infos_df=video_df))
            if self._active_thread is not None and "status" in sessions_df.columns:
                computing = sessions_df.loc[sessions_df["status"].astype(str).str.lower() == "computing", "xdf_filename"]
                names = [str(n) for n in computing.tolist() if n]
                if names:
                    done_hint = self.job_progress_bar.value()
                    total_hint = self.job_progress_bar.maximum()
                    progress_bit = f"{done_hint}/{total_hint} done — " if total_hint > 0 else ""
                    self.job_progress_label.setText(f"{self._job_name}: {progress_bit}computing: {', '.join(names)}")
        except Exception:
            pass

    def _run_job(self, name: str, fn: Callable[[Callable[..., None]], Any], on_success: Callable[[Any], None], poll_catalog: bool = False, cancelable: bool = False) -> None:
        if self._active_thread is not None:
            QMessageBox.information(self, "Job In Progress", "Another background job is currently running. Wait for it to finish.")
            return

        self.log(f"Started: {name}")
        self._cancel_event = threading.Event()
        self._job_cancelable = bool(cancelable)
        self.btn_cancel_job.setText("Cancel")
        self._set_job_running(True, name)

        thread = QThread(self)
        worker = _CallableWorker(fn)
        worker.moveToThread(thread)

        def _cleanup() -> None:
            if poll_catalog:
                self._poll_timer.stop()
            thread.quit()
            thread.wait(5000)
            worker.deleteLater()
            thread.deleteLater()
            self._active_thread = None
            self._active_worker = None
            self._cancel_event = None
            self._job_cancelable = False
            self.btn_cancel_job.setText("Cancel")

        def _handle_finished(result: Any) -> None:
            self._last_job_cancelled = bool(cancelable and self._cancel_event is not None and self._cancel_event.is_set())
            _cleanup()
            try:
                on_success(result)
            finally:
                suffix = "Cancelled" if self._last_job_cancelled else "Finished"
                self._set_job_running(False, f"{suffix}: {name}")

        def _handle_failed(err_tb: str) -> None:
            self._last_job_cancelled = False
            _cleanup()
            self.log(f"FAILED ({name}):\n{err_tb}")
            self._set_job_running(False, f"Failed: {name}")
            QMessageBox.warning(self, f"{name} Failed", err_tb.splitlines()[-1] if err_tb else "Unknown error")

        worker.progress.connect(self.log)
        worker.progress_detail.connect(self._on_progress_detail)
        worker.finished.connect(_handle_finished)
        worker.failed.connect(_handle_failed)
        thread.started.connect(worker.run)

        self._active_thread = thread
        self._active_worker = worker
        if poll_catalog:
            self._poll_timer.start()
        thread.start()

    def on_discover_sessions(self) -> None:
        dirs = self.paths.existing_lab_recorder_dirs()
        if not dirs:
            QMessageBox.warning(self, "Discover Sessions", "No existing LabRecorder directories configured.\nUse Config -> Edit Paths... to add recording directories.")
            return
        catalog = self.session_catalog or get_or_create_catalog(db_path=self.paths.resolved_session_catalog_path(), pickled_data_path=self.paths.pickled_data_path)
        self.session_catalog = catalog

        def _work(progress: Callable[[str], None]) -> int:
            progress(f"Scanning {len(dirs)} directory(ies) for .xdf files...")
            return catalog.discover_and_upsert(dirs, recordings_extensions=[".xdf"])

        def _done(n_upserted: int) -> None:
            self.log(f"Discovered/upserted {n_upserted} session(s) into {catalog.db_path.as_posix()}")
            self.reload_catalogs()

        self._run_job("Discover Sessions", _work, _done)

    def on_process_sessions(self) -> None:
        dialog = ProcessSessionsDialog(self.paths.process_defaults, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        params = dialog.get_params()
        catalog = self.session_catalog or get_or_create_catalog(db_path=self.paths.resolved_session_catalog_path(), pickled_data_path=self.paths.pickled_data_path)
        self.session_catalog = catalog

        def _work(progress: Callable[..., None]) -> Any:
            repo_root = str(default_repo_root())
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            progress("Importing main_analyze_run.SpectogramPlottingHelper...")
            from main_analyze_run import SpectogramPlottingHelper

            progress(
                f"Running process_XDFs_main (n_recent={params['n_most_recent_sessions_to_preprocess']}, workers={params['absolute_max_workers']})...",
                current=0,
                total=0,
                active_sessions=[],
            )
            return SpectogramPlottingHelper.process_XDFs_main(
                n_most_recent_sessions_to_preprocess=params["n_most_recent_sessions_to_preprocess"],
                should_write_final_merged_eeg_fif=params["should_write_final_merged_eeg_fif"],
                included_xdf_file_names=params["included_xdf_file_names"],
                use_computation_cache=params["use_computation_cache"],
                absolute_max_workers=params["absolute_max_workers"],
                session_catalog=catalog,
                use_session_catalog=True,
                retain_in_memory=False,
                progress_callback=progress,
                cancel_event=self._cancel_event,
            )

        def _done(_result: Any) -> None:
            self.log("EEG processing cancelled." if self._last_job_cancelled else "EEG processing finished.")
            self.reload_catalogs()

        self._run_job("Process EEG Sessions", _work, _done, poll_catalog=True, cancelable=True)

    def on_video_ingest(self) -> None:
        sources_yaml = self.paths.video_sources_yaml
        if not sources_yaml.exists():
            QMessageBox.warning(self, "Video Ingest", f"Video sources YAML not found:\n{sources_yaml}")
            return
        catalog = VideoCatalog(self.paths.video_catalog_path)
        self.video_catalog = catalog

        def _work(progress: Callable[[str], None]) -> Dict[str, Any]:
            progress(f"Ingesting videos from {sources_yaml.as_posix()}...")
            counts = catalog.discover_and_upsert(sources_yaml)
            progress("Recomputing duplicate groups...")
            dedupe = catalog.recompute_duplicates()
            return {"counts": counts, "dedupe": dedupe}

        def _done(res: Dict[str, Any]) -> None:
            counts = res["counts"]
            dedupe = res["dedupe"]
            self.log(
                f"Video ingest: sources={counts['sources']} found={counts['found']} upserted={counts['upserted']} "
                f"skipped_incomplete={counts['skipped_incomplete']} missing_roots={counts['skipped_missing_roots']}; "
                f"dedupe groups={dedupe['groups']} duplicate_rows={dedupe['duplicate_rows']}"
            )
            self.reload_catalogs()

        self._run_job("Video Ingest", _work, _done)

    def on_video_report(self) -> None:
        catalog = VideoCatalog(self.paths.video_catalog_path)
        self.video_catalog = catalog

        def _work(_progress: Callable[[str], None]) -> Dict[str, Any]:
            df = catalog.load_videos_df()
            dup_df = catalog.load_duplicates_df()
            n_total = len(df)
            n_canon = int(df["is_canonical"].sum()) if n_total and "is_canonical" in df.columns else 0
            n_dup = int(df["is_duplicate"].sum()) if n_total and "is_duplicate" in df.columns else 0
            n_groups = int(dup_df["duplicate_group_id"].nunique()) if len(dup_df) else 0
            sample_lines: List[str] = []
            if len(dup_df):
                for gid, grp in dup_df.groupby("duplicate_group_id", sort=True):
                    sample_lines.append(f"  Group {gid} ({len(grp)} files):")
                    for row in grp.itertuples():
                        tag = "KEEP" if getattr(row, "is_canonical", False) else "DUP "
                        sample_lines.append(f"    [{tag}] {getattr(row, 'video_path', '')}")
                    ## END for row in grp.itertuples()....
                ## END for gid, grp in dup_df.groupby(...)....
            return {"total": n_total, "canonical": n_canon, "duplicates": n_dup, "groups": n_groups, "lines": sample_lines}

        def _done(summary: Dict[str, Any]) -> None:
            self.log(f"Video report: total={summary['total']} canonical={summary['canonical']} duplicates={summary['duplicates']} groups={summary['groups']}")
            for line in summary["lines"][:50]:
                self.log(line)
            ## END for line in summary["lines"][:50]....
            if len(summary["lines"]) > 50:
                self.log(f"  ... ({len(summary['lines']) - 50} more lines; use `pho-video-catalog report --csv ...` for full export)")

        self._run_job("Video Report", _work, _done)

    def on_video_consolidate(self) -> None:
        default_dest: Optional[Path] = None
        if self.paths.video_sources_yaml.exists():
            try:
                _specs, default_dest = load_source_specs(self.paths.video_sources_yaml)
            except Exception:
                default_dest = None
        dialog = ConsolidateVideosDialog(default_dest=default_dest, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        params = dialog.get_params()
        if params["dest_root"] is None:
            QMessageBox.warning(self, "Consolidate Videos", "Destination root cannot be empty.")
            return
        catalog = VideoCatalog(self.paths.video_catalog_path)
        self.video_catalog = catalog

        def _work(progress: Callable[[str], None]) -> Dict[str, Any]:
            progress(f"Consolidating canonical videos -> {params['dest_root']} (dry_run={params['dry_run']})...")
            return catalog.consolidate(dest_root=params["dest_root"], dry_run=params["dry_run"])

        def _done(result: Dict[str, Any]) -> None:
            self.log(
                f"Consolidate (dry_run={params['dry_run']}): planned={result['planned']} "
                f"copied={result['copied']} skipped={result['skipped']} failed={result['failed']}"
            )
            self.reload_catalogs()

        self._run_job("Consolidate Videos", _work, _done)

    def on_edit_paths(self) -> None:
        dialog = EditPathsDialog(self.paths, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.paths = dialog.build_updated_paths()
        saved = self.paths.save()
        self.log(f"Saved local path overrides to {saved.as_posix()}")
        self.reload_catalogs()

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._active_thread is not None and self._active_thread.isRunning():
            self._active_thread.quit()
            self._active_thread.wait(2000)
        super().closeEvent(event)


def launch_offline_analysis_manager(paths: Optional[AppPaths] = None, config_yaml: Optional[Union[str, Path]] = None, discover_on_start: bool = False, block: bool = True) -> OfflineAnalysisManagerWindow:
    """Open the Offline Analysis Manager PyQt window."""
    app = QApplication.instance()
    created_app = False
    if app is None:
        app = QApplication(sys.argv)
        created_app = True

    resolved_paths = paths if paths is not None else AppPaths.load(config_yaml=config_yaml)
    window = OfflineAnalysisManagerWindow(paths=resolved_paths)
    window.show()
    if discover_on_start:
        window.on_discover_sessions()
    if created_app and block:
        app.exec_()
    return window


if __name__ == "__main__":
    launch_offline_analysis_manager(block=True)
