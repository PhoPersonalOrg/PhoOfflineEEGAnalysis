"""Primary desktop shell (Manager Home) for Pho Offline Data Manager."""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

import pandas as pd
from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)

from phoofflineeeganalysis.offline_data_manager.app_paths import AppPaths
from phoofflineeeganalysis.offline_data_manager.media_modalities import ALL_MODALITY_KEYS
from phoofflineeeganalysis.offline_data_manager.service import OfflineDataManagerService
from phoofflineeeganalysis.offline_data_manager.ui.design_tokens import DesignTokens
from phoofflineeeganalysis.offline_data_manager.ui.dialogs import (
    ConsolidateMediaDialog,
    ProcessSessionsDialog,
    SourcesPathsDialog,
)
from phoofflineeeganalysis.offline_data_manager.ui.widgets import (
    DayDetailPanel,
    JobLogPanel,
    LeftRailPanel,
    ManagerToolBar,
    MonthCalendarPanel,
)


class _CallableWorker(QObject):
    """Execute a background service callable on a ``QThread`` with progress signals."""

    progress = pyqtSignal(str)
    progress_detail = pyqtSignal(object)
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


class PhoOfflineDataManagerWindow(QMainWindow):
    """Three-column Manager Home window backed exclusively by ``OfflineDataManagerService``."""

    def __init__(self, service: Optional[OfflineDataManagerService] = None, *, paths: Optional[AppPaths] = None, auto_select_latest_day: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.service: OfflineDataManagerService = service if service is not None else OfflineDataManagerService(paths=paths)
        self.setWindowTitle("Pho Offline Data Manager")
        self.resize(1440, 900)
        self.setStyleSheet(DesignTokens.app_stylesheet())

        self._active_thread: Optional[QThread] = None
        self._active_worker: Optional[_CallableWorker] = None
        self._cancel_event: Optional[threading.Event] = None
        self._last_job_cancelled: bool = False
        self._job_cancelable: bool = False
        self._job_name: str = ""
        self._menu_job_actions: List[QAction] = []
        self._active_modality_filters: Set[str] = set(ALL_MODALITY_KEYS)
        self._did_initial_day_jump: bool = not auto_select_latest_day

        self.toolbar = ManagerToolBar(parent=self)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)

        central = QWidget(self)
        central_lay = QHBoxLayout(central)
        central_lay.setContentsMargins(DesignTokens.Spacing.MD, DesignTokens.Spacing.MD, DesignTokens.Spacing.MD, DesignTokens.Spacing.MD)
        central_lay.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Horizontal, central)
        self.left_rail = LeftRailPanel(parent=self.main_splitter)
        self.main_splitter.addWidget(self.left_rail)

        self.center_splitter = QSplitter(Qt.Vertical, self.main_splitter)
        self.calendar_panel = MonthCalendarPanel(parent=self.center_splitter, show_media_overlay=True)
        self.job_log_panel = JobLogPanel(parent=self.center_splitter)
        self.center_splitter.addWidget(self.calendar_panel)
        self.center_splitter.addWidget(self.job_log_panel)
        self.center_splitter.setStretchFactor(0, 4)
        self.center_splitter.setStretchFactor(1, 1)
        self.center_splitter.setSizes([640, 180])
        self.main_splitter.addWidget(self.center_splitter)

        self.day_detail_panel = DayDetailPanel(parent=self.main_splitter)
        self.main_splitter.addWidget(self.day_detail_panel)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([250, 810, 360])

        central_lay.addWidget(self.main_splitter)
        self.setCentralWidget(central)

        self.job_progress_bar = self.job_log_panel.job_progress_bar
        self.job_progress_label = self.job_log_panel.job_progress_label
        self.btn_cancel_job = self.job_log_panel.btn_cancel_job
        self.job_log = self.job_log_panel.job_log

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._on_poll_catalog)

        self._build_menus()
        self._wire_signals()
        self.reload_catalogs()


    @property
    def paths(self) -> AppPaths:
        return self.service.paths


    @paths.setter
    def paths(self, value: AppPaths) -> None:
        self.service.paths = value


    def _build_menus(self) -> None:
        mb = self.menuBar()

        sessions_menu = mb.addMenu("&Sessions")
        self.action_discover_sessions = QAction("Discover / Refresh Catalog", self)
        self.action_discover_sessions.triggered.connect(self.on_discover_sessions)
        sessions_menu.addAction(self.action_discover_sessions)

        self.action_enrich_streams = QAction("Inspect XDF Stream Chips", self)
        self.action_enrich_streams.triggered.connect(self.on_enrich_streams)
        sessions_menu.addAction(self.action_enrich_streams)

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

        media_menu = mb.addMenu("&Media")
        self.action_ingest_media = QAction("Ingest Media Metadata", self)
        self.action_ingest_media.triggered.connect(self.on_ingest_media)
        media_menu.addAction(self.action_ingest_media)

        self.action_duplicate_report = QAction("Duplicate Report", self)
        self.action_duplicate_report.triggered.connect(self.on_duplicate_report)
        media_menu.addAction(self.action_duplicate_report)

        self.action_consolidate_media = QAction("Consolidate Media...", self)
        self.action_consolidate_media.triggered.connect(self.on_consolidate_media)
        media_menu.addAction(self.action_consolidate_media)

        view_menu = mb.addMenu("&View")
        view_menu.addAction(self.toolbar.action_toggle_media_overlay)

        toggle_log_action = QAction("Collapse / Expand Job Log", self)
        toggle_log_action.triggered.connect(self.job_log_panel.toggle_collapsed)
        view_menu.addAction(toggle_log_action)

        clear_log_action = QAction("Clear Job Log", self)
        clear_log_action.triggered.connect(self.job_log_panel.clear_log)
        view_menu.addAction(clear_log_action)

        config_menu = mb.addMenu("&Config")
        self.action_sources_paths = QAction("Sources && Paths...", self)
        self.action_sources_paths.triggered.connect(self.on_edit_paths)
        config_menu.addAction(self.action_sources_paths)

        self._menu_job_actions = [
            self.action_discover_sessions,
            self.action_enrich_streams,
            self.action_process_sessions,
            self.action_ingest_media,
            self.action_duplicate_report,
            self.action_consolidate_media,
            self.action_sources_paths,
        ]


    def _wire_signals(self) -> None:
        self.toolbar.discoverRequested.connect(self.on_discover_sessions)
        self.toolbar.processRequested.connect(self.on_process_sessions)
        self.toolbar.ingestMediaRequested.connect(self.on_ingest_media)
        self.toolbar.duplicateReportRequested.connect(self.on_duplicate_report)
        self.toolbar.consolidateMediaRequested.connect(self.on_consolidate_media)
        self.toolbar.mediaOverlayToggled.connect(self._on_media_overlay_toggled)
        self.toolbar.sourcesPathsRequested.connect(self.on_edit_paths)

        self.left_rail.modalityFiltersChanged.connect(self._on_modality_filters_changed)
        self.left_rail.discoverRequested.connect(self.on_discover_sessions)
        self.left_rail.ingestMediaRequested.connect(self.on_ingest_media)
        self.left_rail.duplicateReportRequested.connect(self.on_duplicate_report)
        self.left_rail.processRequested.connect(self.on_process_sessions)
        self.left_rail.consolidateMediaRequested.connect(self.on_consolidate_media)
        self.left_rail.sourcesPathsRequested.connect(self.on_edit_paths)

        self.calendar_panel.daySelected.connect(self._on_day_selected)
        self.day_detail_panel.openInTimelineRequested.connect(self._on_open_in_timeline)
        self.day_detail_panel.statusMessage.connect(self.statusBar().showMessage)
        self.job_log_panel.cancelRequested.connect(self.on_cancel_job)


    def log(self, message: str) -> None:
        self.job_log_panel.append_log(message)


    def _effective_filters(self) -> Set[str]:
        active = set(self._active_modality_filters)
        if not self.toolbar.action_toggle_media_overlay.isChecked():
            active = {"EEG"} if "EEG" in active else set()
        return active


    def reload_catalogs(self) -> None:
        """Reload SQLite catalogs via ``self.service`` and update stats, calendar, and Day Detail."""
        self.service.reload()
        stats = self.service.stats()
        self.left_rail.update_stats(stats)
        n_sess = int(stats.get("total_sessions") or 0)
        n_media = int(stats.get("total_media") or 0)
        summary = f"Loaded {n_sess:,} session(s), {n_media:,} media"
        self.toolbar.set_summary_text(summary)
        self.statusBar().showMessage(summary, 4000)

        self._refresh_calendar_and_day(allow_initial_jump=(not self._did_initial_day_jump))
        self._did_initial_day_jump = True


    def _refresh_calendar_and_day(self, *, allow_initial_jump: bool = False) -> None:
        eff_filters = self._effective_filters()
        payload = self.service.calendar_payload(modality_filters=eff_filters, build_datasource=True)
        datasource = payload.get("datasource")
        if datasource is not None:
            self.calendar_panel.set_datasource(datasource)

        selected_day = self.calendar_panel.selected_day_utc()
        records = self.service.day_records(selected_day, modality_filters=eff_filters)
        if allow_initial_jump and not records:
            latest = self._find_latest_catalog_day(payload.get("sessions_df"), payload.get("media_df"))
            if latest is not None:
                self.calendar_panel.set_selected_day(latest)
                selected_day = self.calendar_panel.selected_day_utc()
                records = self.service.day_records(selected_day, modality_filters=eff_filters)
        self.day_detail_panel.set_day_records(selected_day, records)


    @staticmethod
    def _find_latest_catalog_day(sessions_df: Optional[pd.DataFrame], media_df: Optional[pd.DataFrame]) -> Optional[datetime]:
        candidates: List[pd.Timestamp] = []
        for df in (sessions_df, media_df):
            if df is not None and len(df) > 0 and "recording_day_date" in df.columns:
                col = pd.to_datetime(df["recording_day_date"], utc=True, errors="coerce").dropna()
                if len(col) > 0:
                    candidates.append(col.max())
        ## END for df in (sessions_df, media_df)...

        if not candidates:
            return None
        return max(candidates).to_pydatetime()


    def _on_modality_filters_changed(self, active: object) -> None:
        if isinstance(active, set):
            self._active_modality_filters = set(active)
        self._refresh_calendar_and_day()


    def _on_media_overlay_toggled(self, enabled: bool) -> None:
        self.calendar_panel.set_show_media_overlay(enabled)
        self._refresh_calendar_and_day()


    def _on_day_selected(self, day_utc: Optional[datetime]) -> None:
        target = day_utc if day_utc is not None else self.calendar_panel.selected_day_utc()
        records = self.service.day_records(target, modality_filters=self._effective_filters())
        self.day_detail_panel.set_day_records(target, records)


    def _on_open_in_timeline(self, record: Dict[str, Any]) -> None:
        try:
            mode = self.service.open_in_timeline(record)
            fname = record.get("filename") or "session.xdf"
            status = str(record.get("status") or "").lower()
            cache_note = " (prefer cached spectrograms)" if status in ("computed", "exported") else ""
            if mode == "ipc":
                self.statusBar().showMessage(f"Zoomed/loaded in running timeline: {fname}{cache_note}", 5000)
            elif mode == "ipc_error":
                QMessageBox.warning(self, "Open in Timeline", f"Running timeline rejected show_session for:\n{fname}")
            else:
                self.statusBar().showMessage(f"Started offline timeline for: {fname}{cache_note}", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "Open in Timeline", str(exc))


    def _set_job_running(self, running: bool, label: str = "") -> None:
        self.toolbar.set_job_running(running)
        self.left_rail.set_job_running(running)
        for act in self._menu_job_actions:
            act.setEnabled(not running)
        ## END for act in self._menu_job_actions...

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
            self.job_progress_label.setText(label if label else "No job running.")
            if label:
                self.statusBar().showMessage(label, 5000)
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
            self.job_progress_bar.setFormat("%v/%m sessions (%p%)")
        elif isinstance(total, int) and total == 0:
            self.job_progress_bar.setMaximum(1)
            self.job_progress_bar.setValue(1)
            self.job_progress_bar.setFormat("No sessions")

        active_txt = ", ".join(active) if active else "(none)"
        parts: List[str] = []
        if self._job_name:
            parts.append(self._job_name)
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            parts.append(f"{current}/{total} done")
        parts.append(f"active: {active_txt}")
        if message and message not in parts:
            parts.append(message)
        self.job_progress_label.setText(" — ".join(parts))


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
        self.job_progress_label.setText(f"Cancelling {self._job_name or 'job'}… in-flight session(s) will finish, pending skipped.")
        self.statusBar().showMessage("Cancelling job...")


    def _on_poll_catalog(self) -> None:
        try:
            self._refresh_calendar_and_day()
            sessions_df = self.service.session_catalog.load_sessions_dataframe()
            if self._active_thread is not None and len(sessions_df) > 0 and "status" in sessions_df.columns:
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
            QMessageBox.warning(self, "Discover Sessions", "No existing LabRecorder directories configured.\nOpen Sources & Paths... to add recording directories.")
            return

        def _work(progress: Callable[..., None]) -> int:
            return self.service.discover_sessions(dirs=dirs, progress=progress)

        def _done(n_upserted: int) -> None:
            self.log(f"Discovered/upserted {n_upserted} session(s) into {self.service.session_catalog.db_path.as_posix()}")
            self.reload_catalogs()

        self._run_job("Discover Sessions", _work, _done)


    def on_enrich_streams(self) -> None:
        def _work(progress: Callable[..., None]) -> int:
            return self.service.enrich_session_streams(progress=progress)

        def _done(n_enriched: int) -> None:
            self.log(f"Enriched XDF stream chips for {n_enriched} session(s).")
            self.reload_catalogs()

        self._run_job("Inspect XDF Stream Chips", _work, _done)


    def on_process_sessions(self) -> None:
        sess_df = self.service.session_catalog.load_sessions_dataframe()
        fnames = sess_df["xdf_filename"].dropna().astype(str).tolist() if len(sess_df) > 0 and "xdf_filename" in sess_df.columns else []
        dialog = ProcessSessionsDialog(self.paths.process_defaults, available_filenames=fnames, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        params = dialog.get_params()

        def _work(progress: Callable[..., None]) -> Any:
            return self.service.process_sessions(progress=progress, cancel_event=self._cancel_event, **params)

        def _done(_result: Any) -> None:
            self.log("EEG processing cancelled." if self._last_job_cancelled else "EEG processing finished.")
            self.reload_catalogs()

        self._run_job("Process EEG Sessions", _work, _done, poll_catalog=True, cancelable=True)


    def on_ingest_media(self) -> None:
        sources_yaml = self.paths.media_sources_yaml
        if not sources_yaml.exists():
            QMessageBox.warning(self, "Ingest Media Metadata", f"Media sources YAML not found:\n{sources_yaml}")
            return

        def _work(progress: Callable[..., None]) -> Dict[str, Any]:
            return self.service.ingest_media(sources=sources_yaml, progress=progress)

        def _done(res: Dict[str, Any]) -> None:
            counts = res["counts"]
            dedupe = res["dedupe"]
            self.log(
                f"Media ingest: sources={counts['sources']} found={counts['found']} upserted={counts['upserted']} "
                f"(video={counts.get('upserted_video', 0)}, screenshots={counts.get('upserted_screenshot', 0)}, audio={counts.get('upserted_audio', 0)}) "
                f"skipped_incomplete={counts['skipped_incomplete']} missing_roots={counts['skipped_missing_roots']}; "
                f"dedupe groups={dedupe['groups']} duplicate_rows={dedupe['duplicate_rows']}"
            )
            self.reload_catalogs()

        self._run_job("Ingest Media Metadata", _work, _done)


    def on_duplicate_report(self) -> None:
        def _work(_progress: Callable[..., None]) -> Dict[str, Any]:
            return self.service.duplicate_report()

        def _done(summary: Dict[str, Any]) -> None:
            self.log(f"Duplicate report: total={summary['total']} canonical={summary['canonical']} duplicates={summary['duplicates']} groups={summary['groups']}")
            for line in summary["lines"][:50]:
                self.log(line)
            ## END for line in summary["lines"][:50]...

            if len(summary["lines"]) > 50:
                self.log(f"  ... ({len(summary['lines']) - 50} more lines; use `pho-video-catalog report --csv ...` for full export)")

        self._run_job("Duplicate Report", _work, _done)


    def on_consolidate_media(self) -> None:
        _specs, default_dest = self.service.load_media_source_specs()
        canon_count = int(self.service.stats().get("total_media") or 0)
        dialog = ConsolidateMediaDialog(default_dest=default_dest, canonical_count=canon_count, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        params = dialog.get_params()
        if params["dest_root"] is None:
            QMessageBox.warning(self, "Consolidate Media", "Destination root cannot be empty.")
            return

        def _work(progress: Callable[..., None]) -> Dict[str, int]:
            return self.service.consolidate_media(dest_root=params["dest_root"], dry_run=params["dry_run"], progress=progress)

        def _done(result: Dict[str, int]) -> None:
            self.log(
                f"Consolidate (dry_run={params['dry_run']}): planned={result['planned']} "
                f"copied={result['copied']} skipped={result['skipped']} failed={result['failed']}"
            )
            self.reload_catalogs()

        self._run_job("Consolidate Media", _work, _done)


    def on_edit_paths(self) -> None:
        dialog = SourcesPathsDialog(service=self.service, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return
        res = dialog.build_result()
        saved = self.service.save_paths(
            res["paths"],
            media_specs=res["media_specs"],
            consolidate_dest=res["consolidate_dest"],
        )
        self.log(f"Saved local path overrides to {saved.as_posix()} and media sources to {self.paths.media_sources_yaml.as_posix()}")
        self.reload_catalogs()


    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._active_thread is not None and self._active_thread.isRunning():
            self._active_thread.quit()
            self._active_thread.wait(2000)
        super().closeEvent(event)


def launch_offline_data_manager(paths: Optional[AppPaths] = None, config_yaml: Optional[Union[str, Path]] = None, discover_on_start: bool = False, block: bool = True) -> PhoOfflineDataManagerWindow:
    """Launch the Pho Offline Data Manager desktop application."""
    app = QApplication.instance()
    created_app = False
    if app is None:
        app = QApplication(sys.argv)
        created_app = True

    resolved_paths = paths if paths is not None else AppPaths.load(config_yaml=config_yaml)
    service = OfflineDataManagerService(paths=resolved_paths)
    window = PhoOfflineDataManagerWindow(service=service)
    window.show()
    if discover_on_start:
        window.on_discover_sessions()
    if created_app and block:
        app.exec_()
    return window


def create_preview_main_window() -> PhoOfflineDataManagerWindow:
    from phoofflineeeganalysis.offline_data_manager.ui.sample_data import SAMPLE_ANCHOR_DAY, build_populated_sample_service

    tmp_root = Path(tempfile.mkdtemp(prefix="pho_odm_main_preview_"))
    svc = build_populated_sample_service(tmp_root)
    win = PhoOfflineDataManagerWindow(service=svc, auto_select_latest_day=False)
    win.calendar_panel.set_selected_day(SAMPLE_ANCHOR_DAY)
    win._on_day_selected(SAMPLE_ANCHOR_DAY)
    win.log("Preview ready — Pho Offline Data Manager initialized with sample multimodal catalog.")
    return win


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preview or capture PhoOfflineDataManagerWindow.")
    parser.add_argument("--grab", type=Path, default=None, help="Optional output PNG path for offscreen screenshot capture")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    win = create_preview_main_window()
    win.resize(1440, 900)
    win.show()
    app.processEvents()
    if args.grab is not None:
        args.grab.parent.mkdir(parents=True, exist_ok=True)
        win.grab().save(str(args.grab))
        win.close()
        app.processEvents()
        raise SystemExit(0)
    raise SystemExit(app.exec_())
