"""准备已签名的独立 NCC 发行清单；本命令不会上传文件。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from app_updater import sha256_file, verify_envelope
import release_identity as identity


@contextmanager
def publication_channel(legacy_bridge=False):
    previous=(identity.GITHUB_REPO,identity.MANIFEST_PATH)
    try:
        if legacy_bridge:
            identity.GITHUB_REPO=identity.LEGACY_GITHUB_REPO
            identity.MANIFEST_PATH=identity.LEGACY_MANIFEST_PATH
        yield
    finally:
        identity.GITHUB_REPO,identity.MANIFEST_PATH=previous


def prepare(archive, destination=None, legacy_bridge=False):
    with publication_channel(legacy_bridge):
        return _prepare(archive,destination,legacy_bridge)


def _prepare(archive,destination,legacy_bridge):
    project=Path(__file__).resolve().parent
    archive=Path(archive).resolve()
    destination=Path(destination or project/'release_upload'/(identity.APP_VERSION+('-legacy' if legacy_bridge else ''))).resolve()
    with zipfile.ZipFile(str(archive)) as package:
        names=package.namelist()
        if any('/logs/' in n or '/userdata/' in n or '/.release-secrets/' in n for n in names):
            raise ValueError('发行包包含运行数据，已拒绝。')
        config=[n for n in names if n.endswith('/出纳结算设置.json')]
        if len(config)!=1 or json.loads(package.read(config[0]).decode('utf-8-sig')).get('skip_list_url'):
            raise ValueError('公开更新包必须使用空白文档网址，不得包含私人文档链接。')
        source=[n for n in names if n.endswith('/源码/skip_list.py')]
        if len(source)!=1 or "DEFAULT_SKIP_LIST_URL = ''" not in package.read(source[0]).decode('utf-8-sig'):
            raise ValueError('发行包源码尚未去除私人文档默认链接。')
    destination.mkdir(parents=True,exist_ok=False)
    asset=destination/('NCC_cashier_'+identity.APP_VERSION+'_full.zip')
    shutil.copyfile(str(archive),str(asset))
    payload=dict(schema=1,app_id=identity.APP_ID,version=identity.APP_VERSION,
        released_at=datetime.now(timezone.utc).isoformat(),
        notes=getattr(identity,'RELEASE_NOTES','每次启动自动后台检查更新；优先国内直连通道；签名与 SHA-256 校验；停止任务后可安装重启；保留设置、登录数据、记录和核对结果。'),
        full=dict(url='https://github.com/{}/{}/releases/download/{}{}/{}'.format(
            identity.GITHUB_OWNER,identity.GITHUB_REPO,identity.TAG_PREFIX,identity.APP_VERSION,asset.name),
            sha256=sha256_file(asset),size_bytes=asset.stat().st_size))
    raw=destination/'payload.json'
    raw.write_text(json.dumps(payload,ensure_ascii=True,sort_keys=True,separators=(',',':')),encoding='utf-8')
    signed=destination/'latest.json'
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'update_keys.ps1'),
        '-PayloadPath',str(raw),'-OutputPath',str(signed)],capture_output=True,timeout=30)
    if result.returncode:
        raise RuntimeError('本机更新签名失败，请使用创建私钥的 Windows 账号运行。')
    verify_envelope(json.loads(signed.read_text(encoding='utf-8-sig')))
    release=dict(repository=identity.GITHUB_OWNER+'/'+identity.GITHUB_REPO,tag=identity.TAG_PREFIX+identity.APP_VERSION,
        title='NCC 出纳结算工具 '+identity.DISPLAY_VERSION,asset=asset.name,manifest=identity.MANIFEST_PATH,
        legacy_bridge=legacy_bridge,prerelease=legacy_bridge,make_latest=not legacy_bridge,
        sha256=payload['full']['sha256'],size_bytes=asset.stat().st_size)
    (destination/'release-plan.json').write_text(json.dumps(release,ensure_ascii=False,indent=2),encoding='utf-8')
    print('发行文件已准备：'+str(destination))
    print('独立更新清单：'+identity.MANIFEST_PATH)
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    parser.add_argument('--destination')
    parser.add_argument('--legacy-bridge',action='store_true',help='为 V1.9/V1.9.1 生成一次性旧仓库迁移清单')
    args=parser.parse_args()
    prepare(args.archive,args.destination,args.legacy_bridge)
