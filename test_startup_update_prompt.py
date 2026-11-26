"""启动提示、更新/忽略选择及自动下载安装；使用隐藏窗口和模拟版本，不访问 NCC。"""
from pathlib import Path
import tempfile,threading,time,tkinter as tk,unittest
from unittest.mock import Mock,patch
from settlement_gui import SettlementApp


class StartupPromptTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=tk.Tk(); self.root.withdraw()
        self.app=SettlementApp(self.root,self.folder.name,auto_update=False)
        self.app._startup_prompt_handled=False

    def tearDown(self):
        self.app._destroy(); self.folder.cleanup()

    def available(self):
        return self.app.updater._state(status='available',current_version='1.9.6',latest_version='1.9.7',notes='测试版本说明')

    def offer(self):
        data=self.available()
        self.app._handle_startup_update_result(data)
        with patch.object(self.app,'_can_show_startup_prompt',return_value=True):
            self.app._maybe_show_startup_prompt()
        return self.app.startup_update_dialog

    def drain_until(self,predicate):
        deadline=time.monotonic()+2
        while not predicate() and time.monotonic()<deadline:
            self.app._drain_events(); time.sleep(.01)
        self.assertTrue(predicate())

    def test_latest_version_and_network_failure_do_not_create_prompt(self):
        for status in ('up_to_date','error'):
            self.app._startup_prompt_handled=False
            self.app._handle_startup_update_result(self.app.updater._state(status=status))
            self.app._maybe_show_startup_prompt()
            self.assertIsNone(self.app.startup_update_dialog)
            self.assertTrue(self.app._startup_prompt_handled)

    def test_ignore_and_close_button_never_download_or_install_and_prompt_only_once(self):
        for close_with_x in (False,True):
            self.app._startup_prompt_handled=False
            with patch.object(self.app.updater,'download') as download,patch.object(self.app.updater,'start_installer') as install:
                prompt=self.offer()
                self.assertEqual(str(prompt.update_button['text']),'更新')
                self.assertEqual(str(prompt.ignore_button['text']),'忽略')
                if close_with_x: prompt.tk.call(prompt.protocol('WM_DELETE_WINDOW'))
                else: prompt.ignore_button.invoke()
                self.assertIsNone(self.app.startup_update_dialog)
                self.assertTrue(self.app._startup_prompt_handled)
                self.app._handle_startup_update_result(self.available())
                with patch.object(self.app,'_can_show_startup_prompt',return_value=True): self.app._maybe_show_startup_prompt()
                self.assertIsNone(self.app.startup_update_dialog)
                download.assert_not_called(); install.assert_not_called()

    def test_update_button_starts_opted_in_flow(self):
        with patch.object(self.app,'start_prompted_update') as update:
            prompt=self.offer(); prompt.update_button.invoke()
            update.assert_called_once_with()
            self.assertIsNone(self.app.startup_update_dialog)

    def test_background_or_minimized_window_defers_prompt(self):
        self.app._handle_startup_update_result(self.available())
        self.app._maybe_show_startup_prompt()
        self.assertIsNone(self.app.startup_update_dialog)
        self.assertIsNotNone(self.app._startup_pending_update)
        with patch.object(self.app,'_can_show_startup_prompt',return_value=True): self.app._maybe_show_startup_prompt()
        self.assertIsNotNone(self.app.startup_update_dialog)

    def test_real_prompt_gate_excludes_busy_minimized_unfocused_and_modal_windows(self):
        with patch.object(self.root,'state',return_value='normal'),patch.object(self.root,'winfo_viewable',return_value=True), \
                patch.object(self.root,'focus_displayof',return_value=self.app.start_button),patch.object(self.root,'grab_current',return_value=None):
            self.assertTrue(self.app._can_show_startup_prompt())
            self.app.worker=Mock(); self.app.worker.is_alive.return_value=True
            self.assertFalse(self.app._can_show_startup_prompt())
            self.app.worker=None; self.app._update_installing=True
            self.assertFalse(self.app._can_show_startup_prompt())
            self.app._update_installing=False
            with patch.object(self.root,'state',return_value='iconic'): self.assertFalse(self.app._can_show_startup_prompt())
            with patch.object(self.root,'focus_displayof',return_value=None): self.assertFalse(self.app._can_show_startup_prompt())
            with patch.object(self.root,'grab_current',return_value=self.app.start_button): self.assertFalse(self.app._can_show_startup_prompt())

    def test_manual_update_panel_consumes_startup_prompt(self):
        self.app._handle_startup_update_result(self.available())
        self.app.open_update_dialog()
        self.assertTrue(self.app._startup_prompt_handled)
        self.assertIsNone(self.app._startup_pending_update)
        self.assertIsNotNone(self.app.update_dialog)

    def test_successful_download_requests_install_while_preventing_new_tasks(self):
        self.available(); completed=threading.Event()
        def download():
            completed.set()
            return self.app.updater._state(status='ready')
        with patch.object(self.app.updater,'download',side_effect=download),patch.object(self.app,'install_update') as install:
            self.app.start_prompted_update()
            self.assertTrue(self.app._update_installing)
            self.assertEqual(str(self.app.start_button['state']),'disabled')
            with patch.object(self.app,'current_settings') as settings:
                self.app.start(); self.app.start_debug(); settings.assert_not_called()
            self.app._set_running(False)
            self.assertEqual(str(self.app.start_button['state']),'disabled')
            self.assertTrue(completed.wait(1))
            self.drain_until(lambda:install.called)
            install.assert_called_once_with()

    def test_download_failure_keeps_program_open_and_restores_task_buttons(self):
        self.available()
        with patch.object(self.app.updater,'download',side_effect=lambda:self.app.updater._state(status='error',message='下载失败')), \
                patch.object(self.app,'install_update') as install,patch.object(self.app,'_destroy') as destroy:
            self.app.start_prompted_update()
            self.drain_until(lambda:not self.app._update_installing)
            self.assertFalse(self.app._auto_update_requested)
            self.assertEqual(str(self.app.start_button['state']),'normal')
            self.assertTrue(self.root.winfo_exists())
            install.assert_not_called(); destroy.assert_not_called()

    def test_running_task_prevents_one_click_update(self):
        self.available(); self.app.worker=Mock(); self.app.worker.is_alive.return_value=True
        with patch.object(self.app.updater,'download') as download:
            self.app.start_prompted_update()
            self.assertFalse(self.app._update_installing)
            download.assert_not_called()
        self.app.worker=None

    def test_new_process_can_prompt_again_after_previous_process_ignored(self):
        prompt=self.offer(); prompt.ignore()
        other_root=tk.Toplevel(self.root); other_root.withdraw()
        other=SettlementApp(other_root,self.folder.name,auto_update=True)
        try: self.assertFalse(other._startup_prompt_handled)
        finally: other._destroy()


if __name__=='__main__': unittest.main()
