# -*- mode: python ; coding: utf-8 -*-
"""Build the independent NCC cashier settlement GUI with its own runtime."""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_dir = Path(SPECPATH)
python_dir = Path(sys.base_prefix)
for source_name in (
    'settlement_gui.py', 'settlement_core.py', 'ncc_browser.py',
    'ncc_safety.py', 'ncc_background.py', 'ncc_dom.js', 'skip_list.py',
    'skip_list_results.py', 'skip_list_debug.py',
):
    if not (project_dir / source_name).is_file():
        raise RuntimeError('Required source is missing: ' + source_name)
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all(
    'playwright'
)

tk_datas = [
    (str(python_dir / 'tcl' / 'tcl8.6'), '_tcl_data'),
    (str(python_dir / 'tcl' / 'tk8.6'), '_tk_data'),
]
tk_binaries = [
    (str(python_dir / 'DLLs' / '_tkinter.pyd'), '.'),
    (str(python_dir / 'DLLs' / 'tcl86t.dll'), '.'),
    (str(python_dir / 'DLLs' / 'tk86t.dll'), '.'),
]
dom_script = project_dir / 'ncc_dom.js'
if not dom_script.is_file():
    raise RuntimeError('Required browser helper is missing: ' + str(dom_script))
project_datas = [(str(dom_script), '.'), (str(project_dir / 'update_install.ps1'), '.'),
                 (str(project_dir / 'update_public_key.json'), '.')]

a = Analysis(
    [str(project_dir / 'settlement_gui.py')],
    pathex=[str(project_dir)],
    binaries=playwright_binaries + tk_binaries,
    datas=playwright_datas + tk_datas + project_datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_dir / 'tk_runtime_hook.py')],
    excludes=['openpyxl', 'PIL', 'pandas', 'numpy', 'xlrd', 'xlsxwriter'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NCC出纳结算工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
