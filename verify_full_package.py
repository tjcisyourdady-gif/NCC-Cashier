"""完整包检查：仅启动无窗口浏览器显示本地内容，不访问 NCC 或文档。"""
import json
from pathlib import Path
import sys
from release_identity import APP_VERSION
from unittest.mock import patch
import zipfile
from PyInstaller.archive.readers import CArchiveReader
from playwright.sync_api import sync_playwright
import ncc_browser
from settlement_core import Settings, load_settings

project = Path(__file__).resolve().parent
version=sys.argv[1] if len(sys.argv)>1 else 'V'+APP_VERSION
folder = project / 'release' / ('NCC出纳结算工具_'+version+'_完整版')
zip_path = project / ('NCC出纳结算工具_'+version+'_完整版.zip')
root_name = folder.name + '/'
with zipfile.ZipFile(str(zip_path)) as archive:
    assert archive.testzip() is None, 'ZIP CRC check failed'
    entries = set(archive.namelist())
    required = ['NCC出纳结算工具.exe', '调试读取文档.bat', '出纳结算设置.json',
                '完整包开始使用.txt', '使用说明.txt', '源码/skip_list_debug.py',
                '源码/settlement_gui.py', 'ms-playwright（勿动）/chromium-1161/chrome-win/chrome.exe']
    for name in required:
        assert root_name + name in entries, 'Missing: ' + name
    files = [path for path in folder.rglob('*') if path.is_file()]
    for path in files:
        entry = root_name + path.relative_to(folder).as_posix()
        assert entry in entries, 'File omitted from ZIP: ' + entry
        assert archive.getinfo(entry).file_size == path.stat().st_size
    assert not any('/logs/' in name for name in entries), 'Old logs must not be shipped'

expected=Settings()
if '--public' in sys.argv: expected.skip_list_url=''
assert load_settings(folder / '出纳结算设置.json') == expected
runtime = CArchiveReader(str(folder / 'NCC出纳结算工具.exe'))
runtime_names = {name.replace('\\','/').lower() for name in runtime.toc}
assert 'python38.dll' in runtime_names
if version.startswith('V1.9'):
    assert 'update_install.ps1' in runtime_names
    assert 'update_public_key.json' in runtime_names
    for name in ('app_updater.py','update_dialog.py','update_public_key.json','release_identity.py'):
        assert (folder/'源码'/name).is_file()
    assert not any('.release-secrets' in entry or '/.updates/' in entry for entry in entries)
    if '--public' in sys.argv:
        assert "DEFAULT_SKIP_LIST_URL = ''" in (folder/'源码'/'skip_list.py').read_text(encoding='utf-8-sig')
assert any(name.startswith('playwright/driver/') and name.endswith('node.exe') for name in runtime_names)
with patch.object(ncc_browser, 'RECEIPT_BROWSER_PATH', str(folder / 'absent-old-browser.exe')):
    browser_path = ncc_browser.find_browser(folder)
assert Path(browser_path).resolve() == (folder / required[-1]).resolve()
with sync_playwright() as engine:
    browser = engine.chromium.launch(executable_path=browser_path, headless=True)
    try:
        context = browser.new_context()
        context.route('**/*', lambda route:route.abort())
        page = context.new_page()
        page.set_content('<title>Full package verification</title><p>Local only</p>')
        assert page.title() == 'Full package verification'
    finally:
        browser.close()
print(json.dumps({'zipIntegrity':'OK', 'files':len(files), 'defaultSettings':'OK',
    'bundledPythonAndDriver':'OK', 'independentBundledBrowser':'OK', 'headlessLocalPage':'OK',
    'zipBytes':zip_path.stat().st_size}))
