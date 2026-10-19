"""Build an installable calibre plugin ZIP (no third-party build dependencies)."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import hashlib

HERE = Path(__file__).resolve().parent
SRC = HERE / 'src'
OUT = HERE / 'dist' / 'Bulk_Plugin_Updater-v0.1.0-beta.1.zip'
FILES = ('__init__.py', 'ui.py', 'risk.py', 'plugin-import-name-bulk_plugin_updater.txt')


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUT, 'w', ZIP_DEFLATED, compresslevel=9) as z:
        for name in FILES:
            path = SRC / name
            if not path.is_file():
                raise FileNotFoundError(path)
            z.write(path, arcname=name)
    with ZipFile(OUT) as z:
        if sorted(z.namelist()) != sorted(FILES) or z.testzip() is not None:
            raise RuntimeError('Package validation failed')
    print(OUT)
    print('SHA-256:', hashlib.sha256(OUT.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
