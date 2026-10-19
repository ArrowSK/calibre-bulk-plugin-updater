# GPL-3.0-or-later
"""Calibre GUI: select -> download + deterministic audit -> sequentially install.

Uses calibre's own public plugin catalogue updater API and add_plugin.
Never executes downloaded plugin code during preflight.
"""
import datetime
import hashlib
import os
import shutil
import tempfile
import traceback

from qt.core import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QThread, QTimer, QVBoxLayout, Qt,
)

from calibre.constants import __appname__, __version__, config_dir
from calibre.customize.ui import add_plugin, config
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.dialogs.plugin_updater import get_plugin_updates_available
from calibre.utils.https import get_https_resource_securely
from calibre_plugins.bulk_plugin_updater.risk import (
    format_version, has_blockers, inspect_archive, metadata_issues, severity, should_retain_backups,
)


class DownloadThread(QThread):
    """Only network I/O off the GUI thread; actual calibre installation is on GUI thread."""

    def __init__(self, url, expected_name, expected_version, parent=None):
        super().__init__(parent)
        self.url = url
        self.expected_name = expected_name
        self.expected_version = expected_version
        self.data = None
        self.audit = None
        self.problem = None

    def run(self):
        try:
            self.data = get_https_resource_securely(
                self.url, headers={'User-Agent': f'{__appname__} {__version__}'})
            if not self.data:
                self.problem = 'The server returned an empty file.'
            else:
                # ZIP decompression/CRC inspection can be expensive; keep it off
                # the Qt GUI thread while never importing untrusted plugin code.
                self.audit = inspect_archive(self.data, self.expected_name, self.expected_version)
        except Exception:
            self.problem = traceback.format_exc()


class BulkUpdaterDialog(QDialog):
    def __init__(self, gui):
        super().__init__(gui)
        self.gui = gui
        self.setWindowTitle('Bulk Plugin Updater — review before installing')
        self.resize(1050, 740)
        self.items = []
        self.queue = []
        self.record = {}
        self.phase = 'loading'
        self.index = 0
        self.updates_installed = 0
        self.stop_requested = False
        self.worker = None
        self.tmp = tempfile.TemporaryDirectory(prefix='calibre-plugin-preflight-')
        self.backup_dir = None
        self.recovery_retained = False
        self.recovery_needed = False

        layout = QVBoxLayout(self)
        intro = QLabel('Installed-plugin updates from the official calibre catalogue. '
                       'All checks are deterministic; they cannot establish that code is safe.')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QHBoxLayout()
        self.btn_all = QPushButton('Select all')
        self.btn_none = QPushButton('Deselect all')
        self.btn_review = QPushButton('Download + review selected')
        self.btn_install = QPushButton('Install reviewed updates sequentially')
        self.btn_stop = QPushButton('Stop after current')
        self.btn_export = QPushButton('Export report')
        self.btn_close = QPushButton('Close')
        for btn in (self.btn_all, self.btn_none, self.btn_review,
                    self.btn_install, self.btn_stop, self.btn_export, self.btn_close):
            controls.addWidget(btn)
        layout.addLayout(controls)
        self.keep_backups = QCheckBox('Keep recovery ZIP backups after a successful run (optional; uses disk space)')
        self.keep_backups.setChecked(False)
        layout.addWidget(self.keep_backups)

        self.status = QLabel('Loading calibre plugin index…')
        layout.addWidget(self.status)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Select', 'Plugin', 'Installed', 'Available', 'Assessment / state'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)  # Queue order is fixed, visible, reproducible.
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, stretch=3)

        layout.addWidget(QLabel('Exact pre-install plan and detected risks:'))
        self.plan = QPlainTextEdit()
        self.plan.setReadOnly(True)
        layout.addWidget(self.plan, stretch=2)
        layout.addWidget(QLabel('Activity log:'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=2)

        self.btn_all.clicked.connect(lambda: self._select(True))
        self.btn_none.clicked.connect(lambda: self._select(False))
        self.btn_review.clicked.connect(self.start_preflight)
        self.btn_install.clicked.connect(self.start_install)
        self.btn_stop.clicked.connect(self.request_stop)
        self.btn_export.clicked.connect(self.export_report)
        self.btn_close.clicked.connect(self.reject)
        self.table.itemChanged.connect(self.selection_changed)
        self._update_controls()
        QTimer.singleShot(0, self.discover)

    def _write(self, msg):
        now = datetime.datetime.now().strftime('%H:%M:%S')
        self.log.appendPlainText(f'[{now}] {msg}')

    def _update_controls(self):
        selecting = self.phase in {'ready', 'reviewed'}
        self.btn_all.setEnabled(selecting)
        self.btn_none.setEnabled(selecting)
        self.table.setEnabled(selecting)
        self.keep_backups.setEnabled(self.phase in {'ready', 'reviewed'})
        self.btn_review.setEnabled(selecting and bool(self.items))
        self.btn_install.setEnabled(self.phase == 'reviewed' and self._can_install())
        self.btn_stop.setEnabled(self.phase in {'downloading', 'installing'})
        self.btn_close.setEnabled(self.phase not in {'downloading', 'installing', 'loading'})
        self.btn_export.setEnabled(self.phase != 'loading')

    def discover(self):
        try:
            self.items = sorted(get_plugin_updates_available(raise_error=True) or [],
                                key=lambda x: (x.qname == 'Bulk Plugin Updater', x.name.casefold()))
        except Exception:
            self.phase = 'ready'
            self.status.setText('Could not fetch calibre plugin index. No changes made.')
            self._write('ERROR: Failed to retrieve update catalogue:\n' + traceback.format_exc())
            self._update_controls()
            return

        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for row, dp in enumerate(self.items):
            meta = metadata_issues(dp)
            old_zip = config['plugins'].get(dp.qname)
            if not old_zip or not os.path.isfile(old_zip):
                meta.append({'level': 'BLOCK', 'code': 'NO_BACKUP_SOURCE',
                             'detail': 'The installed plugin ZIP is missing; a safe backup cannot be made.'})
            key = dp.qname
            self.record[key] = {'item': dp, 'metadata': meta, 'archive': [],
                                'sha256': None, 'path': None, 'status': 'Awaiting review'}
            check = QTableWidgetItem('')
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Unchecked if has_blockers(meta) else Qt.CheckState.Checked)
            self.table.setItem(row, 0, check)
            for col, val in enumerate((dp.name, format_version(dp.installed_version),
                                       format_version(dp.available_version), severity(meta)), start=1):
                cell = QTableWidgetItem(val)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, cell)
        self.table.blockSignals(False)
        self.phase = 'ready'
        self.status.setText(f'{len(self.items)} update(s) listed; choose plugins, then run pre-install review.')
        self._write(f'Discovered {len(self.items)} plugin update(s) (no installation performed).')
        self._make_plan()
        self._update_controls()

    def _select(self, state):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.selection_changed()

    def _selected(self):
        return [dp for row, dp in enumerate(self.items)
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def selection_changed(self, *_args):
        if self.phase in {'downloading', 'installing', 'loading'}:
            return
        # A previously downloaded review never authorizes a changed selection.
        self.phase = 'ready'
        self.queue = []
        self._make_plan()
        self._update_controls()

    def _make_plan(self):
        selected = self.queue if self.phase in {'downloading', 'reviewed', 'installing', 'complete'} else self._selected()
        lines = [f'SELECTION: {len(selected)} of {len(self.items)} available update(s).',
                 'Install order: alphabetical by name (self-update, if any, last).',
                 'Cleanup: temporary downloads deleted after completion/close; success backups deleted unless opt-in.',
                 'Failure or interrupted process may retain recovery backups and diagnostic logs in calibre config.',
                 'Source: https://code.calibre-ebook.com/plugins/ (calibre official catalogue).',
                 'Preflight inspections do NOT execute downloaded code.',
                 'WARNING: Installed calibre plugins can execute arbitrary code. No static check proves safety.',
                 'Unknown: runtime compatibility, author intent, malware, configuration migrations, and regressions.', '']
        for idx, dp in enumerate(selected, 1):
            r = self.record[dp.qname]
            lines.extend([
                f'{idx}. {dp.name}  [{format_version(dp.installed_version)} -> {format_version(dp.available_version)}]',
                f'   URL: {dp.zip_url}',
                f'   SHA-256: {r["sha256"] or "not downloaded yet"}',
                f'   State: {r["status"]}',
                f'   Assessment: {severity(r["metadata"] + r["archive"])}',
            ])
            for risk in r['metadata'] + r['archive']:
                lines.append(f'   - {risk["level"]} [{risk["code"]}]: {risk["detail"]}')
            lines.append('')
        if not selected:
            lines.append('Nothing selected.')
        self.plan.setPlainText('\n'.join(lines))

    def _set_status(self, dp, message):
        self.record[dp.qname]['status'] = message
        for row, item in enumerate(self.items):
            if item.qname == dp.qname:
                risks = severity(self.record[dp.qname]['metadata'] + self.record[dp.qname]['archive'])
                self.table.item(row, 4).setText(f'{risks} — {message}')
                break
        self._make_plan()

    def start_preflight(self):
        selected = self._selected()
        if not selected:
            QMessageBox.information(self, 'Nothing selected', 'Select at least one plugin.')
            return
        self.queue = selected
        self.index = 0
        self.stop_requested = False
        for dp in self.queue:
            r = self.record[dp.qname]
            r['archive'] = []
            r['sha256'] = None
            r['path'] = None
            r['status'] = 'Waiting for download'
        self.phase = 'downloading'
        self._write('Preflight started: download and inspect, NO installation yet.')
        self._update_controls()
        self._make_plan()
        QTimer.singleShot(0, self._preflight_next)

    def _preflight_next(self):
        if self.stop_requested:
            self.phase = 'ready'
            self._write('Preflight stopped by user. No plugin was installed.')
            self.status.setText('Preflight cancelled. Select and review again.')
            self._update_controls()
            self._make_plan()
            return
        if self.index >= len(self.queue):
            self.phase = 'reviewed'
            blocked = sum(has_blockers(self.record[dp.qname]['metadata'] + self.record[dp.qname]['archive'])
                          for dp in self.queue)
            self.status.setText(f'Preflight finished: {len(self.queue)} checked; {blocked} blocked. Review plan before installing.')
            self._write(f'Preflight complete, {blocked} blocked. NOTHING installed. Review the exact plan.')
            self._update_controls()
            self._make_plan()
            return
        dp = self.queue[self.index]
        self.status.setText(f'Preflight {self.index + 1}/{len(self.queue)}: {dp.name}')
        self._set_status(dp, 'Downloading')
        self._write(f'[{self.index + 1}/{len(self.queue)}] Downloading {dp.name} {format_version(dp.available_version)}')
        if has_blockers(self.record[dp.qname]['metadata']):
            self._write(f'  BLOCKED by catalogue metadata; download skipped: {dp.name}')
            self._set_status(dp, 'BLOCKED by metadata')
            self.index += 1
            QTimer.singleShot(0, self._preflight_next)
            return
        self.worker = DownloadThread(dp.zip_url, dp.qname, dp.available_version, self)
        self.worker.finished.connect(self._download_finished)
        self.worker.start()

    def _download_finished(self):
        worker = self.worker
        self.worker = None
        dp = self.queue[self.index]
        r = self.record[dp.qname]
        if worker.problem:
            r['archive'].append({'level': 'BLOCK', 'code': 'DOWNLOAD_FAILED', 'detail': worker.problem.strip()})
            self._write(f'  DOWNLOAD FAILED: {dp.name}: {worker.problem.strip()}')
            self._set_status(dp, 'BLOCKED — download failed')
        else:
            try:
                audit = worker.audit
                r['archive'] = audit['issues']
                r['sha256'] = audit['sha256']
                if not has_blockers(audit['issues']):
                    path = os.path.join(self.tmp.name, f'{self.index + 1:03d}.zip')
                    with open(path, 'wb') as out:
                        out.write(worker.data)
                    r['path'] = path
                self._write(f'  {len(worker.data)} bytes, {audit["members"]} member(s); SHA-256 {audit["sha256"]}')
                worker.data = None
                for risk in audit['issues']:
                    self._write(f'  {risk["level"]} [{risk["code"]}] {risk["detail"]}')
                self._set_status(dp, 'BLOCKED — archive' if has_blockers(r['archive']) else 'Preflight ready')
            except Exception:
                r['archive'].append({'level': 'BLOCK', 'code': 'PREFLIGHT_ERROR', 'detail': traceback.format_exc()})
                self._set_status(dp, 'BLOCKED — preflight error')
                self._write('  PREFLIGHT ERROR:\n' + traceback.format_exc())
        worker.deleteLater()
        self.index += 1
        QTimer.singleShot(0, self._preflight_next)

    def _can_install(self):
        return (bool(self.queue) and all(
            self.record[dp.qname]['path'] and
            not has_blockers(self.record[dp.qname]['metadata'] + self.record[dp.qname]['archive'])
            for dp in self.queue))

    def start_install(self):
        if self.phase != 'reviewed' or not self._can_install():
            return
        warnings = sum(any(x['level'] == 'REVIEW' for x in self.record[dp.qname]['metadata'] +
                           self.record[dp.qname]['archive']) for dp in self.queue)
        response = QMessageBox.warning(
            self, 'Approve sequential plugin installation',
            f'Install all {len(self.queue)} reviewed plugin update(s), one by one?\n'
            f'{warnings} plugin(s) have REVIEW warnings. Read the plan.\n\n'
            'The downloaded ZIPs are third-party code; checks cannot guarantee safety.\n'
            'A local backup of each existing plugin ZIP will be made before replacement.\n'
            + ('Successful-run backups will be retained.\n' if self.keep_backups.isChecked() else
               'Successful-run backups will be DELETED (no easy downgrade if runtime breaks).\n')
            + 'On failure the updater will attempt rollback and stop.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if response != QMessageBox.StandardButton.Yes:
            self._write('Installation declined, NO changes made.')
            return
        self.phase = 'installing'
        self.recovery_retained = self.keep_backups.isChecked()
        self.recovery_needed = False
        self.index = 0
        self.updates_installed = 0
        self.stop_requested = False
        now = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.backup_dir = os.path.join(config_dir, 'bulk-plugin-updater-backups', now)
        self._write(f'Installation approved. Backups: {self.backup_dir}')
        self._persist_report()
        self._update_controls()
        self._make_plan()
        QTimer.singleShot(0, self._install_next)

    def _install_next(self):
        if self.stop_requested:
            self._finish('Stopped by user between installs.')
            return
        if self.index >= len(self.queue):
            self._finish('All selected updates were processed.')
            return
        dp = self.queue[self.index]
        r = self.record[dp.qname]
        self.status.setText(f'Installing {self.index + 1}/{len(self.queue)}: {dp.name}')
        self._write(f'[{self.index + 1}/{len(self.queue)}] Installing {dp.name}: '
                    f'{format_version(dp.installed_version)} -> {format_version(dp.available_version)}')
        old_path = config['plugins'].get(dp.qname)
        backup = None
        replace_started = False
        try:
            if not old_path or not os.path.isfile(old_path):
                raise RuntimeError('Existing plugin ZIP unavailable — update blocked to prevent unrecoverable replacement.')
            os.makedirs(self.backup_dir, exist_ok=True)
            backup = os.path.join(self.backup_dir, f'{self.index + 1:03d}-previous.zip')
            shutil.copy2(old_path, backup)
            if not os.path.isfile(backup) or os.stat(backup).st_size == 0:
                raise RuntimeError('Old plugin ZIP backup failed validation.')
            def digest_file(p):
                h = hashlib.sha256()
                with open(p, 'rb') as fd:
                    for chunk in iter(lambda: fd.read(1024 * 1024), b''):
                        h.update(chunk)
                return h.hexdigest()
            if digest_file(old_path) != digest_file(backup):
                raise RuntimeError('Existing plugin backup failed SHA-256 verification.')
            self._write('  Backed up existing ZIP: ' + backup)
            self._set_status(dp, 'Replacing (backup saved)')
            # Re-audit file immediately before executing plugin code; prevent altered staging.
            with open(r['path'], 'rb') as inp:
                staged = inp.read()
            check = inspect_archive(staged, expected_name=dp.qname, expected_version=dp.available_version)
            if check['sha256'] != r['sha256'] or has_blockers(check['issues']):
                raise RuntimeError('Staged plugin archive changed or failed verification.')
            replace_started = True
            self.recovery_needed = True
            installed = add_plugin(r['path'])
            if (isinstance(installed.version, (tuple, list)) and
                    tuple(installed.version) != tuple(dp.available_version)):
                raise RuntimeError(f'Installed plugin reports {installed.version!r}, index expected {dp.available_version!r}.')
            if installed.name != dp.qname:
                raise RuntimeError(f'Installed plugin name mismatch: expected {dp.qname!r}, got {installed.name!r}.')
            # Verify physical ZIP retained by calibre (not just an API success return).
            actual_zip = config['plugins'].get(dp.qname)
            if not actual_zip or not os.path.isfile(actual_zip):
                raise RuntimeError('calibre did not retain expected ZIP after installation.')
            actual_hash = digest_file(actual_zip)
            if actual_hash != r['sha256']:
                raise RuntimeError('Installed ZIP hash differs from preflight ZIP hash.')
            self.updates_installed += 1
            self._set_status(dp, 'INSTALLED — restart pending')
            self._write(f'  SUCCESS {dp.name}; archive SHA-256 matches preflight.')
        except Exception:
            self._set_status(dp, 'FAILED — rollback attempted')
            self._write('  INSTALL ERROR:\n' + traceback.format_exc())
            if replace_started and backup and os.path.isfile(backup):
                try:
                    original = add_plugin(backup)
                    if original.name != dp.qname:
                        raise RuntimeError(f'Rollback yielded plugin named {original.name!r}.')
                    self._set_status(dp, 'FAILED — previous ZIP restored; restart needed')
                    self._write('  ROLLBACK: previous ZIP restored using calibre plugin installer.')
                    if self.updates_installed == 0:
                        self.recovery_needed = False
                except Exception:
                    self._set_status(dp, 'CRITICAL — rollback failed')
                    self._write('  CRITICAL: ROLLBACK FAILED; restore manually from ' + backup + '\n' + traceback.format_exc())
            elif not replace_started:
                self._set_status(dp, 'BLOCKED — old plugin not modified')
                self._write('  Failure occurred before replacement. Existing plugin ZIP unchanged.')
            else:
                self._write('  CRITICAL: Replacement started without an available backup.')
            self._finish('Stopped on first installation failure. Check the log and backups.')
            return
        self.index += 1
        QTimer.singleShot(0, self._install_next)

    def request_stop(self):
        self.stop_requested = True
        self._write('Stop requested. Current operation will finish; next item will not start.')
        self.btn_stop.setEnabled(False)

    def _finish(self, explanation):
        self.phase = 'complete'
        self.status.setText(f'{explanation} Successful updates: {self.updates_installed}/{len(self.queue)}.')
        self._write(explanation)
        self._write(f'Completed: {self.updates_installed} installed; recovery location: {self.backup_dir or "none"}')
        self._persist_report()
        self._make_plan()
        # All selected installs succeeded: backups are no longer necessary for
        # transactional rollback. Keep them only when the user explicitly opts in.
        should_keep = should_retain_backups(
            self.updates_installed, len(self.queue), self.recovery_retained, self.recovery_needed)
        if self.backup_dir and not should_keep:
            try:
                shutil.rmtree(self.backup_dir)
                self._write('CLEANUP: deleted unneeded backup ZIPs and diagnostic report.')
                self._remove_empty_backup_root()
                self.backup_dir = None
            except Exception:
                self._write('CLEANUP ERROR: cannot delete backups. Please remove manually:\n'
                            + self.backup_dir + '\n' + traceback.format_exc())
        elif self.backup_dir:
            self._write('RECOVERY MATERIAL RETAINED: ' + self.backup_dir)
        # Discard all downloaded/staged updates immediately after a run.
        try:
            self.tmp.cleanup()
            self._write('CLEANUP: deleted all preflight ZIP staging files.')
        except Exception:
            self._write('CLEANUP ERROR: staging could not be deleted:\n' + traceback.format_exc())
        self._make_plan()
        self._update_controls()
        if self.updates_installed:
            ans = QMessageBox.question(
                self, 'Restart calibre?',
                f'{self.updates_installed} plugin update(s) installed.\n'
                'Restart calibre now to activate updated plugin code?\n'
                'Choosing No leaves calibre running; updated plugins take effect on your next restart.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                self._write('User approved restart.')
                self.accept()
                QTimer.singleShot(0, lambda: self.gui.quit(restart=True))
            else:
                self._write('User deferred restart.')

    def _remove_empty_backup_root(self):
        root = os.path.join(config_dir, 'bulk-plugin-updater-backups')
        try:
            os.rmdir(root)  # Removes only if empty; never deletes other runs.
        except OSError:
            pass

    def _full_report(self):
        return ('CALIBRE BULK PLUGIN UPDATER — LOCAL REPORT\n\n'
                + self.plan.toPlainText() + '\n\nACTIVITY LOG\n'
                + self.log.toPlainText() + '\n')

    def _persist_report(self):
        if self.backup_dir:
            try:
                os.makedirs(self.backup_dir, exist_ok=True)
                with open(os.path.join(self.backup_dir, 'review-and-install-log.txt'), 'w', encoding='utf-8') as f:
                    f.write(self._full_report())
            except Exception:
                self._write('ERROR: Cannot save installation report:\n' + traceback.format_exc())

    def export_report(self):
        proposed = f'calibre-plugin-update-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt'
        dest, _ = QFileDialog.getSaveFileName(self, 'Save local review and update log', proposed, 'Text files (*.txt)')
        if dest:
            try:
                with open(dest, 'w', encoding='utf-8') as f:
                    f.write(self._full_report())
                self._write('Report saved locally: ' + dest)
            except Exception:
                QMessageBox.critical(self, 'Could not save report', traceback.format_exc())

    def reject(self):
        if self.phase in {'downloading', 'installing', 'loading'}:
            QMessageBox.information(self, 'Operation in progress',
                                    'Use Stop after current and wait for the operation to finish.')
            return
        super().reject()

    def done(self, result):
        # QDialog.reject/accept often hide the dialog without a closeEvent.
        # Cleanup belongs in done(), not just closeEvent().
        if self.phase in {'downloading', 'installing', 'loading'}:
            return
        try:
            self.tmp.cleanup()
        except Exception:
            pass
        super().done(result)

    def closeEvent(self, event):
        if self.phase in {'downloading', 'installing', 'loading'}:
            event.ignore()
            return
        super().closeEvent(event)


class BulkPluginUpdaterAction(InterfaceAction):
    name = 'Bulk Plugin Updater'
    action_spec = ('Bulk plugin updates', None, 'Review and update multiple calibre plugins safely', None)

    def genesis(self):
        # calibre provides get_icons() for resources bundled in the plugin ZIP.
        self.qaction.setIcon(get_icons('images/icon.png', 'Bulk Plugin Updater'))
        self.qaction.triggered.connect(self.show_updater)
        self._dialog = None

    def show_updater(self):
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._dialog = BulkUpdaterDialog(self.gui)
        self._dialog.show()
