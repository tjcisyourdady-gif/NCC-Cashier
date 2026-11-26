"""源码调试入口：只打开文档、等待人工登录并读取清单，绝不进入 NCC。"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from ncc_background import LAUNCH_ARGS, BackgroundActivityGuard
from ncc_browser import find_browser
from settlement_core import Control, StopRequested, load_settings
from skip_list import DingTalkSkipList, SHEET_NAME, latest_preview_url
from urllib.parse import parse_qs, urlsplit
from skip_list_results import SkipListResultStore


def run_debug(settings, control, emit, base_dir):
    engine = browser = guard = None
    previous_hook = control.activity_hook
    store = SkipListResultStore(base_dir, 'debug')
    attempt = snapshot = None
    try:
        control.checkpoint()
        query = parse_qs(urlsplit(latest_preview_url(settings.skip_list_url)).query)
        attempt = store.begin(None, SHEET_NAME, query.get('fileId', query.get('cloudSpaceDentryId'))[0])
        emit('skip_result', attempt)
        engine = sync_playwright().start()
        browser = engine.chromium.launch(executable_path=find_browser(base_dir, settings.browser_path),
            headless=False, args=list(LAUNCH_ARGS), timeout=int(settings.browser_start_timeout_seconds * 1000))
        context = browser.new_context(no_viewport=True)
        context.set_default_timeout(int(settings.operation_timeout_seconds * 1000))
        context.set_default_navigation_timeout(int(settings.navigation_timeout_seconds * 1000))
        guard = BackgroundActivityGuard(emit, interval=settings.background_refresh_seconds)
        control.activity_hook = guard.refresh
        reader = DingTalkSkipList(context, settings, control, emit,
            on_page_created=lambda page:guard.watch(context, page),
            result_store=store)
        emit('status', '调试仅读取文档：请手动登录钉钉，不会进入 NCC。')
        reader.refresh(None)
        snapshot = reader.last_snapshot
        emit('log', '调试读取完成；可点击“核对读取结果”查看编号和原单元格。')
        return snapshot
    except BaseException:
        if attempt is not None and snapshot is None:
            emit('skip_result', store.failure(attempt, control.stop_event.is_set()))
        raise
    finally:
        control.activity_hook = previous_hook
        try:
            if guard is not None:
                guard.close()
        finally:
            try:
                if browser is not None:
                    browser.close()
            finally:
                if engine is not None:
                    engine.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', default=str(Path(__file__).resolve().parent))
    args = parser.parse_args()
    base = Path(args.base_dir)
    settings = load_settings(base / '出纳结算设置.json')
    def emit(kind, text):
        if kind == 'skip_result':
            print('[核对结果] {}；编号数 {}；文件 {}'.format(text['status'], text.get('uniqueCount', '未确认'), text['jsonPath']))
        else:
            print('[{}] {}'.format(kind, text), flush=True)
    try:
        snapshot = run_debug(settings, Control(), emit, base)
        for record in snapshot['records']:
            print('{}\t{}'.format(record['cell'], record['billNumber']))
        print('Excel 可打开的 CSV：' + snapshot['csvPath'])
        return 0
    except (StopRequested, KeyboardInterrupt):
        print('已停止读取。')
        return 130
    except Exception as exc:
        print('读取失败：' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
