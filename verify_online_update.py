"""从 EXE 提取实际更新模块，在临时目录验证联网更新；不创建 GUI 或 NCC 会话。"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import time
import types
import sys
from unittest.mock import patch
from PyInstaller.archive.readers import CArchiveReader
import release_identity as identity


def verify(executable,download=False):
    runtime=CArchiveReader(str(executable))
    bundled_key=json.loads(runtime.extract('update_public_key.json').decode('utf-8-sig'))
    assert bundled_key['key_id']==identity.SIGNING_KEY_ID
    pyz_name=next(name for name in runtime.toc if name.endswith('.pyz'))
    pyz=runtime.open_embedded_archive(pyz_name)
    with tempfile.TemporaryDirectory(prefix='ncc_update_probe_') as folder,ExitStack() as stack:
        (Path(folder)/'update_public_key.json').write_bytes(runtime.extract('update_public_key.json'))
        bundled_identity=types.ModuleType('release_identity')
        bundled_identity.__file__=str(Path(folder)/'release_identity.py')
        exec(pyz.extract('release_identity'),bundled_identity.__dict__)
        stack.enter_context(patch.dict(sys.modules,{'release_identity':bundled_identity}))
        module=types.ModuleType('frozen_update_probe')
        module.__file__=str(executable)
        exec(pyz.extract('app_updater'),module.__dict__)
        manager=module.UpdateManager(folder)
        start=time.monotonic()
        state=manager.check()
        print(json.dumps({'check':state,'seconds':round(time.monotonic()-start,2)},ensure_ascii=True),flush=True)
        assert state['status'] in ('available','up_to_date'),state['message']
        if download:
            state=manager.download()
            print(json.dumps({'download':state,'seconds':round(time.monotonic()-start,2)},ensure_ascii=True),flush=True)
            assert state['status']=='ready',state['message']
            print(json.dumps({'stagedFiles':len(manager.ready['files']),'settingsModified':False,
                'targetDirectory':'temporary only'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable')
    parser.add_argument('--download',action='store_true')
    args=parser.parse_args()
    verify(Path(args.executable),args.download)
