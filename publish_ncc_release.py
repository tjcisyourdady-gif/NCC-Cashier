"""发布事先准备的 NCC 完整包及独立清单；需显式 --publish。"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener

from app_updater import sha256_file, verify_envelope
import release_identity as identity
from prepare_update_release import publication_channel


def git_credential():
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never')
    result=subprocess.run(['git','credential','fill'],input=b'protocol=https\nhost=github.com\n\n',
        capture_output=True,env=env,timeout=20)
    fields={}
    for line in result.stdout.decode('utf-8',errors='replace').splitlines():
        if '=' in line:
            key,value=line.split('=',1); fields[key]=value
    token=fields.get('password')
    if result.returncode or not token:
        raise RuntimeError('本机未找到已登录的 GitHub 凭据，请先完成 GitHub 登录。')
    return token


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('发布接口发生跳转，未继续发送登录凭据。')


def api(token,path,method='GET',data=None,content_type='application/json'):
    url=path if path.startswith('https://') else 'https://api.github.com'+path
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc not in ('api.github.com','uploads.github.com'):
        raise ValueError('拒绝将登录凭据发送至其他站点。')
    body=json.dumps(data).encode() if isinstance(data,(dict,list)) else data
    request=Request(url,data=body,method=method,headers={'Authorization':'Bearer '+token,
        'User-Agent':'NCC-Cashier-Release','Accept':'application/vnd.github+json','Content-Type':content_type,
        'X-GitHub-Api-Version':'2022-11-28'})
    with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=15 if method=='GET' else 300) as response:
        raw=response.read()
    return json.loads(raw) if raw else {}


def publish(folder):
    plan=json.loads((Path(folder)/'release-plan.json').read_text(encoding='utf-8'))
    legacy=plan.get('legacy_bridge',False)
    if not isinstance(legacy,bool): raise ValueError('发布通道标识无效。')
    with publication_channel(legacy):
        return _publish(folder,legacy)


def _publish(folder,legacy):
    folder=Path(folder).resolve()
    plan=json.loads((folder/'release-plan.json').read_text(encoding='utf-8'))
    expected_repo=identity.GITHUB_OWNER+'/'+identity.GITHUB_REPO
    if plan['repository']!=expected_repo or plan['tag']!=identity.TAG_PREFIX+identity.APP_VERSION:
        raise ValueError('发行目标不符。')
    manifest=(folder/'latest.json').read_bytes()
    verified=verify_envelope(json.loads(manifest))
    if verified['version']!=identity.APP_VERSION: raise ValueError('发行版本不符。')
    asset=folder/plan['asset']
    if asset.name!=plan['asset'] or asset.suffix!='.zip' or asset.parent!=folder:
        raise ValueError('发行文件路径非法。')
    if sha256_file(asset)!=verified['package']['sha256'] or asset.stat().st_size!=verified['package']['size_bytes']:
        raise ValueError('完整包校验不通过。')
    token=git_credential()
    root='/repos/'+expected_repo
    # 旧仓库仅保留迁移发行，不替代原应用的正式最新版本。
    release=api(token,root+'/releases','POST',dict(tag_name=plan['tag'],name=plan['title'],
        body=verified['notes'],draft=True,prerelease=legacy,make_latest='false' if legacy else 'true',
        target_commitish=identity.GITHUB_BRANCH))
    release_id=release['id']
    try:
        upload=release['upload_url'].split('{',1)[0]+'?name='+quote(asset.name)
        api(token,upload,'POST',asset.read_bytes(),'application/zip')
        published=api(token,root+'/releases/'+str(release_id),'PATCH',dict(draft=False,prerelease=legacy,make_latest='false' if legacy else 'true'))
        target=root+'/contents/'+identity.MANIFEST_PATH
        previous=None
        try: previous=api(token,target)
        except HTTPError as exc:
            if exc.code!=404: raise
        change=dict(message='Publish NCC cashier '+identity.APP_VERSION+' signed update manifest',
            content=base64.b64encode(manifest).decode(),branch=identity.GITHUB_BRANCH)
        if previous: change['sha']=previous['sha']
        api(token,target,'PUT',change)
        print('已发布：'+published['html_url'])
        print('旧版迁移清单已更新，旧程序升级后转入 NCC 专用仓库。' if legacy else 'NCC 独立仓库的更新清单已更新。')
    except Exception:
        print('发布未完成，请核对 NCC 发行草稿和独立清单；不要删除其他发行。',file=sys.stderr)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    if not args.publish:
        parser.error('只在确认发布目标和文件后，使用 --publish 执行。')
    try: publish(args.folder)
    except Exception as exc:
        # Never print credentials, request headers or HTTP response bodies.
        print(str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '发布失败：'+type(exc).__name__,file=sys.stderr)
        sys.exit(1)
