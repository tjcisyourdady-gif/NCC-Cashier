"""用 V1.9.4 EXE 的真实更新模块验证轻量包、替换程序及保留浏览器和设置。"""
from contextlib import ExitStack
import argparse,json,shutil,subprocess,sys,tempfile,types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PyInstaller.archive.readers import CArchiveReader
from ncc_safety import SingleInstanceLock
import app_updater
import release_identity

project=Path.cwd()
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--version',default=release_identity.APP_VERSION)
args=parser.parse_args()
target=args.version
app_updater.version_tuple(target)
old=project/'release/NCC出纳结算工具_V1.9.4_完整版/NCC出纳结算工具.exe'
archive=project/('NCC出纳结算工具_V'+target+'_轻量更新.zip')
signed=json.loads((project/'release_upload'/target/'latest.json').read_text(encoding='utf-8-sig'))
r=CArchiveReader(str(old)); pyz=r.open_embedded_archive('PYZ-00.pyz')
with tempfile.TemporaryDirectory(prefix='ncc_light_update_') as name,ExitStack() as stack:
    root=Path(name); (root/'update_public_key.json').write_bytes(r.extract('update_public_key.json'))
    identity=types.ModuleType('release_identity'); identity.__file__=str(root/'release_identity.py')
    exec(pyz.extract('release_identity'),identity.__dict__)
    assert identity.APP_VERSION=='1.9.4'
    stack.enter_context(patch.dict(sys.modules,{'release_identity':identity}))
    updater=types.ModuleType('v194_updater'); updater.__file__=str(old)
    exec(pyz.extract('app_updater'),updater.__dict__)
    manifest=updater.verify_envelope(signed)
    assert manifest['version']==target and manifest['package']['url'].endswith('_update.zip')
    job=root/'.updates/fixture'; job.mkdir(parents=True)
    files=updater.stage_package(archive,job/'stage')
    assert not any('ms-playwright' in f['path'] or f['path']=='出纳结算设置.json' for f in files)
    (root/'logs').mkdir()
    (root/'出纳结算设置.json').write_bytes(b'keep-settings')
    journal=root/'logs/结算操作记录.json'; journal.write_bytes(b'keep-journal')
    browser=root/'ms-playwright（勿动）/chromium-1161/chrome-win/chrome.exe'
    browser.parent.mkdir(parents=True); browser.write_bytes(b'keep-existing-browser')
    shutil.copyfile(old,root/identity.EXE_NAME)
    lock=SingleInstanceLock(journal); lock.acquire(); lock.release()
    process=subprocess.Popen([sys.executable,'-c','pass']); pid=process.pid; process.wait(timeout=10)
    plan={'app_id':identity.APP_ID,'app_dir':str(root),'stage_dir':str(job/'stage'),'backup_dir':str(job/'backup'),
        'exe_name':identity.EXE_NAME,'pid':pid,'version':target,'files':files,'lock_path':str(lock.lock_path)}
    planfile=job/'plan.json'; planfile.write_text(json.dumps(plan),encoding='utf-8-sig')
    helper=job/'install.ps1'; helper.write_bytes(r.extract('update_install.ps1'))
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper),
        '-PlanPath',str(planfile),'-PlanSha256',updater.sha256_file(planfile),'-NoRestart'],
        env=app_updater.clean_child_environment(),capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    state=json.loads((root/'.updates/last-result.json').read_text(encoding='utf-8-sig'))
    assert state['status']=='success',state
    assert updater.sha256_file(root/identity.EXE_NAME)==next(f['sha256'] for f in files if f['path']==identity.EXE_NAME)
    assert browser.read_bytes()==b'keep-existing-browser'
    assert (root/'出纳结算设置.json').read_bytes()==b'keep-settings'
    assert journal.read_bytes()==b'keep-journal'
    full_size=(project/'NCC出纳结算工具_V1.9.4_完整版.zip').stat().st_size
    report={'oldClient':'1.9.4','target':target,'signedManifestAccepted':True,'installSucceeded':True,
        'browserPreserved':True,'settingsAndJournalPreserved':True,'stagedFiles':len(files),
        'updateBytes':archive.stat().st_size,'reductionPercent':round(100*(1-archive.stat().st_size/full_size),1)}
    (project/'build'/('light_update_validation_'+target+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
