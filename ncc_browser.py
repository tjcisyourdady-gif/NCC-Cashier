"""NCC 结算（出纳）的浏览器适配；未识别的状态会停止，不猜测财务操作。"""
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from settlement_core import LOGIN_URL, PageSnapshot, Row, StopRequested, eligible, parse_date
from ncc_safety import SingleInstanceLock, is_settlement_success
from ncc_background import BROWSER_RELATIVE_PATH, RECEIPT_BROWSER_PATH, LAUNCH_ARGS, BackgroundActivityGuard
from skip_list import DingTalkSkipList, bill_number
from skip_list_results import SkipListResultStore


class NccUiError(RuntimeError):
    pass


class CommitUncertain(NccUiError):
    pass


def find_browser(base_dir, configured=''):
    base = Path(base_dir)
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise NccUiError('所选浏览器不存在：' + str(path))
        return str(path)
    receipt_browser = Path(RECEIPT_BROWSER_PATH)
    if receipt_browser.is_file():
        return str(receipt_browser)
    roots = [base, base / 'dist',
             base.parent / 'v7_package_stage_current_full' / '收款结算单填写工具V7']
    for root in roots:
        path = root / BROWSER_RELATIVE_PATH
        if path.is_file():
            return str(path)
    raise NccUiError('未找到收款结算单同款内置浏览器：%s。请保持随包的“ms-playwright（勿动）”目录完整，或在界面选择原收款工具的 chrome.exe。' % (base / BROWSER_RELATIVE_PATH))


class CommitJournal:
    """写前日志：无法确认的点击跨重启仍会阻止重试。"""
    def __init__(self, path):
        self.path = Path(path)
        self.entries = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if not isinstance(self.entries, dict):
            raise NccUiError('结算记录文件损坏，请先人工核对：' + str(self.path))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.entries, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(self.path))

    def pending(self):
        return [key for key, value in self.entries.items() if value.get('state') == 'pending']

    def record(self, key, state, **details):
        self.entries[key] = dict(state=state, time=datetime.now().isoformat(timespec='seconds'), **details)
        self._save()


class NccBrowserAdapter:
    def __init__(self, settings, control, emit, base_dir):
        self.settings, self.control, self.emit = settings, control, emit
        self.base_dir = Path(base_dir)
        resource = Path(getattr(sys, '_MEIPASS', str(Path(__file__).parent)))
        self.script = (resource / 'ncc_dom.js').read_text(encoding='utf-8')
        journal_path = self.base_dir / 'logs' / '结算操作记录.json'
        self.lock = SingleInstanceLock(journal_path).acquire()
        try:
            self.journal = CommitJournal(journal_path)
        except BaseException:
            self.lock.release()
            raise
        self.playwright = self.browser = self.context = self.main = self.page = self.frame = None
        self.business_date = None
        self.skip_list_reader = None
        self.skip_numbers = frozenset()
        self.skip_list_date = None
        self.last_rows = {}
        self.prepared = False
        self.org_selection = self.type_selection = None
        self.activity = BackgroundActivityGuard(emit, interval=float(settings.background_refresh_seconds))
        self.control.poll_interval = float(settings.control_poll_seconds)
        self.previous_activity_hook = self.control.activity_hook
        self.activity_hook = self.activity.refresh
        self.control.activity_hook = self.activity_hook

    def _eval(self, action, root=None, **kwargs):
        return (root or self.frame).evaluate(self.script, dict(action=action, **kwargs))

    def _response_timeout(self):
        return float(self.settings.response_timeout_seconds)

    def _action_pause(self):
        self.control.wait(float(self.settings.action_delay_seconds))

    def _wait(self, predicate, message, timeout=None, cancel=True):
        timeout = self._response_timeout() if timeout is None else timeout
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            if cancel:
                self.control.checkpoint()
            try:
                result = predicate()
                if result:
                    return result
            except (NccUiError, StopRequested):
                raise
            except Exception as exc:
                last_error = exc
            if cancel:
                self.control.wait(float(self.settings.condition_poll_seconds))
            else:
                time.sleep(float(self.settings.condition_poll_seconds))
        suffix = ('；' + str(last_error).splitlines()[0]) if last_error else ''
        raise NccUiError(message + suffix)

    def _wait_stable_value(self, reader, accept, message, timeout=None, stable_seconds=None):
        """Do not treat a transient empty/stale UI value as a completed response."""
        timeout = self._response_timeout() if timeout is None else timeout
        stable_seconds = float(self.settings.stable_seconds) if stable_seconds is None else stable_seconds
        deadline = time.monotonic() + timeout
        marker = object()
        previous, stable_since = marker, None
        last = None
        while time.monotonic() < deadline:
            self.control.checkpoint()
            try:
                value = reader()
                now = time.monotonic()
                if accept(value):
                    if previous is marker or value != previous or stable_since is None:
                        stable_since = now
                    elif now - stable_since >= stable_seconds:
                        return value
                else:
                    stable_since = None
                previous, last = value, value
            except StopRequested:
                raise
            except Exception as exc:
                previous, stable_since, last = marker, None, str(exc).splitlines()[0]
            self.control.wait(float(self.settings.stable_poll_seconds))
        detail = str(last)
        if len(detail) > 180:
            detail = detail[:180] + '…'
        raise NccUiError(message + '；最后页面状态：' + detail)

    def _visible(self, root, selectors):
        for selector in selectors:
            candidates = root.locator(selector)
            for i in range(min(candidates.count(), 100)):
                if candidates.nth(i).is_visible():
                    return candidates.nth(i)
        return None

    def _locate(self, root, selectors, message, timeout=None):
        return self._wait(lambda: self._visible(root, selectors), message, timeout)

    def _click(self, locator):
        self.control.checkpoint()
        locator.click(timeout=int(min(self._response_timeout(), float(self.settings.click_timeout_seconds)) * 1000))
        self._action_pause()

    def _fill(self, locator, value):
        self.control.checkpoint()
        locator.fill(value)
        self._action_pause()

    def _press(self, locator, key):
        self.control.checkpoint()
        locator.press(key)
        self._action_pause()

    def _text_button(self, root, name, timeout=None):
        return self._locate(root, [
            "xpath=//button[normalize-space(.)='%s']" % name,
            "xpath=//*[@role='button' and normalize-space(.)='%s']" % name,
        ], '未找到按钮：' + name, timeout)

    def _idle(self):
        self._wait_stable_value(lambda: self._visible(self.frame, [
            '.u-loading-backdrop', '.nc-loading', '.u-table-placeholder .u-loading',
            '.ant-spin-spinning', '.loading-container .loading'
        ]) is None, bool, '页面仍在加载，已停止')

    def _find_work_frame(self, page):
        """Probe one page once; keep polling the context until a real node is ready."""
        if page.is_closed():
            return None
        for candidate in page.frames:
            try:
                org = self._visible(candidate, ['.search-item-wrap[name="pk_org"]', "input[placeholder='财务组织']"])
                if org is None:
                    continue
                expected_path = '/cmp/settlementmanagement/settlement/' in candidate.url
                if expected_path:
                    return candidate
                trade = self._visible(candidate, ['.search-item-wrap[name="pk_tradetypeid"]', "input[placeholder='业务单据交易类型']"])
                settle = self._visible(candidate, ['button.nc-list_head-btn-settleBtn'])
                if trade is not None and settle is not None:
                    return candidate
            except Exception:
                # A just-opened tab/iframe may replace its execution context.
                continue
        return None

    def _work_frame(self):
        return self._wait(lambda: self._find_work_frame(self.page), '未检测到“结算（出纳）”查询页面')

    def _locate_node_result(self, root, timeout=None):
        def find():
            first = self._visible(root, ['ul.ant-select-dropdown-menu > li', "[role='listbox'] [role='option']"])
            if first is None:
                return None
            matches = first.evaluate(r"""el => {
                const norm=s=>String(s||'').replace(/\s/g,'').replace(/（/g,'(').replace(/）/g,')');
                const name='结算(出纳)';
                if(norm(el.getAttribute('title'))===name) return true;
                if(norm(el.innerText)===name) return true;
                if((el.innerText||'').split(/\r?\n/).some(s=>norm(s)===name)) return true;
                return [...el.querySelectorAll('[title],span,a')].some(child=>
                    norm(child.getAttribute('title'))===name || (!child.children.length && norm(child.textContent)===name));
            }""")
            return first if matches else None
        return self._wait(find, '未找到首条“结算（出纳）”菜单结果，请检查节点名称或权限', timeout)

    def _wait_for_node_page(self, main, before, timeout=None):
        self.page = main
        protected = set()
        def find():
            pages = [p for p in self.context.pages if not p.is_closed()]
            candidates = [p for p in reversed(pages) if p not in before]
            if not main.is_closed():
                candidates.append(main)
            for candidate in candidates:
                if id(candidate) not in protected:
                    self.activity.watch(self.context, candidate)
                    protected.add(id(candidate))
                frame = self._find_work_frame(candidate)
                if frame is not None:
                    self.page, self.frame = candidate, frame
                    return frame
            return None
        self.emit('status', '正在等待“结算（出纳）”页面加载…')
        self._wait(find, '未检测到“结算（出纳）”查询页面，已停止；请查看页面检查资料', timeout)

    def _login_and_navigate(self):
        self.playwright = sync_playwright().start()
        browser_path = find_browser(self.base_dir, self.settings.browser_path)
        self.emit('log', '使用收款结算单同款浏览器：' + browser_path)
        self.browser = self.playwright.chromium.launch(executable_path=browser_path,
            headless=False, args=list(LAUNCH_ARGS), timeout=int(float(self.settings.browser_start_timeout_seconds) * 1000))
        self.context = self.browser.new_context(ignore_https_errors=True, no_viewport=True)
        self.context.set_default_timeout(int(float(self.settings.operation_timeout_seconds) * 1000))
        self.context.set_default_navigation_timeout(int(float(self.settings.navigation_timeout_seconds) * 1000))
        self._wait_skip_list_login()
        self.control.checkpoint()
        self.main = self.context.new_page()
        self.activity.watch(self.context, self.main)
        self.main.goto(LOGIN_URL, wait_until='domcontentloaded')
        self.emit('status', '请在打开的浏览器中登录 NCC（最长等待 {:g} 秒）'.format(float(self.settings.login_timeout_seconds)))
        icon = self._locate(self.main, ['i.iconfont.icon-sousuo1'], '等待 NCC 登录超时，请重新启动任务', float(self.settings.login_timeout_seconds))
        self.emit('log', 'NCC 登录已确认，搜索“结算（出纳）”。')
        self._click(icon)
        search = self._locate(self.main, ["input.ant-select-search__field", "input[placeholder*='搜索']"], '未找到菜单搜索框')
        self._fill(search, '结算（出纳）')
        result = self._locate_node_result(self.main)
        before = list(self.context.pages)
        self._click(result)
        self._wait_for_node_page(self.main, before)

    def _header_date(self):
        raw = self._eval('businessDate', root=self.page)
        return parse_date(raw)

    def _business_date_state(self):
        return self._eval('businessDateState', root=self.page)

    def _business_date_clear(self):
        state = self._business_date_state()
        return not state['popup_visible'] and not state['mask_visible']

    def _assert_business_date_clear(self):
        if self.page is not None:
            self._wait(self._business_date_clear, '业务日期日历或遮罩尚未关闭，已停止后续操作')

    def set_business_date(self, day):
        self.control.checkpoint()
        current = self._header_date()
        if current is None:
            raise NccUiError('无法唯一识别右上角业务日期，未执行结算')
        state = self._business_date_state()
        if current == day and not state['popup_visible'] and not state['mask_visible']:
            self.business_date = day
            self.emit('log', '业务日期已就绪，日历及遮罩已关闭：' + day.isoformat())
            return
        root = self.page
        if state['ambiguous']:
            raise NccUiError('业务日期日历不唯一，未修改日期')
        if not state['popup_visible']:
            if state['mask_visible']:
                self._wait(self._business_date_clear, '业务日期遮罩未退出，无法打开日期日历')
            if not self._eval('businessDateClickTarget', root=root):
                raise NccUiError('右上角日期点击区域无法唯一确认，未修改日期')
            self._click(root.locator('[data-nccbot-business-date-trigger]'))
        self._wait(lambda: self._business_date_state()['editor_available'],
                   '右上角日期弹窗未识别，请使用“页面检查”资料校准日期控件')
        editor = root.locator('[data-nccbot-business-date-editor]')
        self._fill(editor, day.isoformat())
        self._press(editor, 'Tab')
        state = self._business_date_state()
        # NCC previews the typed value in the header before confirmation. The
        # preview is never completion evidence while its calendar/mask is open.
        if state['popup_visible']:
            self._wait(lambda: self._business_date_state()['confirm_available'],
                       '未找到业务日期日历内部的唯一确定按钮')
            self._click(root.locator('[data-nccbot-business-date-confirm]'))
        self._wait(lambda: self._business_date_clear() and self._header_date() == day,
                   '业务日期尚未完成确认或日历遮罩未关闭，已停止')
        self.frame = self._work_frame()
        self.business_date = day
        self.emit('log', '业务日期已确认，日历及遮罩已关闭：' + day.isoformat())

    def _field(self, names):
        fields = {'财务组织': 'pk_org', '业务单据类型': 'pk_tradetypeid',
                  '业务单据交易类型': 'pk_tradetypeid', '结算状态': 'settlestatus'}
        for name in names:
            if name in fields:
                candidate = self.frame.locator('.search-item-wrap[name="%s"]' % fields[name])
                if candidate.count() == 1 and candidate.is_visible():
                    return candidate
        self._wait(lambda: self._eval('field', names=names), '未找到查询条件：' + '/'.join(names))
        return self.frame.locator('[data-nccbot-field]')

    def _selection(self, names):
        field = self._field(names)
        return field.evaluate("""el => {
            const hidden = [...el.querySelectorAll('input.hidden-input')].map(x=>x.value.trim()).filter(Boolean);
            if(hidden.length) return hidden.join('|');
            const selected = [...el.querySelectorAll('.selected-item, .u-select-selection-choice')].map(x=>x.textContent.trim()).filter(Boolean);
            if(selected.length) return selected.join('|');
            return [...el.querySelectorAll('input')].map(x=>x.value.trim()).filter(Boolean).join('|');
        }""")

    def _selection_display(self, names):
        return self._field(names).evaluate("""el => {
            const shown=x=>getComputedStyle(x).display!=='none' && x.getBoundingClientRect().width>0;
            const names=[...el.querySelectorAll('.selected-item,.u-select-selection-choice')]
                .filter(shown).map(x=>(x.title || x.textContent).trim()).filter(Boolean);
            if(names.length) return names.join('|');
            return [...el.querySelectorAll('input:not(.hidden-input)')].filter(shown)
                .map(x=>x.value.trim()).filter(Boolean).join('|');
        }""")

    def _wait_reference_commit(self, dialog, names, label, expected_name=''):
        self.emit('status', '等待' + label + '选择结果写回页面…')
        self._wait(lambda: not dialog.is_visible(), label + '参照窗口尚未关闭')
        self._idle()
        result = self._wait_stable_value(
            lambda: (self._selection(names), self._selection_display(names)),
            lambda pair: bool(pair[0]) and (not expected_name or pair[1] == expected_name),
            label + '选择结果未确认，请检查 NCC 响应或调大页面最长等待')
        return result[0]

    def _assert_filters(self):
        self._assert_business_date_clear()
        self._assert_unsettled()
        if self.org_selection is not None and self._selection(['财务组织']) != self.org_selection:
            raise NccUiError('财务组织筛选发生变化，已停止')
        if self.type_selection is not None and self._selection(['业务单据类型']) != self.type_selection:
            raise NccUiError('D4 交易类型筛选发生变化，已停止')

    def _open_refer(self, names):
        self._assert_business_date_clear()
        field = self._field(names)
        icon = self._visible(field, ['.icon-refer'])
        self._click(icon or field)

    def _dialog(self):
        return self._locate(self.frame, ['.u-modal-dialog', '.ant-modal-content', '[role="dialog"]', '.refer-pop-window'], '未找到参照选择窗口')

    def _checkbox_state(self, locator):
        return locator.evaluate("el => { const custom=el.closest('.nc-checkbox'); if(custom)return custom.classList.contains('is-checked'); const input=el.matches('input')?el:el.querySelector('input[type=checkbox],input[type=radio]'); const role=el.matches('[role=checkbox]')?el:el.querySelector('[role=checkbox]'); return input ? input.checked : role ? role.getAttribute('aria-checked')==='true' : !!el.closest('.is-checked,.u-checkbox-checked,.ant-checkbox-checked,.u-tree-checkbox-checked,.ant-tree-checkbox-checked'); }")

    def _set_checkbox(self, locator, checked):
        if self._checkbox_state(locator) != checked:
            self._click(locator)
        self._wait(lambda: self._checkbox_state(locator) == checked, '复选框选择状态无法确认')

    def _set_org(self):
        self._open_refer(['财务组织'])
        dialog = self._dialog()
        label = self._locate(dialog, ["xpath=.//label[contains(.,'包含下级')]", "xpath=.//*[normalize-space(text())='包含下级']/parent::*"], '未找到“包含下级”复选框')
        self._set_checkbox(label, True)
        # First tree node is the top financial organization, not the first search input checkbox.
        checkbox = self._locate(dialog, [
            '.u-tree li > .u-tree-checkbox', '.ant-tree-treenode > .ant-tree-checkbox',
            "[role='treeitem'] input[type=checkbox]", "[role='treeitem'] [role=checkbox]"
        ], '财务组织首项缺少复选框')
        self._set_checkbox(checkbox, True)
        self._click(self._locate(dialog, ["xpath=.//button[starts-with(normalize-space(.),'确定')]"], '未找到财务组织确定按钮'))
        self.org_selection = self._wait_reference_commit(dialog, ['财务组织'], '财务组织')

    def _set_type(self):
        self._open_refer(['业务单据类型', '业务单据交易类型'])
        dialog = self._dialog()
        search = self._locate(dialog, ['input[placeholder*="搜索"]', 'input[type="text"]', 'input:not([type])'], '未找到业务类型搜索框')
        self._fill(search, 'D4')
        self._press(search, 'Enter')
        selectors = [
            "xpath=.//tr[td[normalize-space(.)='D4']]",
            "xpath=.//*[@role='treeitem' and .//*[normalize-space(text())='D4']]",
            "xpath=.//li[.//*[normalize-space(text())='D4'] or normalize-space(.)='D4']"
        ]
        self._wait_stable_value(lambda: self._type_candidate_signature(dialog, selectors), bool,
                                '未找到稳定的 D4 类型首项')
        first = self._locate(dialog, selectors, '未找到 D4 类型首项')
        cells = first.locator('td')
        names = [cells.nth(i).inner_text().strip() for i in range(cells.count())]
        expected_name = names[names.index('D4') + 1] if 'D4' in names and names.index('D4') + 1 < len(names) else ''
        cb = self._visible(first, ['label.nc-checkbox', 'input[type=checkbox]', '[role=checkbox]', 'input[type=radio]'])
        # NCC's fixed selection column can live in a separate table. Match its
        # stable row key rather than clicking an unselected text row too early.
        key = first.get_attribute('data-row-key')
        if cb is None and key:
            peers = dialog.locator('tr[data-row-key=%s]' % json.dumps(key))
            for i in range(peers.count()):
                cb = self._visible(peers.nth(i), ['label.nc-checkbox', 'input[type=checkbox]', '[role=checkbox]', 'input[type=radio]'])
                if cb is not None:
                    break
        if cb:
            self._set_checkbox(cb, True)
        else:
            if 'refer-selected-row' not in (first.get_attribute('class') or ''):
                self._click(first)
            self._wait(lambda: 'refer-selected-row' in (first.get_attribute('class') or '') or
                       self._visible(dialog, ["xpath=.//*[contains(@class,'refer-selected')]//*[normalize-space(.)='D4']"]) is not None,
                       'D4 候选尚未确认选中，请检查 NCC 响应')
        self._click(self._locate(dialog, ["xpath=.//button[starts-with(normalize-space(.),'确定')]"], '未找到类型确定按钮'))
        self.type_selection = self._wait_reference_commit(
            dialog, ['业务单据类型', '业务单据交易类型'], 'D4 业务类型', expected_name)

    def _type_candidate_signature(self, dialog, selectors):
        first = self._visible(dialog, selectors)
        if first is None:
            return ''
        return (first.get_attribute('data-row-key') or '') + '|' + first.inner_text().strip()

    def _set_unsettled(self):
        if self._status_values() == ['未结算']:
            return
        field = self._field(['结算状态'])
        for _ in range(12):
            remove = self._visible(field, ['.u-select-selection-choice-remove', '.ant-select-selection__choice__remove'])
            if remove is None:
                break
            self._click(remove)
        self._click(field)
        option = self._locate(self.frame, [
            "xpath=//*[@role='option' and normalize-space(.)='未结算']",
            "xpath=//li[normalize-space(.)='未结算']",
            "xpath=//*[contains(@class,'option') and normalize-space(.)='未结算']"
        ], '未找到结算状态“未结算”')
        self._click(option)
        self._wait_stable_value(self._status_values, lambda values: values == ['未结算'],
                                '结算状态“未结算”尚未生效')

    def _assert_unsettled(self):
        if self._status_values() != ['未结算']:
            raise NccUiError('未确认筛选条件仅为“未结算”，已停止')

    def _status_values(self):
        field = self._field(['结算状态'])
        return field.evaluate("""el => {
            const choices=[...el.querySelectorAll('.u-select-selection-choice,.ant-select-selection__choice')];
            if(choices.length) return choices.map(x=>x.title || x.textContent.replace(/×/g,'').trim());
            return [...el.querySelectorAll('input')].map(x=>x.value.trim()).filter(Boolean);
        }""")

    def prepare(self, start_date):
        if self.prepared:
            raise NccUiError('前置设置不得重复执行')
        pending = self.journal.pending()
        if pending:
            raise CommitUncertain('存在上次结果未确认的结算，禁止自动重试：%s。请先在 NCC 人工核对，再点击主界面的“核对待确认记录”。' % pending[0])
        self._login_and_navigate()
        self.emit('status', '设置右上角业务日期：' + start_date.isoformat())
        self.set_business_date(start_date)
        self.emit('status', '选择财务组织：包含下级，并勾选首项')
        self._set_org()
        self.emit('status', '选择业务单据交易类型 D4')
        self._set_type()
        self.emit('status', '设置结算状态为“未结算”')
        self._set_unsettled()
        self.emit('log', '前置条件已设置：财务组织包含下级/首项、D4、未结算；左侧查询日期保留 NCC 原值。')
        self.refresh()
        self.prepared = True

    def _pager(self):
        info = self._eval('pager')
        if info['total'] is None:
            # NCC's empty-result page may omit the total count.
            if self._visible(self.frame, ["xpath=//*[normalize-space(.)='暂无数据']", '.u-table-empty-text']):
                info.update(total=0, page=1, pages=1)
        return info

    def ensure_page_size(self):
        pager = self._pager()
        if pager['size'] != 100:
            selector = self.frame.locator('[data-nccbot-page-size]')
            if not selector.count():
                raise NccUiError('未识别左下角每页条数')
            self._click(selector)
            option = self._locate(self.frame, [
                "xpath=//li[normalize-space(.)='100条/页']",
                "xpath=//*[@role='option' and normalize-space(.)='100条/页']",
                "xpath=//*[contains(@class,'option') and normalize-space(.)='100条/页']"
            ], '未找到“100条/页”选项')
            self._click(option)
            self._idle()
        self._wait(lambda: self._pager()['size'] == 100, '未确认每页 100 条')

    def first_page(self):
        info = self._pager()
        if info['page'] == 1:
            return
        if not self._eval('pageButton', number=1):
            raise NccUiError('未找到第一页按钮')
        self._click(self.frame.locator('[data-nccbot-page-button]'))
        self._idle()
        self._wait(lambda: self._pager()['page'] == 1, '未确认返回第一页')

    def next_page(self):
        before = self._pager()
        if before['page'] is None or before['pages'] is None:
            raise NccUiError('无法确认分页信息')
        if not self._eval('nextButton') and not self._eval('pageButton', number=before['page'] + 1):
            raise NccUiError('未找到下一页按钮')
        self._click(self.frame.locator('[data-nccbot-page-button]'))
        self._idle()
        self._wait(lambda: self._pager()['page'] == before['page'] + 1, '翻页后页码未变化')

    def refresh(self):
        self._assert_filters()
        search = self._locate(self.frame, [
            ".search-component-searchBtn", "button:has(i.icon-sousuo)", "button:has(i.icon-sousuo1)",
            ".search-button", ".NC_searchBtn", "button[title='查询']", "button[title='搜索']",
            "xpath=//button[.//*[contains(@class,'icon-search') or contains(@class,'icon-sousuo')]]"
        ], '未找到红色查询按钮；需校准按钮定位')
        self._click(search)
        self._idle()
        # NCC initially displays 待签字; 全部 is needed to inspect signed entries too.
        all_tab = self._visible(self.frame, ["xpath=//*[@role='tab' and normalize-space(.)='全部']"])
        if all_tab is not None and all_tab.get_attribute('aria-selected') != 'true':
            self._click(all_tab)
            self._idle()
        self.ensure_page_size()
        self.first_page()

    def _ensure_skip_list_reader(self):
        if self.skip_list_reader is None:
            self.skip_list_reader = DingTalkSkipList(
                self.context, self.settings, self.control, self.emit,
                on_page_created=lambda page: self.activity.watch(self.context, page),
                result_store=SkipListResultStore(self.base_dir, 'run'))

    def _wait_skip_list_login(self):
        self._ensure_skip_list_reader()
        self.emit('status', '先打开文档，请手动登录钉钉；确认成功后才进入 NCC。')
        self.skip_list_reader.wait_for_login()

    def refresh_skip_list(self, day):
        # 清除上个日期的有效标记，刷新失败后不能继续沿用旧快照。
        self.skip_list_date = None
        self.skip_numbers = frozenset()
        self._ensure_skip_list_reader()
        numbers = self.skip_list_reader.refresh(day)
        self.skip_numbers = numbers
        self.skip_list_date = day
        return numbers

    def open_date(self, day):
        self.set_business_date(day)
        self.refresh()

    def read_page(self):
        self.control.checkpoint()
        pager = self._pager()
        if pager['size'] != 100 or pager['page'] is None or pager['pages'] is None:
            raise NccUiError('无法核实 100 条/页、总条数或当前页码')
        if pager['total'] == 0:
            return PageSnapshot([], pager['page'], pager['pages'])
        data = self._eval('grid')
        if data.get('error'):
            raise NccUiError(data['error'])
        self._eval('scrollX', end=True)
        self._action_pause()
        expected = min(100, pager['total'] - (pager['page'] - 1) * 100)
        lookup = {}
        self._eval('scrollY', start=True)
        self._action_pause()
        for step in range(150):
            self.control.checkpoint()
            data = self._eval('grid')
            if data.get('error') or data.get('conflict'):
                raise NccUiError('明细列对应关系无法确认，已停止')
            headers = data['headers']
            signed_column = next((h for h in ['签字确认日期', '签字日期'] if h in headers), None)
            if '签字人' not in headers or signed_column is None or '业务单据编号' not in headers:
                raise NccUiError('未同时识别“业务单据编号、签字人、签字确认日期”列，请先校准表格')
            page_keys = set()
            for raw in data['rows']:
                values = raw['values']
                number = values.get('业务单据编号', '').strip()
                if not number:
                    continue
                key = values.get('财务组织名称', '').strip() + '|' + number
                if key in page_keys:
                    raise NccUiError('同页单据标识重复，无法安全选择：' + key)
                page_keys.add(key)
                previous = lookup.get(key)
                if previous and (previous.signer != values.get('签字人', '') or previous.sign_date != values.get(signed_column, '')):
                    raise NccUiError('读取期间签字信息发生变化，已停止')
                values.update(_index=raw['index'], _token=raw.get('token'), _dom_key=raw.get('dom_key'), _selected=raw['checked'])
                lookup[key] = Row(key, values.get('签字人', ''), values.get(signed_column, ''), values)
            if len(lookup) >= expected:
                break
            scroll = self._eval('scrollY', start=False)
            if not scroll['moved']:
                break
            self._action_pause()
        if len(lookup) != expected:
            raise NccUiError('本页应有 %s 条，实际读取 %s 条；明细未完整加载，已停止防止漏单' % (expected, len(lookup)))
        after = self._pager()
        if after['total'] != pager['total'] or after['page'] != pager['page']:
            raise NccUiError('读取期间页码或总条数变化，已停止')
        result = list(lookup.values())
        self.last_rows = lookup
        return PageSnapshot(result, pager['page'], pager['pages'])

    def _row_checkbox(self, row):
        token = row.values.get('_token')
        if not token:
            raise NccUiError('目标行缺少已核验的 DOM 标识')
        dom_key = row.values.get('_dom_key')
        selector = '[data-nccbot-grid] tr[data-row-key=%s]' % json.dumps(dom_key) if dom_key else '[data-nccbot-grid] tr[data-nccbot-token="%s"]' % token
        candidates = self.frame.locator(selector)
        if dom_key and not candidates.count():
            self._eval('scrollY', start=True)
            self._action_pause()
            for _ in range(150):
                if candidates.count():
                    break
                if not self._eval('scrollY', start=False)['moved']:
                    break
                self._action_pause()
        for i in range(candidates.count()):
            tr = candidates.nth(i)
            cb = self._visible(tr, ['label.nc-checkbox', 'input[type=checkbox]', '[role=checkbox]', '.u-checkbox'])
            if cb:
                return cb
        raise NccUiError('待结算行复选框未找到：' + row.key)

    def _messages(self):
        result = []
        for root in [self.frame, self.page]:
            for selector in ['.toast-content', '.groupOperationMsg li', '.u-message-notice-content', '.u-notification-notice-description',
                             '.u-notification-notice-message', '.u-message', '.u-notification',
                             '.ant-message-notice', '.ant-notification-notice', '.nc-message', '[role=alert]']:
                loc = root.locator(selector)
                for i in range(min(loc.count(), 20)):
                    if loc.nth(i).is_visible():
                        value = loc.nth(i).inner_text().strip()
                        if value and value not in result:
                            result.append(value)
        return result

    def settle(self, row, day):
        self._assert_skip_list(row, day)
        if self._header_date() != day:
            raise NccUiError('提交前右上角日期发生变化，已停止')
        self._assert_filters()
        # On the calibrated NCC page a new query replaces row keys and clears
        # the previous selection. Start every submission from that clean query.
        self.refresh()
        current = None
        while True:
            snapshot = self.read_page()
            for other in snapshot.rows:
                if other.values['_selected']:
                    self._set_checkbox(self._row_checkbox(other), False)
            current = next((r for r in snapshot.rows if r.key == row.key), None)
            if current is not None or snapshot.page >= snapshot.total_pages:
                break
            self.next_page()
        if current is None or not eligible(current, day):
            raise NccUiError('提交前签字信息已变化，未执行结算：' + row.key)
        journal_key = day.isoformat() + '|' + current.key
        if self.journal.entries.get(journal_key, {}).get('state') in ('pending', 'confirmed'):
            raise CommitUncertain('该单已有结算点击记录，请人工核对：' + journal_key)
        # Clear every selection on this page; never use the select-all checkbox.
        for other in snapshot.rows:
            if other.values['_selected']:
                self._set_checkbox(self._row_checkbox(other), False)
        self._set_checkbox(self._row_checkbox(current), True)
        checked = [r.key for r in self.read_page().rows if r.values['_selected']]
        if checked != [current.key]:
            raise NccUiError('勾选范围未确认仅包含目标明细，未点击结算')
        button = self._text_button(self.frame, '结算')
        if not button.is_enabled() or button.get_attribute('aria-disabled') == 'true':
            raise NccUiError('目标明细的结算按钮不可用：' + current.key)
        # Old success notifications must disappear before the one irreversible click.
        self._wait(lambda: not self._messages(), '旧提示未消失，无法判断本次结算结果')
        self.control.wait(float(self.settings.settlement_delay_seconds))
        # Recheck after waiting: no stale signer/date/selection may reach the click.
        fresh = self.read_page()
        selected = [r for r in fresh.rows if r.values['_selected']]
        if len(selected) != 1 or selected[0].key != current.key or not eligible(selected[0], day) or self._header_date() != day:
            raise NccUiError('点击前明细或日期变化，未执行结算')
        self._assert_skip_list(selected[0], day)
        self._assert_filters()
        self.control.checkpoint()
        self.journal.record(journal_key, 'pending', number=current.values['业务单据编号'], business_date=day.isoformat())
        try:
            self.control.checkpoint()
        except StopRequested:
            self.journal.record(journal_key, 'cancelled_before_click', number=current.values['业务单据编号'], business_date=day.isoformat())
            raise
        try:
            # Do not retry this click, even after timeout or a stop signal.
            button.click(timeout=int(min(self._response_timeout(), float(self.settings.click_timeout_seconds)) * 1000))
            deadline = time.monotonic() + float(self.settings.success_timeout_seconds)
            while time.monotonic() < deadline:
                self.activity.refresh()
                messages = self._messages()
                errors = [m for m in messages if re.search('失败|错误|异常|不能|不允许|不足|未成功', m)]
                if errors:
                    raise CommitUncertain('NCC 返回：' + '；'.join(errors))
                success = [m for m in messages if is_settlement_success(m) or re.fullmatch(r'操作成功[！!。]?', m.strip())]
                if success:
                    # Success toast plus a fresh unsettle query must confirm this target disappeared.
                    self._verify_disappeared(current, day)
                    self.journal.record(journal_key, 'confirmed', number=current.values['业务单据编号'], business_date=day.isoformat(), evidence='；'.join(success))
                    return True
                # Post-click verification is bounded and continues when Stop is requested.
                time.sleep(float(self.settings.condition_poll_seconds))
            raise CommitUncertain('点击结算后未收到明确的结算成功提示')
        except BaseException as exc:
            self.capture_diagnostics()
            raise CommitUncertain('%s；单据 %s，日期 %s。操作已记录为 pending，禁止自动重试；请先在 NCC 核对。' % (exc, current.key, day.isoformat())) from exc

    def _assert_skip_list(self, row, day):
        if self.skip_list_date != day:
            raise NccUiError('该日期尚未成功刷新跳过清单，未执行结算')
        if bill_number(row) in self.skip_numbers:
            raise NccUiError('单据位于 Sheet3 跳过清单，未执行结算：' + bill_number(row))

    def _verify_disappeared(self, target, day):
        # A stop after the click waits only for this bounded verification, never another submission.
        original = self.control
        from settlement_core import Control
        self.control = Control(activity_hook=self.activity_hook, poll_interval=float(self.settings.control_poll_seconds))
        deadline = time.monotonic() + float(self.settings.verification_timeout_seconds)
        try:
            self.refresh()
            while time.monotonic() < deadline:
                if self._header_date() != day:
                    raise CommitUncertain('结算后业务日期发生变化')
                page = self.read_page()
                if any(row.key == target.key for row in page.rows):
                    raise CommitUncertain('收到成功提示，但该单仍在未结算查询结果中')
                if page.page == page.total_pages:
                    return
                self.next_page()
            raise CommitUncertain('结算后核验目标单据超时')
        finally:
            self.control = original

    def capture_diagnostics(self):
        """只保存截图和页面结构，排除输入框值，便于首次现场校准。"""
        if self.page is None:
            return
        folder = self.base_dir / 'logs' / ('页面检查_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        folder.mkdir(parents=True, exist_ok=True)
        if self.context is not None:
            try:
                from urllib.parse import urlsplit
                def public_path(url):
                    parsed = urlsplit(url)
                    return parsed.scheme + '://' + parsed.netloc + parsed.path
                pages = []
                for page in self.context.pages:
                    if not page.is_closed():
                        pages.append(dict(current=page is self.page, url=public_path(page.url),
                            frames=[public_path(frame.url) for frame in page.frames]))
                (folder / '标签页清单.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass
        try:
            self.page.screenshot(path=str(folder / '页面.png'), timeout=int(float(self.settings.screenshot_timeout_seconds) * 1000))
            structure = []
            for frame in self.page.frames:
                info = frame.evaluate("""() => ({url:location.origin+location.pathname, inputs:[...document.querySelectorAll('input')].map(e=>({placeholder:e.placeholder,label:e.getAttribute('label'),class:e.className,type:e.type})),buttons:[...document.querySelectorAll('button')].map(e=>({text:e.innerText,class:e.className})),headers:[...document.querySelectorAll('th')].map(e=>e.innerText)})""")
                structure.append(info)
            (folder / '控件结构.json').write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding='utf-8')
            self.emit('log', '页面检查资料已保存：' + str(folder))
        except Exception:
            pass

    def close(self):
        try:
            if self.control.activity_hook is self.activity_hook:
                self.control.activity_hook = self.previous_activity_hook
            self.activity.close()
            if self.page is not None:
                try:
                    self.capture_diagnostics()
                except Exception:
                    pass
            if self.browser is not None:
                self.browser.close()
        finally:
            try:
                if self.playwright is not None:
                    self.playwright.stop()
            finally:
                self.lock.release()
