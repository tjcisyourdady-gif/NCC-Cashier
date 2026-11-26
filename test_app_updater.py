"""离线验证更新签名、安装边界、失败回滚以及启动检查；不连接 NCC。"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import zipfile

import app_updater as upd
import release_identity as identity
from ncc_safety import SingleInstanceLock

PROJECT=Path(__file__).resolve().parent


def manifest(blob=b'zip',version='1.10.0'):
    return dict(schema=1,app_id=identity.APP_ID,version=version,notes='测试更新',full={
        'url':'https://github.com/{}/{}/releases/download/{}{}/NCC_full.zip'.format(
            identity.GITHUB_OWNER,identity.GITHUB_REPO,identity.TAG_PREFIX,version),
        'sha256':hashlib.sha256(blob).hexdigest(),'size_bytes':len(blob)})


def package(entries=None):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        for name,body in entries or [(identity.EXE_NAME,b'new-executable')]:
            z.writestr(name,body)
    return stream.getvalue()


class ManifestTests(unittest.TestCase):
    def test_independent_client_rejects_shared_repo_and_bridge_context_restores_identity(self):
        from prepare_update_release import publication_channel
        original_repo=identity.GITHUB_REPO
        original_manifest=identity.MANIFEST_PATH
        self.assertEqual(original_repo,'NCC-Cashier')
        self.assertEqual(original_manifest,'latest.json')
        standalone=manifest()
        legacy=dict(standalone,full=dict(standalone['full'],url=standalone['full']['url'].replace('/NCC-Cashier/','/YuiDesk/')))
        with self.assertRaises(upd.UpdateError): upd.validate_manifest(legacy)
        with publication_channel(True):
            self.assertEqual(identity.GITHUB_REPO,'YuiDesk')
            self.assertEqual(identity.MANIFEST_PATH,'updates/ncc-cashier/latest.json')
            self.assertEqual(upd.validate_manifest(legacy)['version'],'1.10.0')
            with self.assertRaises(upd.UpdateError): upd.validate_manifest(standalone)
        self.assertEqual(identity.GITHUB_REPO,original_repo)
        self.assertEqual(identity.MANIFEST_PATH,original_manifest)

    def test_versions_are_numeric_and_reject_invalid(self):
        self.assertGreater(upd.version_tuple('1.10.0'),upd.version_tuple('1.9'))
        for value in ['1','1.x','1.2-beta','1.2.3.4.5','../2']:
            with self.assertRaises(upd.UpdateError): upd.version_tuple(value)

    def test_channel_url_and_hash_are_checked(self):
        good=manifest()
        self.assertEqual(upd.validate_manifest(good)['version'],'1.10.0')
        for key,value in [('app_id','yuidesk'),('schema',0)]:
            bad=dict(good,**{key:value})
            with self.assertRaises(upd.UpdateError): upd.validate_manifest(bad)
        original=good['full']['url']
        for url in [original.replace('https:','http:'),original.replace('/'+identity.GITHUB_REPO+'/','/other/'),
                    original.replace('NCC_full.zip','%2e%2e%2ffile.zip'),original+'?x=1',
                    original.replace('ncc-cashier-v1.10.0','v1.10.0')]:
            with self.subTest(url=url): self.assertFalse(upd.trusted_asset_url(url,'1.10.0'))
        for size in [True,-1,upd.MAX_ARCHIVE+1]:
            with self.assertRaises(upd.UpdateError): upd.validate_manifest(dict(good,full=dict(good['full'],size_bytes=size)))

    def test_duplicate_fields_are_rejected(self):
        with self.assertRaises(upd.UpdateError): upd.json_bytes(b'{"version":1,"version":2}')

    def test_unsigned_or_changed_manifest_is_rejected(self):
        for data in [manifest(),{'key_id':identity.SIGNING_KEY_ID,'payload':'e30=','signature':'AA=='}]:
            with self.assertRaises(upd.UpdateError): upd.verify_envelope(data)

    @unittest.skipUnless(os.name=='nt','Windows signing tool')
    def test_dotnet_signature_matches_python_verifier_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'payload.json'; dst=Path(folder)/'signed.json'
            src.write_text(json.dumps(manifest()),encoding='utf-8')
            pub=Path(folder)/'public.json'
            common=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(PROJECT/'update_keys.ps1'),
                    '-KeyDirectory',folder,'-PublicKeyPath',str(pub)]
            initialized=subprocess.run(common+['-Initialize'],capture_output=True,timeout=20)
            self.assertEqual(initialized.returncode,0,initialized.stderr.decode(errors='replace'))
            result=subprocess.run(common+['-PayloadPath',str(src),'-OutputPath',str(dst)],capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            signed=upd.json_bytes(dst.read_bytes())
            key=upd.json_bytes(pub.read_bytes())
            with patch.object(identity,'SIGNING_KEY_ID',key['key_id']),patch.object(identity,'SIGNING_MODULUS',key['modulus']):
                self.assertEqual(upd.verify_envelope(signed)['version'],'1.10.0')
                signed['payload']=base64.b64encode(json.dumps(manifest(version='9.0.0')).encode()).decode()
                with self.assertRaises(upd.UpdateError): upd.verify_envelope(signed)

    def test_mirrors_are_first_and_failure_falls_back(self):
        response=io.BytesIO(b'{}')
        with patch.object(upd,'open_url',side_effect=[OSError('offline'),response]) as request, \
                patch.object(upd,'verify_envelope',return_value=upd.validate_manifest(manifest())):
            self.assertEqual(upd.fetch_manifest()['source'],'国内直连通道')
        self.assertTrue(request.call_args_list[0].args[0].startswith(identity.DOWNLOAD_MIRRORS[0]))
        self.assertTrue(request.call_args_list[1].args[0].startswith(identity.DOWNLOAD_MIRRORS[1]))

    def test_untrusted_redirect_cannot_downgrade_tls_or_change_host(self):
        handler=upd.SafeRedirect()
        for url in ['http://ghfast.top/a','https://evil.example/a','https://github.com:443/a',
                    'https://user@github.com/a']:
            with self.assertRaises(upd.UpdateError): handler.redirect_request(None,None,302,'',{},url)


class ArchiveTests(unittest.TestCase):
    def stage(self,folder,blob):
        path=Path(folder)/'package.zip'; path.write_bytes(blob)
        return upd.stage_package(path,Path(folder)/'stage')

    def test_full_package_preserves_settings_and_updates_source_and_browser(self):
        entries=[('NCC/'+identity.EXE_NAME,b'exe'),('NCC/出纳结算设置.json',b'ignored'),
                 ('NCC/源码/settlement_gui.py',b'source'),('NCC/README.md',b'help'),
                 ('NCC/ms-playwright（勿动）/chromium-1161/chrome-win/chrome.exe',b'browser')]
        with tempfile.TemporaryDirectory() as folder:
            files=self.stage(folder,package(entries))
            self.assertEqual(len(files),4)
            self.assertFalse((Path(folder)/'stage'/'出纳结算设置.json').exists())
            self.assertTrue((Path(folder)/'stage'/'源码'/'settlement_gui.py').is_file())

    def test_hostile_windows_paths_are_rejected_before_extracting(self):
        for name in ['../x','dir/../x','C:/x','/x','\\\\server\\x','dir/file:ads','CON.txt',
                     'dir/NUL','dir/a.','dir/a ','dir/a?','dir//x','dir/\x01x']:
            with self.subTest(path=name),tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(upd.UpdateError): self.stage(folder,package([(identity.EXE_NAME,b'exe'),(name,b'bad')]))
                self.assertFalse((Path(folder)/'stage').exists())

    def test_duplicate_symlink_unknown_files_and_expansion_limit(self):
        for entries in [[(identity.EXE_NAME,b'exe'),(identity.EXE_NAME.upper(),b'other')],
                        [(identity.EXE_NAME,b'exe'),('logs/结算操作记录.json',b'bad')],
                        [(identity.EXE_NAME,b'exe'),('源码/private.key',b'bad')]]:
            with tempfile.TemporaryDirectory() as folder,self.assertRaises(upd.UpdateError): self.stage(folder,package(entries))
        info=zipfile.ZipInfo('ms-playwright（勿动）/link'); info.create_system=3
        info.external_attr=(stat.S_IFLNK|0o777)<<16
        with tempfile.TemporaryDirectory() as folder,self.assertRaises(upd.UpdateError):
            self.stage(folder,package([(identity.EXE_NAME,b'exe'),(info,b'target')]))
        with tempfile.TemporaryDirectory() as folder,patch.object(upd,'MAX_EXPANDED',1),self.assertRaises(upd.UpdateError):
            self.stage(folder,package())

    def test_target_cannot_escape_or_overwrite_data(self):
        with tempfile.TemporaryDirectory() as folder:
            for path in ['../x','logs/a','出纳结算设置.json','源码/private.key',identity.EXE_NAME+'/x']:
                with self.assertRaises(upd.UpdateError): upd.validate_target(folder,path)


class ManagerTests(unittest.TestCase):
    def test_check_resets_stale_ready_and_reports_failure_without_touching_app(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=upd.UpdateManager(folder); manager.ready={'old':1}
            with patch.object(upd,'fetch_manifest',side_effect=OSError('offline')):
                self.assertEqual(manager.check()['status'],'error')
            self.assertIsNone(manager.ready)
            self.assertFalse(manager.root.exists())
            with patch.object(upd,'fetch_manifest',return_value=upd.validate_manifest(manifest(version=identity.APP_VERSION))):
                self.assertEqual(manager.check()['status'],'up_to_date')

    def test_failed_hash_never_stages_and_download_falls_back_to_second_mirror(self):
        blob=package()
        with tempfile.TemporaryDirectory() as folder:
            manager=upd.UpdateManager(folder); manager.manifest=upd.validate_manifest(manifest(blob))
            with patch.object(upd,'open_url',side_effect=[io.BytesIO(b'bad'),io.BytesIO(blob)]) as request:
                self.assertEqual(manager.download()['status'],'ready')
            self.assertEqual(request.call_count,2)
            self.assertIn('国内直连通道 2',manager.status()['source'])
            plan=manager.installation_plan()
            self.assertEqual(plan['app_id'],identity.APP_ID)
            (manager.ready['stage']/identity.EXE_NAME).write_bytes(b'changed')
            with self.assertRaises(upd.UpdateError): manager.installation_plan()

    def test_all_corrupt_downloads_preserve_existing_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/identity.EXE_NAME; exe.write_bytes(b'old')
            manager=upd.UpdateManager(folder); manager.manifest=upd.validate_manifest(manifest(package()))
            with patch.object(upd,'open_url',side_effect=lambda *_:io.BytesIO(b'bad')):
                self.assertEqual(manager.download()['status'],'error')
            self.assertEqual(exe.read_bytes(),b'old'); self.assertIsNone(manager.ready)

    def test_busy_or_source_execution_cannot_install(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=upd.UpdateManager(folder)
            with self.assertRaisesRegex(upd.UpdateError,'停止'): manager.start_installer(lambda:True)
            with self.assertRaisesRegex(upd.UpdateError,'源码'): manager.start_installer(lambda:False)


@unittest.skipUnless(os.name=='nt','Windows installer')
class InstallerTests(unittest.TestCase):
    def prepare(self,folder):
        app=Path(folder); job=app/'.updates'/'test'; stage=job/'stage'; stage.mkdir(parents=True)
        (app/'logs').mkdir()
        files=[]
        for name in [identity.EXE_NAME,'使用说明.txt']:
            (app/name).write_bytes(b'old-'+name.encode())
            (stage/name).write_bytes(b'new-'+name.encode())
            files.append({'path':name,'sha256':upd.sha256_file(stage/name)})
        (app/'出纳结算设置.json').write_bytes(b'keep-settings')
        (app/'logs'/'结算操作记录.json').write_bytes(b'keep-journal')
        child=subprocess.run([sys.executable,'-c','pass'],capture_output=True,timeout=10)
        # An already exited child guarantees the helper will not wait on our test runner.
        process=subprocess.Popen([sys.executable,'-c','pass']); pid=process.pid; process.wait(timeout=10)
        lock=SingleInstanceLock(app/'logs'/'结算操作记录.json')
        lock.acquire(); lock.release()
        plan=dict(app_id=identity.APP_ID,app_dir=str(app),stage_dir=str(stage),backup_dir=str(job/'backup'),
            pid=pid,exe_name=identity.EXE_NAME,version='1.10.0',files=files,lock_path=str(lock.lock_path))
        path=job/'plan.json'; path.write_text(json.dumps(plan),encoding='utf-8-sig')
        return app,path,lock

    def install(self,path):
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(PROJECT/'update_install.ps1'),
            '-PlanPath',str(path),'-PlanSha256',upd.sha256_file(path),'-NoRestart'],capture_output=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        return upd.json_bytes((path.parents[1]/'last-result.json').read_bytes())

    def test_success_installs_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as folder:
            app,path,_=self.prepare(folder)
            self.assertEqual(self.install(path)['status'],'success')
            self.assertTrue((app/identity.EXE_NAME).read_bytes().startswith(b'new-'))
            self.assertEqual((app/'出纳结算设置.json').read_bytes(),b'keep-settings')
            self.assertEqual((app/'logs'/'结算操作记录.json').read_bytes(),b'keep-journal')

    def test_running_financial_job_prevents_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            app,path,lock=self.prepare(folder)
            with lock:
                self.assertEqual(self.install(path)['status'],'failed')
            self.assertTrue((app/identity.EXE_NAME).read_bytes().startswith(b'old-'))

    def test_copy_failure_after_first_replacement_rolls_back(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            app,path,_=self.prepare(folder)
            # Permit backup reads; deny the second replacement and exercise real rollback.
            handle=create(str(app/'使用说明.txt'),0x80000000,1,None,3,0,None)
            self.assertNotEqual(handle,wintypes.HANDLE(-1).value)
            try: result=self.install(path)
            finally: kernel.CloseHandle(handle)
            self.assertEqual(result['status'],'failed')
            self.assertTrue(result['rollback'])
            self.assertEqual(result['applied_count'],1)
            self.assertTrue((app/identity.EXE_NAME).read_bytes().startswith(b'old-'))


class StartupGuiTests(unittest.TestCase):
    def test_public_blank_configuration_loads_but_cannot_start_financial_task(self):
        from settlement_core import Settings,load_settings,resolve_date_range
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as folder:
            data=asdict(Settings(skip_list_url=''))
            path=Path(folder)/'出纳结算设置.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            settings=load_settings(path)
            self.assertEqual(settings.skip_list_url,'')
            with self.assertRaisesRegex(ValueError,'网址'): resolve_date_range(settings)

    def test_each_app_open_schedules_a_background_check_without_popup(self):
        from settlement_gui import SettlementApp
        for _ in range(2):
            with tempfile.TemporaryDirectory() as folder,patch('settlement_gui.threading.Thread') as thread:
                root=tk.Tk(); root.withdraw()
                app=SettlementApp(root,folder)
                try:
                    self.assertIsNotNone(app._startup_update_id)
                    root.after_cancel(app._startup_update_id)
                    app._startup_check_update()
                    self.assertEqual(thread.call_args.kwargs['target'],app.updater.check)
                    self.assertTrue(thread.call_args.kwargs['daemon'])
                    thread.return_value.start.assert_called_once_with()
                    self.assertIsNone(app.update_dialog)
                finally: app._destroy()

    def test_install_gate_blocks_running_job_and_future_starts(self):
        from settlement_gui import SettlementApp
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk(); root.withdraw(); app=SettlementApp(root,folder,auto_update=False)
            try:
                app.worker=Mock(); app.worker.is_alive.return_value=True
                with patch.object(app.updater,'start_installer') as install:
                    app.install_update(); install.assert_not_called()
                app.worker=None; app._update_installing=True
                with patch.object(app,'current_settings') as settings:
                    app.start(); app.start_debug(); settings.assert_not_called()
            finally: app._destroy()


if __name__=='__main__': unittest.main()
