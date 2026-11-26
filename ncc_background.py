"""沿用收款结算单的 Chromium 后台保护，全程在自动化工作线程内调用。"""
import time
from pathlib import Path


BROWSER_RELATIVE_PATH = 'ms-playwright（勿动）/chromium-1161/chrome-win/chrome.exe'
RECEIPT_BROWSER_PATH = str(Path.home() / 'Desktop' / '孵化平台' / '填写工具' /
                           '收款结算填写工具' / '收款结算填写工具' / BROWSER_RELATIVE_PATH)
LAUNCH_ARGS = [
    '--start-fullscreen',
    '--start-maximized',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-background-timer-throttling',
    '--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling',
]


class BackgroundActivityGuard:
    def __init__(self, emit, interval=2.0):
        self.emit = emit
        self.interval = interval
        self.sessions = []
        self.pages = []
        self.last_refresh = None

    @staticmethod
    def _activate(session):
        session.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
        try:
            session.send('Page.setWebLifecycleState', {'state': 'active'})
        except Exception:
            # 与旧收款工具一致：旧内置浏览器不支持该实验协议时仍保留焦点模拟。
            pass

    def watch(self, context, page):
        if any(existing is page for existing in self.pages):
            return
        session = None
        try:
            session = context.new_cdp_session(page)
            self._activate(session)
            self.sessions.append(session)
            self.pages.append(page)
            self.emit('log', '已启用与收款结算单相同的最小化和后台运行保护。')
        except Exception as exc:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
            self.emit('warning', '页面后台活动保护未启用，将继续使用浏览器后台参数：' + str(exc))

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and self.last_refresh is not None and now - self.last_refresh < self.interval:
            return
        self.last_refresh = now
        alive_sessions, alive_pages = [], []
        for session, page in zip(self.sessions, self.pages):
            try:
                self._activate(session)
                alive_sessions.append(session)
                alive_pages.append(page)
            except Exception:
                try:
                    session.detach()
                except Exception:
                    pass
        self.sessions, self.pages = alive_sessions, alive_pages

    def close(self):
        sessions, self.sessions, self.pages = self.sessions, [], []
        for session in sessions:
            try:
                session.detach()
            except Exception:
                pass
