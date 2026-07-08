# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
PACKAGING_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = PACKAGING_DIR.parent


def existing_tree(relative_path: str, destination: str):
    source = PROJECT_ROOT / relative_path
    if not source.exists():
        return []
    return Tree(
        str(source),
        prefix=destination,
        excludes=["__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache", ".ruff_cache"],
    )


# Only bundled, read-only application resources are collected here. Runtime data
# is created under the per-user data directory by app.core.paths at startup.
datas = []
datas += collect_data_files("PySide6", include_py_files=False)
datas += existing_tree("app/config", "app/config")
datas += existing_tree("app/db/migrations", "app/db/migrations")
datas += existing_tree("app/assets", "app/assets")

hiddenimports = []
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("PySide6")

excluded_modules = [
    "pytest",
    "ruff",
    "tests",
]


a = Analysis(
    [str(PROJECT_ROOT / "app" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GasSafetyAlarm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GasSafetyAlarm",
)
