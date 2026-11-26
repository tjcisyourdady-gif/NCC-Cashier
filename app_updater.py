"""Python 3.8 兼容的独立更新客户端。只使用标准库，不控制桌面窗口。"""
import base64
import hashlib
import hmac
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
import release_identity as identity

MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
INSTALL_ROOT_FILES = frozenset((identity.EXE_NAME, '使用说明.txt', '完整包开始使用.txt', '调试读取文档.bat', 'README.md'))
INSTALL_SOURCE_FILES = frozenset(('settlement_gui.py','settlement_core.py','ncc_browser.py','ncc_safety.py',
    'ncc_background.py','ncc_dom.js','skip_list.py','skip_list_debug.py','skip_list_results.py','tk_runtime_hook.py',
    'package_tool.spec','build_package.ps1','调试读取文档.bat','源码运行说明.txt','完整包开始使用.txt','使用说明.txt',
    'app_updater.py','update_dialog.py','release_identity.py','update_install.ps1','update_public_key.json',
    'prepare_build_source.py','prepare_update_release.py','publish_ncc_release.py','update_keys.ps1'))
SKIP_ROOTS = frozenset(('出纳结算设置.json',))
_RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',re.I)


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    match = re.fullmatch(r'[vV]?(\d+(?:\.\d+){1,3})',str(value))
    if match is None:
        raise UpdateError('更新版本号格式无效。')
    parts = tuple(int(x) for x in match.group(1).split('.'))
    return parts + (0,) * (4-len(parts))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def trusted_asset_url(url, version):
    try:
        parsed = urlsplit(url)
        prefix = '/{}/{}/releases/download/{}{}/'.format(identity.GITHUB_OWNER,identity.GITHUB_REPO,identity.TAG_PREFIX,version)
        filename=unquote(parsed.path[len(prefix):])
        return (parsed.scheme=='https' and parsed.netloc=='github.com' and not parsed.query and not parsed.fragment
            and parsed.path.startswith(prefix) and '/' not in parsed.path[len(prefix):]
            and re.fullmatch(r'[A-Za-z0-9_.-]+\.zip',filename) is not None)
    except ValueError:
        return False


def validate_manifest(raw):
    if not isinstance(raw,dict) or raw.get('schema')!=1 or raw.get('app_id')!=identity.APP_ID:
        raise UpdateError('更新清单不属于 NCC 出纳结算工具，已拒绝。')
    version = str(raw.get('version',''))
    version_tuple(version)
    package = raw.get('full')
    if not isinstance(package,dict) or not trusted_asset_url(str(package.get('url','')),version):
        raise UpdateError('更新包地址不属于 NCC 的独立发布通道。')
    if re.fullmatch('[0-9a-fA-F]{64}',str(package.get('sha256',''))) is None:
        raise UpdateError('更新包缺少有效的 SHA-256 校验值。')
    size = package.get('size_bytes')
    if isinstance(size,bool) or not isinstance(size,int) or not 0<size<=MAX_ARCHIVE:
        raise UpdateError('更新包大小超出允许范围。')
    return dict(version=version,notes=str(raw.get('notes',''))[:20000],released_at=str(raw.get('released_at','')),
        package=dict(url=package['url'],sha256=package['sha256'].lower(),size_bytes=size))


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        allowed = {'github.com','api.github.com','raw.githubusercontent.com','release-assets.githubusercontent.com',
                   'objects.githubusercontent.com','ghfast.top','gh-proxy.com','gcore.jsdelivr.net','cdn.jsdelivr.net'}
        if parsed.scheme!='https' or parsed.hostname not in allowed or parsed.username or parsed.password or parsed.port:
            raise UpdateError('更新下载发生未允许的跳转。')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_url(url, timeout=15):
    # 独立直连，不依赖用户电脑上正在运行的代理软件。
    return build_opener(ProxyHandler({}),SafeRedirect()).open(Request(url,headers={
        'User-Agent':'NCC-Cashier/'+identity.APP_VERSION,'Cache-Control':'no-cache'}),timeout=timeout)


def json_bytes(data):
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result: raise UpdateError('更新清单包含重复字段。')
            result[key]=value
        return result
    return json.loads(data.decode('utf-8-sig'),object_pairs_hook=pairs)


def verify_envelope(envelope):
    """严格验证完整 PKCS#1 SHA-256 编码；镜像无法自行替换更新清单。"""
    try:
        if not isinstance(envelope,dict) or envelope.get('key_id')!=identity.SIGNING_KEY_ID:
            raise ValueError('key')
        payload=base64.b64decode(envelope['payload'],validate=True)
        signature=base64.b64decode(envelope['signature'],validate=True)
        modulus=int.from_bytes(base64.b64decode(identity.SIGNING_MODULUS,validate=True),'big')
        length=(modulus.bit_length()+7)//8
        if len(signature)!=length or int.from_bytes(signature,'big')>=modulus or modulus.bit_length()<2048:
            raise ValueError('size')
        decoded=pow(int.from_bytes(signature,'big'),65537,modulus).to_bytes(length,'big')
        digest_info=bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(payload).digest()
        expected=b'\x00\x01'+b'\xff'*(length-len(digest_info)-3)+b'\x00'+digest_info
        if not hmac.compare_digest(decoded,expected): raise ValueError('signature')
        return validate_manifest(json_bytes(payload))
    except UpdateError:
        raise
    except (ValueError,KeyError,TypeError,OverflowError):
        raise UpdateError('更新清单签名校验失败，已停止更新。') from None


def fetch_manifest():
    root = '{}/{}'.format(identity.GITHUB_OWNER,identity.GITHUB_REPO)
    raw_url='https://raw.githubusercontent.com/{}/{}/{}'.format(root,identity.GITHUB_BRANCH,identity.MANIFEST_PATH)
    urls=[mirror+raw_url for mirror in identity.DOWNLOAD_MIRRORS]
    urls += ['https://gcore.jsdelivr.net/gh/{}@{}/{}'.format(root,identity.GITHUB_BRANCH,identity.MANIFEST_PATH),
             'https://cdn.jsdelivr.net/gh/{}@{}/{}'.format(root,identity.GITHUB_BRANCH,identity.MANIFEST_PATH),
             'https://api.github.com/repos/{}/contents/{}?ref={}'.format(root,identity.MANIFEST_PATH,identity.GITHUB_BRANCH),raw_url]
    missing = 0
    rejected=False
    for url in urls:
        try:
            with open_url(url,8) as response:
                body = response.read(512*1024+1)
            if len(body)>512*1024: raise UpdateError('更新清单过大。')
            raw = json_bytes(body)
            if url.startswith('https://api.github.com/'):
                if raw.get('encoding')!='base64': raise UpdateError('官方更新清单格式无效。')
                raw = json_bytes(base64.b64decode(raw['content'],validate=False))
            manifest=verify_envelope(raw)
            manifest['source']='国内直连通道' if url not in urls[-2:] else 'GitHub 官方备用'
            return manifest
        except HTTPError as exc:
            if exc.code==404: missing+=1
        except UpdateError:
            rejected=True
        except Exception:
            pass
    if missing==len(urls):
        raise UpdateError('NCC 自动更新清单尚未发布，当前程序仍可正常使用。')
    if rejected: raise UpdateError('未取得签名校验通过的 NCC 更新清单，请稍后重试。')
    raise UpdateError('暂时无法连接更新通道，请稍后重试；当前程序可继续使用。')


def _parts(name):
    raw = name.replace('\\','/')
    parts = raw.rstrip('/').split('/')
    if (not raw or raw.startswith('/') or ntpath.splitdrive(raw)[0] or any(not p or p in ('.','..') for p in parts)
            or any(any(c in p for c in '<>:"|?*') or p.endswith((' ','.')) or _RESERVED.match(p) or any(ord(c)<32 for c in p) for p in parts)):
        raise UpdateError('更新压缩包包含非法路径。')
    return parts


def stage_package(zip_path, stage_dir):
    stage_dir = Path(stage_dir)
    if stage_dir.exists(): raise UpdateError('更新暂存目录已存在，未覆盖。')
    with zipfile.ZipFile(str(zip_path)) as archive:
        infos = archive.infolist()
        if not infos or len(infos)>20000 or sum(i.file_size for i in infos)>MAX_EXPANDED:
            raise UpdateError('更新包为空或解压大小超限。')
        parsed = [(info,_parts(info.filename)) for info in infos]
        roots = {parts[0] for _,parts in parsed}
        strip = len(roots)==1 and next(iter(roots)) not in INSTALL_ROOT_FILES | {'ms-playwright（勿动）'}
        seen,selected = set(),[]
        for info,parts in parsed:
            mode = info.external_attr>>16
            if stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise UpdateError('更新包包含链接或加密文件，已拒绝。')
            if strip: parts=parts[1:]
            if not parts: continue
            lower = '/'.join(parts).casefold()
            if lower in seen and not info.is_dir(): raise UpdateError('更新包存在重名文件。')
            seen.add(lower)
            top=parts[0]
            if top in SKIP_ROOTS:
                continue
            if top=='源码':
                if info.is_dir() and len(parts)==1: continue
                if len(parts)!=2 or parts[1] not in INSTALL_SOURCE_FILES:
                    raise UpdateError('更新包包含不允许安装的源码文件。')
            elif top not in INSTALL_ROOT_FILES and top!='ms-playwright（勿动）':
                raise UpdateError('更新包包含不允许覆盖的内容：'+top)
            if top in INSTALL_ROOT_FILES and len(parts)!=1:
                raise UpdateError('更新包文件结构异常。')
            if not info.is_dir(): selected.append((info,parts))
        if not any(parts==[identity.EXE_NAME] for _,parts in selected):
            raise UpdateError('更新包缺少 NCC 主程序。')
        if shutil.disk_usage(str(stage_dir.parent)).free < MAX_EXPANDED//4 + sum(i.file_size for i,_ in selected)*2:
            raise UpdateError('可用磁盘空间不足以暂存和备份更新。')
        stage_dir.mkdir()
        files=[]
        for info,parts in selected:
            target = stage_dir.joinpath(*parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(info) as source,target.open('wb') as dest:
                shutil.copyfileobj(source,dest,1024*1024)
            files.append({'path':'/'.join(parts),'sha256':sha256_file(target)})
        return files


def validate_target(app_dir, relative):
    parts=_parts(relative)
    if not ((len(parts)==1 and parts[0] in INSTALL_ROOT_FILES)
            or (len(parts)==2 and parts[0]=='源码' and parts[1] in INSTALL_SOURCE_FILES)
            or (len(parts)>1 and parts[0]=='ms-playwright（勿动）')):
        raise UpdateError('不允许更新此文件。')
    root=Path(app_dir).resolve()
    target=root.joinpath(*parts)
    try: target.resolve().relative_to(root)
    except ValueError: raise UpdateError('更新目标路径越界。') from None
    # Windows 目录联接也不得成为更新目标路径的一部分。
    for part in [target]+list(target.parents):
        if part==root: break
        if part.exists():
            attributes=getattr(part.stat(),'st_file_attributes',0)
            if part.is_symlink() or attributes & 0x400: raise UpdateError('更新目标存在链接或目录联接。')
    return target


class UpdateManager:
    def __init__(self, base_dir):
        self.base_dir=Path(base_dir).resolve()
        self.root=self.base_dir/'.updates'
        self.lock=threading.RLock()
        self.operation=threading.Lock()
        self.manifest=self.ready=self.install_lock=None
        self.state={'status':'idle','message':'启动后会自动检查 NCC 更新。','progress':0,
                    'current_version':identity.APP_VERSION,'latest_version':'','notes':'','source':''}

    def status(self):
        with self.lock: return dict(self.state)

    def _state(self,**values):
        with self.lock:
            self.state.update(values)
            return dict(self.state)

    def check(self):
        if not self.operation.acquire(False): return self.status()
        try:
            self.manifest=self.ready=None
            self._state(status='checking',message='正在后台检查 NCC 更新…',progress=0,latest_version='',notes='',source='')
            manifest=fetch_manifest()
            self.manifest=manifest
            available=version_tuple(manifest['version'])>version_tuple(identity.APP_VERSION)
            return self._state(status='available' if available else 'up_to_date',latest_version=manifest['version'],
                notes=manifest['notes'],source=manifest.get('source',''),
                message='发现 NCC 新版本 '+manifest['version'] if available else 'NCC 当前已是最新版本。')
        except Exception as exc:
            return self._state(status='error',message=str(exc) if isinstance(exc,UpdateError) else '检查更新失败，请稍后重试。')
        finally: self.operation.release()

    def download(self):
        if not self.operation.acquire(False): return self.status()
        try:
            if self.manifest is None or version_tuple(self.manifest['version'])<=version_tuple(identity.APP_VERSION):
                raise UpdateError('请先检查是否有 NCC 新版本。')
            self.ready=None
            self.root.mkdir(parents=True,exist_ok=True)
            if self.root.is_symlink() or getattr(self.root.stat(),'st_file_attributes',0)&0x400:
                raise UpdateError('更新目录存在链接，未继续。')
            job=self.root/uuid.uuid4().hex
            job.mkdir()
            package=self.manifest['package']
            part=job/'package.part'
            urls=[('国内直连通道 '+str(i+1),mirror+package['url']) for i,mirror in enumerate(identity.DOWNLOAD_MIRRORS)]+[('GitHub 官方备用',package['url'])]
            verified=False
            for label,url in urls:
                self._state(status='downloading',message='正在下载 NCC 完整包…',progress=0,source=label)
                try:
                    hasher,done=hashlib.sha256(),0
                    with open_url(url,12) as response,part.open('wb') as stream:
                        while True:
                            block=response.read(128*1024)
                            if not block: break
                            done+=len(block)
                            if done>package['size_bytes']: raise UpdateError('下载大小超过清单声明。')
                            stream.write(block)
                            hasher.update(block)
                            self._state(progress=min(99,int(done*100/package['size_bytes'])))
                    if done!=package['size_bytes'] or hasher.hexdigest()!=package['sha256']:
                        raise UpdateError('下载完整性校验失败。')
                    verified=True
                    break
                except Exception:
                    if part.exists(): part.unlink()
            if not verified: raise UpdateError('官方下载和备用通道均未取得校验通过的完整包。')
            self._state(status='staging',message='校验通过，正在准备更新文件…',progress=99)
            stage=job/'stage'
            files=stage_package(part,stage)
            if not any(f['path']==identity.EXE_NAME for f in files): raise UpdateError('更新包缺少 NCC 主程序。')
            for file in files: validate_target(self.base_dir,file['path'])
            part.unlink()
            self.ready={'job':job,'stage':stage,'files':files,'version':self.manifest['version']}
            return self._state(status='ready',message='更新已校验完成。停止任务后，可安装并重启。',progress=100)
        except Exception as exc:
            self.ready=None
            return self._state(status='error',message=str(exc) if isinstance(exc,UpdateError) else '下载或准备更新失败，当前版本未修改。')
        finally: self.operation.release()

    def installation_plan(self):
        if self.ready is None or self.status()['status']!='ready': raise UpdateError('尚无已校验的更新。')
        ready=self.ready
        stage=ready['stage'].resolve()
        stage.relative_to(self.root.resolve())
        for file in ready['files']:
            validate_target(self.base_dir,file['path'])
            source=stage.joinpath(*_parts(file['path']))
            if source.is_symlink() or sha256_file(source)!=file['sha256']:
                raise UpdateError('暂存文件发生变化，请重新下载更新。')
        return {'app_id':identity.APP_ID,'app_dir':str(self.base_dir),'stage_dir':str(stage),
            'backup_dir':str(ready['job']/'backup'),'exe_name':identity.EXE_NAME,'pid':os.getpid(),
            'version':ready['version'],'files':ready['files']}

    def start_installer(self,is_busy):
        if is_busy(): raise UpdateError('请先停止结算或调试任务，等待浏览器关闭后再安装。')
        if not getattr(sys,'frozen',False): raise UpdateError('源码模式只检查和下载；请在 EXE 中安装更新。')
        if os.name!='nt': raise UpdateError('自动安装目前只支持 Windows。')
        if not self.operation.acquire(False): raise UpdateError('更新操作正在进行中。')
        try:
            from ncc_safety import SingleInstanceLock
            lock=SingleInstanceLock(self.base_dir/'logs'/'结算操作记录.json').acquire()
            try:
                if is_busy(): raise UpdateError('任务状态已变化，未开始安装。')
                plan=self.installation_plan()
                plan['lock_path']=str(lock.lock_path)
                ready=self.ready
                plan_path=ready['job']/'install-plan.json'
                plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8-sig')
                resource=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'update_install.ps1'
                helper=ready['job']/'install.ps1'
                shutil.copyfile(resource,helper)
                flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'DETACHED_PROCESS',0)
                subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',str(helper),
                    '-PlanPath',str(plan_path),'-PlanSha256',sha256_file(plan_path)],
                    creationflags=flags,close_fds=True)
                self.install_lock=lock
                return self._state(status='installing',message='正在关闭程序并安装更新…')
            except BaseException:
                lock.release()
                raise
        finally: self.operation.release()
