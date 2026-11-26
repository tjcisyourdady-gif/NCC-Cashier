"""结算日期、签字检查及可停止的循环。此模块不依赖浏览器或 GUI。"""

import json
import math
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from skip_list import DEFAULT_SKIP_LIST_URL, bill_number, latest_preview_url


LOGIN_URL = "https://ncc.bgyfw.com/nccloud/resources/uap/rbac/login/main/index.html"


@dataclass
class Settings:
    date_mode: str = "month_to_date"
    start_date: str = ""
    end_date: str = ""
    sleep_minutes: float = 10.0
    browser_path: str = ""
    action_delay_seconds: float = 3.0
    response_timeout_seconds: float = 60.0
    settlement_delay_seconds: float = 3.0
    stable_seconds: float = 1.0
    click_timeout_seconds: float = 15.0
    operation_timeout_seconds: float = 5.0
    browser_start_timeout_seconds: float = 30.0
    navigation_timeout_seconds: float = 60.0
    login_timeout_seconds: float = 600.0
    success_timeout_seconds: float = 60.0
    verification_timeout_seconds: float = 60.0
    screenshot_timeout_seconds: float = 4.0
    condition_poll_seconds: float = 0.2
    stable_poll_seconds: float = 0.25
    control_poll_seconds: float = 0.5
    background_refresh_seconds: float = 2.0
    gui_log_refresh_seconds: float = 0.1
    gui_date_refresh_seconds: float = 30.0
    skip_list_url: str = DEFAULT_SKIP_LIST_URL


# 界面范围及标签统一定义；核心允许零操作停顿，供离线验证使用。
TIME_OPTIONS = (
    ("action_delay_seconds", "普通操作后停顿", "秒", 0.5, 30, 0.5, "操作与循环"),
    ("response_timeout_seconds", "页面条件最长等待", "秒", 5, 600, 5, "操作与循环"),
    ("sleep_minutes", "每轮完成后休眠", "分钟", 0.1, 525600, 1, "操作与循环"),
    ("settlement_delay_seconds", "结算前额外停顿", "秒", 0, 60, 0.5, "操作与循环"),
    ("stable_seconds", "页面状态连续稳定时间", "秒", 0.5, 30, 0.5, "操作与循环"),
    ("click_timeout_seconds", "单次点击最长等待", "秒", 1, 600, 1, "超时"),
    ("operation_timeout_seconds", "输入、按键等操作最长等待", "秒", 1, 600, 1, "超时"),
    ("browser_start_timeout_seconds", "浏览器启动最长等待", "秒", 5, 600, 5, "超时"),
    ("navigation_timeout_seconds", "网页导航最长等待", "秒", 5, 600, 5, "超时"),
    ("login_timeout_seconds", "人工登录最长等待", "秒", 30, 3600, 30, "超时"),
    ("success_timeout_seconds", "结算成功提示最长等待", "秒", 5, 600, 5, "超时"),
    ("verification_timeout_seconds", "结算后目标消失核验窗口", "秒", 5, 3600, 5, "超时"),
    ("screenshot_timeout_seconds", "出错截图最长等待", "秒", 1, 60, 1, "超时"),
    ("condition_poll_seconds", "普通条件及结算提示检查间隔", "秒", 0.05, 5, 0.05, "检查与刷新"),
    ("stable_poll_seconds", "稳定状态检查间隔", "秒", 0.05, 2, 0.05, "检查与刷新"),
    ("control_poll_seconds", "长等待后台维护检查间隔", "秒", 0.05, 1, 0.05, "检查与刷新"),
    ("background_refresh_seconds", "浏览器后台保活间隔", "秒", 0.5, 10, 0.5, "检查与刷新"),
    ("gui_log_refresh_seconds", "界面日志刷新间隔", "秒", 0.05, 2, 0.05, "检查与刷新"),
    ("gui_date_refresh_seconds", "默认日期预览刷新间隔", "秒", 1, 300, 1, "检查与刷新"),
)


def validate_time_options(values):
    """保存前逐项校验，避免无限、空白或越界时间被写入配置。"""
    result = {}
    for name, label, unit, minimum, maximum, _, _ in TIME_OPTIONS:
        try:
            number = float(values[name])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("{}请输入数字，单位为{}。".format(label, unit))
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError("{}应为 {:g}～{:g} {}。".format(label, minimum, maximum, unit))
        result[name] = number
    return result


@dataclass
class Row:
    key: str
    signer: str
    sign_date: str
    values: dict = field(default_factory=dict)


@dataclass
class PageSnapshot:
    rows: list
    page: int
    total_pages: int


class StopRequested(Exception):
    """用户已请求停止。"""


class Control:
    def __init__(self, stop_event=None, activity_hook=None, poll_interval=0.5):
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.activity_hook = activity_hook
        self.poll_interval = float(poll_interval)
        if not math.isfinite(self.poll_interval) or self.poll_interval <= 0:
            raise ValueError("后台维护检查间隔必须是大于零的有限数字")

    def checkpoint(self):
        if self.stop_event.is_set():
            raise StopRequested("任务已停止")
        if self.activity_hook is not None:
            self.activity_hook()

    def wait(self, seconds):
        self.checkpoint()
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("等待时间必须是非负有限数值")
        if self.activity_hook is None:
            if self.stop_event.wait(seconds):
                raise StopRequested("任务已停止")
        else:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if self.stop_event.wait(min(self.poll_interval, max(0, deadline - time.monotonic()))):
                    raise StopRequested("任务已停止")
                self.checkpoint()
        self.checkpoint()


_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})(?P<sep>[-/.])(?P<month>\d{1,2})(?P=sep)(?P<day>\d{1,2})"
    r"(?:(?:T|\s+)(?P<time>\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?)?"
)


def parse_date(value):
    """解析完整年月日及可选时间；非法日期和占位内容返回 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _DATE_PATTERN.fullmatch(text)
    if match is None:
        return None
    try:
        result = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        time_text = match.group("time")
        if time_text:
            hour, remainder = time_text.split(":", 1)
            zone = match.group("zone") or ""
            if zone == "Z":
                zone = "+00:00"
            elif len(zone) == 5:
                zone = zone[:3] + ":" + zone[3:]
            # 校验时间本身，不能只截取字符串的日期前缀。
            datetime.fromisoformat(
                result.isoformat() + "T" + hour.zfill(2) + ":" + remainder + zone
            )
        return result
    except (ValueError, OverflowError):
        return None


def _validate_settings(settings, require_document=True):
    if require_document or settings.skip_list_url:
        latest_preview_url(settings.skip_list_url)
    if settings.date_mode not in ("month_to_date", "custom"):
        raise ValueError("日期选项必须是本月截至今天或自定义日期")
    try:
        minutes = float(settings.sleep_minutes)
    except (TypeError, ValueError):
        raise ValueError("休眠分钟数必须是大于零的数字")
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("休眠分钟数必须是大于零的数字")
    try:
        action_delay = float(settings.action_delay_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("操作间隔必须是大于或等于零的有限数字")
    if not math.isfinite(action_delay) or action_delay < 0:
        raise ValueError("操作间隔必须是大于或等于零的有限数字")
    try:
        response_timeout = float(settings.response_timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("页面最长等待必须是大于零的有限数字")
    if not math.isfinite(response_timeout) or response_timeout <= 0:
        raise ValueError("页面最长等待必须是大于零的有限数字")
    for name, label, _, _, _, _, _ in TIME_OPTIONS:
        if name in ("action_delay_seconds", "response_timeout_seconds", "sleep_minutes"):
            continue
        try:
            number = float(getattr(settings, name))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(label + "必须是有效数字")
        if not math.isfinite(number) or number < 0 or (number == 0 and name != "settlement_delay_seconds"):
            raise ValueError(label + "必须是大于零的有限数字" if name != "settlement_delay_seconds" else "结算前额外停顿必须是非负有限数字")


def resolve_date_range(settings, today=None, require_document=True):
    _validate_settings(settings,require_document=require_document)
    if settings.date_mode == "month_to_date":
        current = date.today() if today is None else parse_date(today)
        if current is None:
            raise ValueError("无法识别当前系统日期")
        return current.replace(day=1), current
    start = parse_date(settings.start_date)
    end = parse_date(settings.end_date)
    if start is None or end is None:
        raise ValueError("请输入有效的起止日期，例如 2026-10-01")
    if start > end:
        raise ValueError("起始日期不能晚于结束日期")
    return start, end


def iter_dates(start, end):
    start, end = parse_date(start), parse_date(end)
    if start is None or end is None or start > end:
        raise ValueError("日期范围无效")
    current = start
    while current <= end:
        yield current
        if current == end:
            break
        current += timedelta(days=1)


def load_settings(path):
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("r", encoding="utf-8-sig") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict):
        raise ValueError("设置文件格式无效")
    known = Settings.__dataclass_fields__
    # 旧版这三项跟随页面等待或操作间隔；升级时保留当时的实际行为。
    if "settlement_delay_seconds" not in raw:
        raw["settlement_delay_seconds"] = raw.get("action_delay_seconds", 3.0)
    if "success_timeout_seconds" not in raw:
        raw["success_timeout_seconds"] = max(25, float(raw.get("response_timeout_seconds", 60.0)))
    if "verification_timeout_seconds" not in raw:
        raw["verification_timeout_seconds"] = max(45, float(raw.get("response_timeout_seconds", 60.0)))
    settings = Settings(**{key: value for key, value in raw.items() if key in known})
    resolve_date_range(settings,require_document=False)
    return settings


def save_settings(settings, path):
    resolve_date_range(settings)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=path.name + ".", suffix=".tmp", delete=False
        ) as stream:
            temporary = stream.name
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, str(path))
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


_EMPTY_SIGNERS = frozenset(("", "-", "--", "—", "null", "none", "undefined"))


def eligible(row, business_date):
    signer = "" if row.signer is None else str(row.signer).strip()
    if signer.lower() in _EMPTY_SIGNERS:
        return False
    signed = parse_date(row.sign_date)
    business = parse_date(business_date)
    return signed is not None and business is not None and signed == business


class AutomationRunner:
    """每次提交确认成功后重新查第一页，避免未结算记录减少导致漏单。"""

    def __init__(self, adapter, settings, control, emit):
        self.adapter = adapter
        self.settings = settings
        self.control = control
        self.emit = emit
        self._settled_by_day = {}

    def _process_date(self, business_date):
        self.control.checkpoint()
        self.emit("status", "刷新并读取 Sheet3 跳过清单：" + business_date.isoformat())
        excluded = self.adapter.refresh_skip_list(business_date)
        if not isinstance(excluded, (set, frozenset)) or any(not isinstance(x, str) or not x.strip() for x in excluded):
            raise RuntimeError("跳过清单读取结果无效，未继续结算")
        self.control.checkpoint()
        self.emit("status", "正在处理 " + business_date.isoformat())
        self.adapter.open_date(business_date)
        settled = self._settled_by_day.setdefault(business_date, set())
        completed = 0
        inspected = 0
        skipped = set()
        expected_page = 1
        seen_pages = set()
        seen_signatures = set()
        while True:
            self.control.checkpoint()
            snapshot = self.adapter.read_page()
            if (not isinstance(snapshot.page, int) or
                    not isinstance(snapshot.total_pages, int) or
                    snapshot.page < 1 or snapshot.total_pages < snapshot.page):
                raise RuntimeError("无法确认当前页数，已停止任务")
            if snapshot.page != expected_page or snapshot.page in seen_pages:
                raise RuntimeError("翻页结果未通过核验，已停止任务")
            signature = tuple(str(row.key) for row in snapshot.rows)
            if signature and all(signature) and signature in seen_signatures:
                raise RuntimeError("翻页后仍然显示相同明细，已停止任务")
            seen_pages.add(snapshot.page)
            if signature and all(signature):
                seen_signatures.add(signature)
            self.emit(
                "log", "%s：检查第 %s/%s 页，共 %s 条明细" % (
                    business_date.isoformat(), snapshot.page,
                    snapshot.total_pages, len(snapshot.rows),
                ),
            )
            if not snapshot.rows and snapshot.total_pages > 1:
                raise RuntimeError("多页结果中出现空页，无法确认是否漏单，已停止任务")
            candidate = None
            page_keys = set()
            for row in snapshot.rows:
                self.control.checkpoint()
                inspected += 1
                number = bill_number(row)
                if number in excluded:
                    if number not in skipped:
                        skipped.add(number)
                        self.emit("log", "命中 Sheet3 跳过清单，跳过单据：" + number)
                    continue
                if not eligible(row, business_date):
                    continue
                key = str(row.key).strip() if row.key is not None else ""
                if not key:
                    raise RuntimeError("待结算明细缺少唯一标识，已停止任务")
                if key in settled:
                    raise RuntimeError("已成功结算的明细再次出现：%s；已停止避免重复操作" % key)
                if key in page_keys:
                    raise RuntimeError("待结算明细的唯一标识重复：%s；已停止任务" % key)
                page_keys.add(key)
                if candidate is None:
                    candidate = row
            if candidate is not None:
                self.control.checkpoint()
                if self.adapter.settle(candidate, business_date) is not True:
                    raise RuntimeError("结算结果未确认：%s；请在 NCC 中核对后再运行" % candidate.key)
                settled.add(str(candidate.key).strip())
                completed += 1
                self.emit("log", "结算已确认成功：" + str(candidate.key))
                self.control.checkpoint()
                self.adapter.refresh()
                expected_page = 1
                seen_pages.clear()
                seen_signatures.clear()
                continue
            if snapshot.page >= snapshot.total_pages:
                break
            self.control.checkpoint()
            self.adapter.next_page()
            expected_page = snapshot.page + 1
        self.emit(
            "log", "%s 处理完成，本次确认结算 %s 条，按清单跳过 %s 个单据号" % (business_date.isoformat(), completed, len(skipped))
        )
        return completed

    def run(self):
        self.control.checkpoint()
        start, _ = resolve_date_range(self.settings)
        self.emit("status", "正在打开文档，等待手动登录钉钉")
        self.adapter.prepare(start)
        cycle = 0
        while True:
            self.control.checkpoint()
            # 默认范围每轮重新按系统日期计算，跨日和跨月都自动更新。
            start, end = resolve_date_range(self.settings)
            cycle += 1
            self.emit(
                "log", "第 %s 轮：%s 至 %s" % (cycle, start.isoformat(), end.isoformat())
            )
            count = 0
            for business_date in iter_dates(start, end):
                count += self._process_date(business_date)
            minutes = float(self.settings.sleep_minutes)
            self.emit("log", "第 %s 轮已完成，共确认结算 %s 条" % (cycle, count))
            self.emit("status", "本轮完成，休眠 %g 分钟；可随时停止" % minutes)
            self.emit("sleep", str(minutes * 60))
            self.control.wait(minutes * 60)
