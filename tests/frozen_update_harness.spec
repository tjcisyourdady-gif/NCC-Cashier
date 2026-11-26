from pathlib import Path
root=Path(SPECPATH).parent
a=Analysis([str(root/'tests'/'frozen_update_harness.py')],pathex=[str(root)],
    binaries=[],datas=[(str(root/'update_install.ps1'),'.'),(str(root/'update_public_key.json'),'.')],
    hiddenimports=[],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['tkinter','playwright'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='NCC出纳结算工具',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False)
