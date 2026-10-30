import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'scrollweave_build_backend', ROOT / 'build_backend.py'
)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class PackagingTests(unittest.TestCase):
    def test_zero_packaging_dependencies(self):
        self.assertEqual(backend.get_requires_for_build_wheel(), [])
        self.assertEqual(backend.get_requires_for_build_sdist(), [])

    def test_versions_are_consistent(self):
        import re

        import scrollweave

        def version(path):
            text = (ROOT / path).read_text(encoding='utf-8')
            return re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.M).group(1)

        versions = {
            'package': scrollweave.__version__,
            'backend': backend.VERSION,
            'cargo': version('Cargo.toml'),
            'ffi': version('crates/scrollweave-ffi/Cargo.toml'),
            'cli': version('crates/scrollweave-cli/Cargo.toml'),
            'pyproject': version('pyproject.toml'),
        }
        self.assertEqual(set(versions.values()), {'0.9.4'}, versions)

    def test_can_prepare_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            folder = backend.prepare_metadata_for_build_wheel(d)
            metadata = (Path(d) / folder / 'METADATA').read_text(encoding='utf-8')
            self.assertIn('Name: scrollweave', metadata)
            self.assertIn('Version: 0.9.4', metadata)

    def test_can_build_source_distribution(self):
        with tempfile.TemporaryDirectory() as d:
            name = backend.build_sdist(d)
            import tarfile

            with tarfile.open(Path(d) / name) as bundle:
                names = bundle.getnames()
            self.assertTrue(any(s.endswith('/crates/scrollweave-ffi/src/lib.rs') for s in names))
            self.assertTrue(any(s.endswith('/crates/scrollweave-cli/src/main.rs') for s in names))
            self.assertTrue(any(s.endswith('/scrollweave/api.py') for s in names))
            self.assertTrue(any(s.endswith('/scrollweave/py.typed') for s in names))
            self.assertTrue(any(s.endswith('/pyproject.toml') for s in names))

    @unittest.skipUnless(shutil.which('cargo'), 'cargo is required to build a wheel')
    def test_wheel_contains_native_metadata_and_license(self):
        import zipfile

        with tempfile.TemporaryDirectory() as d:
            # A separate target dir avoids Windows locking the DLL already loaded
            # by the native integration tests earlier in this process.
            previous = os.environ.get('CARGO_TARGET_DIR')
            os.environ['CARGO_TARGET_DIR'] = str(Path(d) / 'target')
            try:
                name = backend.build_wheel(d)
            finally:
                if previous is None:
                    os.environ.pop('CARGO_TARGET_DIR', None)
                else:
                    os.environ['CARGO_TARGET_DIR'] = previous
            with zipfile.ZipFile(Path(d) / name) as wheel:
                names = wheel.namelist()
        self.assertIn('scrollweave/py.typed', names)
        self.assertTrue(any(name.endswith('/licenses/LICENSE') for name in names), names)
        self.assertTrue(any(name.startswith('scrollweave/_native/') for name in names), names)


if __name__ == '__main__':
    unittest.main()
