# Bulk Plugin Updater for calibre

**Review and update several calibre plugins in one session—without repeatedly opening the plugin manager.**

![Bulk Plugin Updater toolbar icon](src/images/icon.png)

Bulk Plugin Updater is a free, **unofficial** calibre add-on. It lists available updates for your installed third-party plugins, checks each download against a fixed set of safety and compatibility rules, installs the updates **one at a time**, and asks whether you want to restart calibre when it finishes.

> **Status: beta (v0.1.1-beta.1).** The project owner has successfully installed and used Bulk Plugin Updater in a running calibre installation on macOS. Automated tests and packaging checks also pass. As with any plugin manager, backing up calibre's configuration before a large update is recommended.

## What it does

- **Review before you change anything.** See plugin names, current versions, available versions, and detected concerns before installation.
- **Pick several updates at once.** Individual checkboxes, **Select all**, and **Deselect all**; blocked updates cannot be installed.
- **Know what will happen.** A detailed pre-install plan shows update order, download URLs, SHA-256 hashes, compatibility warnings and archive checks.
- **Install sequentially.** Updates are installed in a predictable order; the batch stops after the first failed update rather than continuing blindly.
- **Keep a recovery path.** A copy of the old plugin ZIP is made before each replacement. If an installation fails, the updater attempts to restore the previous ZIP.
- **Restart only if you agree.** One optional restart prompt appears after the batch. Nothing restarts automatically.

It **does not** update calibre itself, built-in plugins, or plugins that are absent from calibre's official plugin index.

## Install (macOS, Windows, Linux)

Requires **calibre 8.0 or newer** and an internet connection.

1. Download the **installable plugin ZIP**: [Bulk_Plugin_Updater-v0.1.1-beta.1.zip](downloads/Bulk_Plugin_Updater-v0.1.1-beta.1.zip). **Do not extract it.** GitHub's green **Code → Download ZIP** is *source code*, not an installable calibre plugin.
2. In calibre, open **Preferences → Plugins → Load plugin from file**.
3. Choose the downloaded ZIP. Confirm calibre's warning about installing third-party code only if you trust the source.
4. Restart calibre to load the new interface action.
5. Look for **Bulk plugin updates**, now with a book-and-refresh icon. If it is not visible, go to **Preferences → Toolbars & menus**, add the action to a toolbar or menu, then restart if calibre requests it.

### Update your plugins

1. Open **Bulk plugin updates**.
2. Select the plugins to update, or choose **Select all**.
3. Click **Download + review selected**. Downloads and archive inspection do **not** install the updates.
4. Read the **Exact pre-install plan and detected risks**. Deselect any blocked items and run the review again.
5. Click **Install reviewed updates sequentially**, and confirm the complete batch.
6. Read the activity log and choose whether to **Restart calibre**.

**Tip:** For your first run, select one low-impact plugin rather than upgrading everything. Enable **Keep recovery ZIP backups** if easy manual rollback after a restart matters to you.

## How the risk checks work

The checks are **deterministic**: the same input archive and catalogue metadata produce the same warnings. They do not use AI-generated risk scores or invented probabilities.

| Result | Meaning |
| --- | --- |
| **BLOCKED** | A defined rule found a problem (for example, an incompatible calibre version, unsupported platform, unsafe ZIP path, invalid archive, or mismatch in readable plugin metadata). The update cannot proceed. |
| **REVIEW** | A condition calls for manual judgment (for example, a major version change, native binaries, or metadata that cannot be checked statically). |
| **CHECKED** | No defined blocking or review condition was detected. **This is not a declaration of safety.** |

The preflight also records SHA-256 hashes and verifies the ZIP can be read. **An updated plugin can still be malicious, broken, or incompatible even when every check passes.** calibre executes plugin Python code during installation; the updater is not a sandbox, antivirus scanner, or runtime compatibility test.

## Does it leave files behind?

The design aims for **no unnecessary residue after a successful update**:

| Situation | What remains |
| --- | --- |
| Cancel or close without installing | Downloaded temporary ZIPs removed when the dialog closes. |
| Successful batch (default) | Temporary downloads, rollback ZIPs and transient report removed. |
| Successful batch with **Keep recovery ZIP backups** selected | Previous plugin ZIPs and the diagnostic report retained on purpose. |
| Partial failure / interrupted batch | Recovery material retained when needed for manual repair. |
| Forced quit or system crash | Some temporary files may remain; cleanup after a crash cannot be guaranteed. |
| **Export report** | The log remains where **you** explicitly saved it. |

Retained recovery files are stored under `<calibre configuration directory>/bulk-plugin-updater-backups/<timestamp>/`. The updated plugin ZIPs installed by calibre remain in calibre's normal plugin directory, as they should.

The updater creates **no background daemon, scheduled job, telemetry collection, extra service, or database**. Third-party plugins it updates may create their *own* files or change their settings; the updater cannot undo those side effects.

## Known limitations

- Only updates advertised in calibre's **official plugin catalogue** are listed. Hand-installed or unindexed plugins may be missing.
- If an update requires **uninstalling another plugin**, this updater blocks it instead of removing anything automatically.
- A plugin's final behavior cannot be verified until calibre restarts.
- Removing successful-run backups by default saves disk space, but means you **cannot easily undo a regression discovered later**. Enable backup retention before installing if that risk matters.
- The plugin has been used successfully with calibre on macOS. Windows/Linux compatibility and exceptional failure/rollback paths have not been independently verified; some calibre APIs are version-dependent.

## For contributors

Source is in `src/`; the deterministic scanner and cleanup rules are in `src/risk.py`. The native calibre action lives in `src/ui.py`. Unit tests are in `tests/`. See [initial code review](docs/CODE_REVIEW.md) for corrected findings and remaining testing gaps.

To test and build from a local checkout (Python 3.10+):

```bash
python -m unittest discover -s tests -v
python build.py
```

The build script creates the **installable** ZIP in `dist/`. GitHub's source ZIP is **not** interchangeable with it. CI covers automated tests and packaging; the project owner has separately confirmed live operation in calibre on macOS.

This project is not affiliated with or endorsed by calibre or its maintainers. Plugin authors and catalogue listings remain responsible for their own updates.

## Licence

GPL-3.0-or-later; see [LICENSE](LICENSE).
