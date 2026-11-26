"""用 EXE 中的真实 GUI 和更新模块运行隐藏窗口的启动选择回归，不打开 NCC。"""
import argparse
from contextlib import ExitStack
import marshal
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_offline_tests  # 兼容构建用旧版 Tcl 的中文路径。
from PyInstaller.archive.readers import CArchiveReader


def verify(executable):
    runtime=CArchiveReader(str(executable))
    pyz=runtime.open_embedded_archive('PYZ-00.pyz')
    with tempfile.TemporaryDirectory(prefix='ncc_bundled_prompt_') as folder,ExitStack() as stack:
        root=Path(folder)
        (root/'update_public_key.json').write_bytes(runtime.extract('update_public_key.json'))
        modules={}
        stack.enter_context(patch.dict(sys.modules,modules))
        for name in ('release_identity','app_updater','settlement_gui','update_dialog'):
            module=types.ModuleType(name)
            module.__file__=str(root/(name+'.py'))
            sys.modules[name]=module
            code=marshal.loads(runtime.extract(name)) if name=='settlement_gui' else pyz.extract(name)
            exec(code,module.__dict__)
        print('Bundled version: '+sys.modules['release_identity'].APP_VERSION,flush=True)
        # unittest 导入时获得的是上方装入的发行包实际类，而非本地源码类。
        suite=unittest.defaultTestLoader.loadTestsFromName('test_startup_update_prompt')
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        return result.wasSuccessful()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable',type=Path)
    args=parser.parse_args()
    sys.exit(not verify(args.executable))
