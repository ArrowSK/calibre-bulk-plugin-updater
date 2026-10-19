# Initial source review (manual, not the requested external Code Review connector)

Scope: `src/__init__.py`, `src/ui.py`, `src/risk.py`, `tests/test_risk.py`, `build.py`.

## Findings corrected before beta

1. **High — lifecycle cleanup could miss `reject()` / `accept()`**: QDialog can hide without calling `closeEvent`. Added idempotent temporary-directory cleanup in `done()` and explicit cleanup at batch completion.
2. **High — unbounded successful-run backup accumulation**: earlier code always retained old plugin ZIPs and reports. Success now removes recovery directory by default; optional retention is explicit. Failure/interruption retains files only where recovery is useful.
3. **Medium — risk inspection on GUI thread**: ZIP structural and CRC inspection moved into the download worker, avoiding UI freezes during normal preflight. Install-time revalidation still runs on the GUI thread because calibre installs there.
4. **Medium — archives could contain excessive member counts**: added explicit 3000-entry cap before per-file decompression checks.
5. **Medium — no regression coverage for cleanup decisions**: added tests for success, opt-in retention, no-change aborts, and partial failure.
6. **Medium — stale selection after completing updates**: selection and preflight disabled when installation is complete; close and reopen to refresh.
7. **High — rollback after partial replacement**: verified previous ZIP with SHA-256 before proceeding; rollback attempted through calibre's installer and recovery files retained on unsuccessful rollback.

## Open constraints / external validation needed

1. **High / not yet tested**: actual runtime installation and GUI behavior on a live calibre 8.x installation (macOS initially, ideally also Windows and Linux). Static syntax/tests do not replace this.
2. **High / inherent**: third-party plugin code executes during installation. A deterministic ZIP structure scan is not a malware detector or sandbox.
3. **Medium / inherent**: previously loaded code can remain active until restart; rollback of ZIP does not undo plugin-defined external side effects or data migrations.
4. **Medium / inherent**: a forced process kill or OS crash can leave temporary staged archives; ordinary completion/cancellation cleans them.
5. **Low**: success-run backups default to deletion to avoid residue, so easy rollback of a post-restart regression requires opting into retention first.
6. **Medium**: catalogue update and installer APIs are version-dependent; test against a live installed calibre before making a stable release.

## Tests

- 17 pure-Python unit tests for deterministic metadata, archive and retention logic.
- Python bytecode compile of plugin source.
- Build ZIP names / CRC integrity check.

The separate ChatGPT Code Review connector was not available among callable tools when this audit was prepared. This report is a manually conducted source review and must not be presented as a completed external Code Review pass.
