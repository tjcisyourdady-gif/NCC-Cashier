"""窗口版 onefile 启动、退出、替换自身、自动重启的真实链路；不创建窗口。"""
import json,os,sys,traceback
from pathlib import Path
import app_updater
import release_identity

root=Path(sys.executable).resolve().parent
try:
    if '--parent' in sys.argv or '--failed-helper' in sys.argv:
        job=root/'.updates'/'fixture'
        manager=app_updater.UpdateManager(root)
        exe=job/'stage'/release_identity.EXE_NAME
        manager.ready={'job':job,'stage':job/'stage','files':[{'path':exe.name,'sha256':app_updater.sha256_file(exe)}],'version':'99.0.0'}
        manager._state(status='ready')
        if '--failed-helper' in sys.argv:
            (Path(sys._MEIPASS)/'update_install.ps1').write_text('exit 0\n',encoding='utf-8-sig')
        (root/'before.json').write_text(json.dumps({'extract':sys._MEIPASS,'pyiEnvironment':{k:v for k,v in os.environ.items() if k.startswith('_PYI') or k=='_MEIPASS2'}}),encoding='utf-8')
        try: result=manager.start_installer(lambda:False)
        except Exception as exc:
            result={'failed':str(exc),'state':manager.status(),'targetUnchanged':app_updater.sha256_file(root/release_identity.EXE_NAME)==app_updater.sha256_file(exe)}
        (root/'parent-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    else:
        (root/'restarted.json').write_text(json.dumps({'extract':sys._MEIPASS,'extractExists':Path(sys._MEIPASS).is_dir(),'path':str(sys.executable)}),encoding='utf-8')
except BaseException:
    (root/'harness-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
    sys.exit(1)
