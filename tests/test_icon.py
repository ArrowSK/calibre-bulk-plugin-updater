"""Static regression checks for toolbar icon bundled in calibre plugin."""
import struct
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ToolbarIconTests(unittest.TestCase):
    def test_icon_png_is_present_and_transparent(self):
        png = (ROOT / 'src/images/icon.png').read_bytes()
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertGreater(len(png), 2500)
        w, h, bit_depth, color = struct.unpack('>IIBB', png[16:26])
        self.assertEqual((w, h, bit_depth, color), (128, 128, 8, 3))
        self.assertIn(b'tRNS', png)

    def test_action_sets_icon_from_bundled_resource(self):
        code = (ROOT / 'src/ui.py').read_text(encoding='utf-8')
        self.assertIn("self.qaction.setIcon(get_icons('images/icon.png', 'Bulk Plugin Updater'))", code)

    def test_build_archive_contains_png(self):
        from subprocess import run
        import sys
        result = run([sys.executable, str(ROOT/'build.py')], capture_output=True, text=True, check=True)
        self.assertIn('SHA-256', result.stdout)
        with zipfile.ZipFile(ROOT / 'dist/Bulk_Plugin_Updater-v0.1.1-beta.1.zip') as z:
            self.assertIn('images/icon.png', z.namelist())
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read('images/icon.png'), (ROOT / 'src/images/icon.png').read_bytes())


if __name__ == '__main__':
    unittest.main()
