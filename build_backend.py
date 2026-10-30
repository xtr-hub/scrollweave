"""PEP 517 wheel/sdist builder using *only* the Python standard library.

Build requirement: the Rust compiler and Cargo executable (toolchain, not runtime
package dependencies). No setuptools, maturin or PyO3 required.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = 'scrollweave'
VERSION = '0.9.2'


def _platform():
    return sysconfig.get_platform().replace('-', '_').replace('.', '_')


def _libname():
    if sys.platform == 'win32':
        return 'scrollweave_ffi.dll'
    if sys.platform == 'darwin':
        return 'libscrollweave_ffi.dylib'
    return 'libscrollweave_ffi.so'


def _metadata():
    return (
        f'Metadata-Version: 2.1\nName: {DIST}\nVersion: {VERSION}\n'
        'Summary: Rust-core, zero third-party dependency Python screenshot stitching library\n'
        'Requires-Python: >=3.10\n'
        'License: MIT\n'
        'Project-URL: Homepage, https://github.com/xtr-hub/scrollweave\n'
        'Project-URL: Repository, https://github.com/xtr-hub/scrollweave\n'
        'Project-URL: Documentation, https://github.com/xtr-hub/scrollweave/blob/main/docs/ARCHITECTURE.md\n'
        'Keywords: screenshot,stitching,scrolling,rust,computer-vision\n'
        'Classifier: Development Status :: 4 - Beta\n'
        'Classifier: Intended Audience :: Developers\n'
        'Classifier: License :: OSI Approved :: MIT License\n'
        'Classifier: Programming Language :: Python :: 3\n'
        'Classifier: Programming Language :: Python :: 3 :: Only\n'
        'Classifier: Programming Language :: Rust\n'
        'Classifier: Topic :: Multimedia :: Graphics\n'
        'Classifier: Topic :: Software Development :: Libraries :: Python Modules\n'
        'Description-Content-Type: text/markdown\n\n'
        + (ROOT / 'README.md').read_text(encoding='utf-8')
    )


def _dist_info():
    return f'{DIST}-{VERSION}.dist-info'


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    info = Path(metadata_directory) / _dist_info()
    info.mkdir(parents=True, exist_ok=True)
    (info / 'METADATA').write_text(_metadata(), encoding='utf-8')
    (info / 'WHEEL').write_text(
        'Wheel-Version: 1.0\nGenerator: scrollweave-stdlib-backend\n'
        f'Root-Is-Purelib: false\nTag: py3-none-{_platform()}\n',
        encoding='utf-8',
    )
    (info / 'entry_points.txt').write_text(
        '[console_scripts]\nscrollweave=scrollweave.__main__:main\n', encoding='utf-8'
    )
    licenses = info / 'licenses'
    licenses.mkdir(exist_ok=True)
    (licenses / 'LICENSE').write_bytes((ROOT / 'LICENSE').read_bytes())
    return info.name


def _compile_rust():
    cmd = ['cargo', 'build', '--release', '--lib', '--locked', '-p', 'scrollweave-ffi']
    try:
        subprocess.run(
            cmd,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            'Cargo is required at install/build time. Install Rust from rustup.rs.'
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '').strip()
        raise RuntimeError(f'Rust build failed (exit {exc.returncode}):\n{detail[-4000:]}') from exc
    target_dir = Path(os.environ.get('CARGO_TARGET_DIR', str(ROOT / 'target')))
    if not target_dir.is_absolute():
        target_dir = ROOT / target_dir
    path = target_dir / 'release' / _libname()
    if not path.is_file():
        raise RuntimeError(f'Rust build did not produce {path}')
    return path


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    dylib = _compile_rust()
    platform = _platform()
    name = f'{DIST}-{VERSION}-py3-none-{platform}.whl'
    destination = Path(wheel_directory) / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    record = []
    with zipfile.ZipFile(
        destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:

        def add(path, data):
            if not isinstance(data, bytes):
                data = data.encode('utf-8')
            archive.writestr(path, data)
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode('ascii').rstrip('=')
            )
            record.append(f'{path},sha256={digest},{len(data)}')

        for f in sorted((ROOT / 'scrollweave').iterdir()):
            if f.is_file() and (f.suffix == '.py' or f.name == 'py.typed'):
                add(f'scrollweave/{f.name}', f.read_bytes())
        add('scrollweave/_native/' + _libname(), dylib.read_bytes())
        info = _dist_info()
        add(f'{info}/METADATA', _metadata())
        add(
            f'{info}/WHEEL',
            f'Wheel-Version: 1.0\nGenerator: scrollweave-stdlib-backend\n'
            f'Root-Is-Purelib: false\nTag: py3-none-{platform}\n',
        )
        add(
            f'{info}/entry_points.txt', '[console_scripts]\nscrollweave=scrollweave.__main__:main\n'
        )
        add(f'{info}/licenses/LICENSE', (ROOT / 'LICENSE').read_bytes())
        archive.writestr(f'{info}/RECORD', '\n'.join(record + [f'{info}/RECORD,,']) + '\n')
    return name


def build_sdist(sdist_directory, config_settings=None):
    name = f'{DIST}-{VERSION}.tar.gz'
    output = Path(sdist_directory) / name
    output.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(output, 'w:gz') as tar:
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [
                d
                for d in dirs
                if d
                not in {
                    '.git',
                    'target',
                    '__pycache__',
                    '.pytest_cache',
                    '.ruff_cache',
                    '.mypy_cache',
                    '.venv',
                    'venv',
                    '.idea',
                    '.vscode',
                    'dist',
                    'output',
                    'benchmark_data',
                }
            ]
            for file in sorted(files):
                f = Path(root) / file
                if f.suffix in {'.pyc', '.zip', '.whl'} or file == 'manifest.sha256':
                    continue
                arc = f'{DIST}-{VERSION}/' + str(f.relative_to(ROOT)).replace(os.sep, '/')
                tar.add(f, arcname=arc, recursive=False)
        metadata = _metadata().encode('utf-8')
        info = tarfile.TarInfo(f'{DIST}-{VERSION}/PKG-INFO')
        info.size = len(metadata)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(metadata))
    return name
