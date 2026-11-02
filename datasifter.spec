# -*- mode: python ; coding: utf-8 -*-

import os
import runpy
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH)
version_namespace = runpy.run_path(str(project_root / "src" / "datasifter" / "build_version.py"))
build_version = version_namespace["reserve_build_version"](
    project_root / "src" / "datasifter" / "__init__.py",
    os.environ.get("DATASIFTER_BUILD_VERSION"),
)
info_namespace = runpy.run_path(str(project_root / "src" / "datasifter" / "build_info.py"))
build_stamp = info_namespace["write_build_stamp"](project_root / "src" / "datasifter")
print(f"Building DataSifter version {build_version} at {build_stamp} UTC", flush=True)

hidden_imports = collect_submodules("keyring.backends")
hidden_imports += collect_submodules("ldap3")
hidden_imports.append("datasifter._build_stamp")

# Python 3.15 uses this shim for dual abi3/abi3t extension wheels.
abi3t_compat = Path(sys.base_prefix) / "abi3t-compat" / "python3t.dll"
binaries = [(str(abi3t_compat), ".")] if abi3t_compat.is_file() else []

analysis = Analysis(
    ["src/datasifter/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="DataSifter 2.0",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="DataSifter 2.0",
)

bom_namespace = runpy.run_path(str(project_root / "tools" / "generate_bom.py"))
bom_namespace["generate"](project_root)
