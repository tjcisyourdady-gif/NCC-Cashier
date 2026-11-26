import json,shutil,subprocess,time
from pathlib import Path

project=Path(__file__).resolve().parents[1]
binary=project/'build/harness_dist/NCC出纳结算工具.exe'
stamp=str(time.time_ns())
results=[]
for case,argument in [('成功重启','--parent'),('助手启动失败','--failed-helper')]:
    folder=project/'build'/('完整更新测试 '+stamp)/case
    stage=folder/'.updates/fixture/stage'
    stage.mkdir(parents=True)
    (folder/'logs').mkdir()
    (folder/'出纳结算设置.json').write_bytes(b'keep-settings')
    (folder/'logs/结算操作记录.json').write_bytes(b'keep-journal')
    exe=folder/binary.name
    shutil.copyfile(binary,exe)
    shutil.copyfile(binary,stage/binary.name)
    parent=subprocess.Popen([str(exe),argument],creationflags=subprocess.CREATE_NO_WINDOW)
    parent.wait(timeout=40)
    assert parent.returncode==0
    outcome=json.loads((folder/'parent-result.json').read_text(encoding='utf-8'))
    if argument=='--parent':
        deadline=time.monotonic()+30
        while not (folder/'restarted.json').exists() and time.monotonic()<deadline: time.sleep(.1)
        assert (folder/'restarted.json').is_file(),list(folder.rglob('*.log'))
        after=json.loads((folder/'restarted.json').read_text(encoding='utf-8'))
        before=json.loads((folder/'before.json').read_text(encoding='utf-8'))
        assert after['extractExists'] and after['extract']!=before['extract']
        assert (folder/'.updates/fixture/backup'/binary.name).exists()
        result=json.loads((folder/'.updates/last-result.json').read_text(encoding='utf-8-sig'))
        assert result['status']=='success' and result['restarted_pid']
        results.append({'case':case,'helperStarted':True,'selfReplacement':True,'restarted':True,'freshExtraction':True})
    else:
        assert outcome.get('failed') and outcome['state']['status']=='ready'
        assert not (folder/'restarted.json').exists()
        assert not (folder/'.updates/fixture/backup').exists()
        assert (folder/'.updates/fixture/installer.log').exists()
        results.append({'case':case,'failureReported':True,'originalPreserved':True,'logCreated':True})
    assert (folder/'出纳结算设置.json').read_bytes()==b'keep-settings'
    assert (folder/'logs/结算操作记录.json').read_bytes()==b'keep-journal'
report={'frozenWindowedExe':True,'results':results,'settingsAndJournalPreserved':True}
(project/'build/frozen_update_test_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
