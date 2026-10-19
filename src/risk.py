"""Deterministic, conservative plugin-update preflight.

Pure Python module: never imports or executes untrusted plugin code.
A clear report is NOT evidence that the package is harmless.
"""

import ast
import hashlib
import io
import re
import stat
import zipfile

# Deliberately fixed public, auditable limits and rules.
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ENTRY_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 3000
WARN_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
TRUSTED_PREFIX = 'https://code.calibre-ebook.com/plugins/'


def issue(level, code, detail):
    return {'level': level, 'code': code, 'detail': detail}


def format_version(v):
    if v is None:
        return 'unknown'
    if isinstance(v, (tuple, list)):
        return '.'.join(str(x) for x in v)
    return str(v)


def metadata_issues(item, current_plugin_name='Bulk Plugin Updater'):
    """Use calibre's own compatibility flags and official catalogue metadata."""
    ans = []
    if not item.is_valid_platform():
        ans.append(issue('BLOCK', 'PLATFORM', 'Not listed for this operating system.'))
    if not item.is_valid_calibre():
        ans.append(issue('BLOCK', 'CALIBRE_VERSION',
                         'Requires calibre >= ' + format_version(item.calibre_required_version) + '.'))
    if item.is_deprecated:
        ans.append(issue('BLOCK', 'DEPRECATED', 'Catalogue marks this plugin deprecated.'))
    if item.uninstall_plugins:
        ans.append(issue('BLOCK', 'UNINSTALL_DEPENDENCIES',
                         'Update instructs calibre to remove other plugins: ' + ', '.join(item.uninstall_plugins)
                         + '. This updater will not remove them automatically.'))
    if not item.is_installed():
        ans.append(issue('BLOCK', 'NOT_INSTALLED', 'Not an update of an installed plugin.'))
    if not item.is_upgrade_available():
        ans.append(issue('BLOCK', 'NOT_AN_UPDATE', 'A newer version is not advertised.'))
    if not item.zip_url.startswith(TRUSTED_PREFIX):
        ans.append(issue('BLOCK', 'UNEXPECTED_URL', 'Update download URL is outside calibre official catalogue host.'))
    installed = item.installed_version
    available = item.available_version
    if isinstance(installed, (tuple, list)) and isinstance(available, (tuple, list)) and installed and available:
        if int(available[0]) > int(installed[0]):
            ans.append(issue('REVIEW', 'MAJOR_VERSION', 'Major version number increases. Read upstream changelog.'))
    else:
        ans.append(issue('REVIEW', 'VERSION_UNVERIFIABLE', 'Installed or target version is not a comparable tuple.'))
    if item.qname == current_plugin_name:
        ans.append(issue('REVIEW', 'SELF_UPDATE', 'This updater is updating itself; it will be installed last.'))
    if not ans:
        ans.append(issue('INFO', 'NO_KNOWN_COMPAT_ISSUE', 'No incompatibility detected in calibre catalogue metadata.'))
    return ans


def inspect_archive(raw, expected_name=None, expected_version=None):
    """Inspect ZIP structure without importing/loading plugin Python code."""
    digest = hashlib.sha256(raw).hexdigest()
    result = {'sha256': digest, 'bytes': len(raw), 'issues': [], 'members': 0}
    out = result['issues']
    if len(raw) > MAX_ZIP_BYTES:
        out.append(issue('BLOCK', 'ZIP_TOO_LARGE', 'Compressed archive exceeds 50 MiB limit.'))
        return result
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
            result['members'] = len(infos)
            if len(infos) > MAX_MEMBERS:
                out.append(issue('BLOCK', 'TOO_MANY_MEMBERS', 'ZIP has more than 3000 entries.'))
                return result
            if not infos:
                out.append(issue('BLOCK', 'EMPTY_ARCHIVE', 'ZIP is empty.'))
            names = set()
            unpacked = 0
            native = set()
            scripts = set()
            for zi in infos:
                name = zi.filename.replace('\\', '/')
                chunks = name.split('/')
                if (not name or name.startswith('/') or '..' in chunks or '.' in chunks or
                        re.match(r'^[A-Za-z]:', name) or '\x00' in name):
                    out.append(issue('BLOCK', 'UNSAFE_PATH', 'Unsafe ZIP member path: ' + repr(name)))
                normalized = name.rstrip('/').casefold()
                if normalized in names:
                    out.append(issue('BLOCK', 'DUPLICATE_PATH', 'Duplicate/case-colliding ZIP member: ' + repr(name)))
                names.add(normalized)
                if zi.flag_bits & 0x1:
                    out.append(issue('BLOCK', 'ENCRYPTED', 'Encrypted ZIP member: ' + repr(name)))
                mode = zi.external_attr >> 16
                if stat.S_IFMT(mode) == stat.S_IFLNK:
                    out.append(issue('BLOCK', 'SYMLINK', 'Archive contains symbolic link: ' + repr(name)))
                if zi.file_size > MAX_ENTRY_BYTES:
                    out.append(issue('BLOCK', 'ENTRY_TOO_LARGE', 'ZIP member exceeds 100 MiB: ' + repr(name)))
                unpacked += zi.file_size
                ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
                if ext in {'so', 'dylib', 'dll', 'pyd', 'exe'}:
                    native.add(ext)
                if ext in {'sh', 'bat', 'cmd', 'ps1'}:
                    scripts.add(ext)
                if zi.compress_size and zi.file_size >= 10 * 1024 * 1024 and zi.file_size > zi.compress_size * 1000:
                    out.append(issue('REVIEW', 'HIGH_COMPRESSION', 'ZIP member has >1000:1 ratio: ' + repr(name)))
            if '__init__.py' not in zf.namelist():
                out.append(issue('BLOCK', 'NO_ROOT_INIT', 'ZIP lacks calibre plugin __init__.py at root.'))
            if unpacked > MAX_UNCOMPRESSED_BYTES:
                out.append(issue('BLOCK', 'UNPACKED_TOO_LARGE', 'Uncompressed members exceed 200 MiB.'))
            elif unpacked > WARN_UNCOMPRESSED_BYTES:
                out.append(issue('REVIEW', 'LARGE_CONTENT', 'Uncompressed members exceed 50 MiB.'))
            if native:
                out.append(issue('REVIEW', 'NATIVE_CODE', 'Bundled binary executable/library types: ' + ', '.join(sorted(native)) + '.'))
            if scripts:
                out.append(issue('REVIEW', 'SHELL_SCRIPTS', 'Bundled shell/batch scripts: ' + ', '.join(sorted(scripts)) + '.'))
            # Parse only the metadata's AST. Do not import/execute package code.
            if '__init__.py' in zf.namelist() and '__init__.py' in names:
                try:
                    if zf.getinfo('__init__.py').file_size > 2 * 1024 * 1024:
                        out.append(issue('BLOCK', 'LARGE_METADATA', 'Root plugin metadata Python file exceeds 2 MiB.'))
                    else:
                        init_bytes = zf.read('__init__.py')
                        tree = ast.parse(init_bytes)
                        wrappers = []
                        for node in tree.body:
                            if not isinstance(node, ast.ClassDef):
                                continue
                            bases = [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ''
                                     for b in node.bases]
                            if 'InterfaceActionBase' not in bases:
                                continue
                            props = {}
                            for child in node.body:
                                if isinstance(child, ast.Assign):
                                    for target in child.targets:
                                        if isinstance(target, ast.Name) and target.id in {'name', 'version'}:
                                            try:
                                                props[target.id] = ast.literal_eval(child.value)
                                            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                                                pass
                            wrappers.append(props)
                        if wrappers:
                            declared_names = [x['name'] for x in wrappers if isinstance(x.get('name'), str)]
                            if expected_name and declared_names and expected_name not in declared_names:
                                out.append(issue('BLOCK', 'NAME_MISMATCH',
                                                 f'Plugin metadata name(s) {declared_names!r} do not match {expected_name!r}.'))
                            versions = [tuple(x['version']) for x in wrappers
                                        if isinstance(x.get('version'), (tuple, list)) and
                                        all(isinstance(v, int) for v in x['version'])]
                            if expected_version and versions and tuple(expected_version) not in versions:
                                out.append(issue('BLOCK', 'VERSION_MISMATCH',
                                                 f'Plugin ZIP metadata version(s) {versions!r} do not match index {tuple(expected_version)!r}.'))
                        else:
                            out.append(issue('REVIEW', 'IDENTITY_NOT_STATIC',
                                             'Cannot verify plugin name/version from direct InterfaceActionBase metadata.'))
                except (UnicodeError, SyntaxError, zipfile.BadZipFile, RuntimeError, RecursionError, MemoryError) as e:
                    out.append(issue('BLOCK', 'BAD_PLUGIN_METADATA',
                                     f'Cannot parse root plugin metadata safely: {type(e).__name__}: {e}'))
            if not any(x['level'] == 'BLOCK' for x in out):
                bad = zf.testzip()
                if bad:
                    out.append(issue('BLOCK', 'CORRUPT_ZIP', 'CRC/decompression failed for: ' + repr(bad)))
    except Exception as e:
        out.append(issue('BLOCK', 'INVALID_ZIP', f'Cannot read ZIP: {type(e).__name__}: {e}'))
    if not out:
        out.append(issue('INFO', 'ARCHIVE_CHECK_OK', 'ZIP structure and CRC are readable; this is not a malware scan.'))
    return result


def has_blockers(issues):
    return any(x['level'] == 'BLOCK' for x in issues)


def severity(issues):
    if has_blockers(issues):
        return 'BLOCKED'
    if any(x['level'] == 'REVIEW' for x in issues):
        return 'REVIEW'
    return 'CHECKED'


def should_retain_backups(installed_count, planned_count, retain_on_success, recovery_needed):
    """Deterministic policy: no leftover state without changes; retain interrupted runs."""
    if not recovery_needed and installed_count == 0:
        return False
    if installed_count == planned_count and planned_count > 0:
        return bool(retain_on_success)
    return True
