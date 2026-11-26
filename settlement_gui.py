# -*- coding: utf-8 -*-
"""NCC 结算（出纳）工具的独立 Windows 图形界面。"""
import calendar
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
from datetime import date, datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from settlement_core import (
    AutomationRunner, Control, Settings, StopRequested,
    TIME_OPTIONS, load_settings, resolve_date_range, save_settings, validate_time_options,
)
from skip_list_results import SkipListResultStore
from app_updater import UpdateManager
from release_identity import DISPLAY_VERSION

APP_VERSION = DISPLAY_VERSION


def application_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class DatePicker(tk.Toplevel):
    """不依赖额外组件的日期选择器。"""

    def __init__(self, master, value, on_choose):
        super().__init__(master)
        self.title("选择日期")
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.on_choose = on_choose
        try:
            selected = datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            selected = date.today()
        self.year, self.month = selected.year, selected.month
        self.selected = selected
        head = ttk.Frame(self, padding=(10, 10, 10, 4))
        head.pack(fill="x")
        ttk.Button(head, text="‹", width=3, command=lambda: self.move(-1)).pack(side="left")
        self.heading = ttk.Label(head, anchor="center", width=20)
        self.heading.pack(side="left", fill="x", expand=True)
        ttk.Button(head, text="›", width=3, command=lambda: self.move(1)).pack(side="right")
        self.days = ttk.Frame(self, padding=10)
        self.days.pack()
        ttk.Button(self, text="今天", command=lambda: self.choose(date.today())).pack(pady=(0, 10))
        self.render()
        self.update_idletasks()
        self.geometry("+{}+{}".format(master.winfo_rootx() + 30, master.winfo_rooty() + 30))
        self.grab_set()

    def move(self, delta):
        month_index = self.year * 12 + self.month - 1 + delta
        year, month = month_index // 12, month_index % 12 + 1
        if 1 <= year <= 9999:
            self.year, self.month = year, month
            self.render()

    def render(self):
        self.heading.configure(text="{} 年 {} 月".format(self.year, self.month))
        for child in self.days.winfo_children():
            child.destroy()
        for col, label in enumerate("一二三四五六日"):
            ttk.Label(self.days, text=label, anchor="center", width=4).grid(row=0, column=col, pady=(0, 4))
        for row, week in enumerate(calendar.monthcalendar(self.year, self.month), 1):
            for col, day in enumerate(week):
                if day:
                    chosen = date(self.year, self.month, day)
                    ttk.Button(
                        self.days, text=str(day), width=4,
                        command=lambda chosen=chosen: self.choose(chosen),
                    ).grid(row=row, column=col, padx=1, pady=1)

    def choose(self, chosen):
        self.on_choose(chosen.isoformat())
        self.destroy()


class PendingReviewDialog(tk.Toplevel):
    """持有操作记录锁期间，仅保存用户的人工核对结论。"""

    def __init__(self, master, journal, emit):
        super().__init__(master)
        self.title("核对待确认记录")
        self.geometry("960x460")
        self.minsize(800, 380)
        self.transient(master)
        self.journal, self.emit = journal, emit
        self.key_by_item = {}
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="请先在 NCC 人工核对所选单据，再选择对应结论。确认未结算后，下次运行允许重试该单；此窗口不操作 NCC。", wraplength=900).grid(row=0, column=0, sticky="ew", pady=(0, 12))
        area = ttk.Frame(frame)
        area.grid(row=1, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.records = ttk.Treeview(area, columns=("date", "number", "identity"), show="headings", selectmode="browse")
        self.records.heading("date", text="业务日期")
        self.records.heading("number", text="单据编号")
        self.records.heading("identity", text="操作记录（日期 / 财务组织 / 单据）")
        self.records.column("date", width=110, stretch=False)
        self.records.column("number", width=180, stretch=False)
        self.records.column("identity", width=530)
        self.records.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.records.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.records.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.records.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.records.configure(xscrollcommand=horizontal.set)
        for index, key in enumerate(journal.pending()):
            entry = journal.entries[key]
            item = "record{}".format(index)
            self.key_by_item[item] = key
            self.records.insert("", "end", iid=item, values=(entry.get("business_date", key.split("|", 1)[0]), entry.get("number", key.rsplit("|", 1)[-1]), key))
        self.feedback = tk.StringVar(value="尚未保存核对结论。")
        ttk.Label(frame, textvariable=self.feedback, wraplength=900, style="Hint.TLabel").grid(row=2, column=0, sticky="ew", pady=(10, 8))
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="ew")
        self.confirm_button = ttk.Button(buttons, text="确认已结算", command=lambda: self.record_review("confirmed"))
        self.confirm_button.pack(side="left")
        self.retry_button = ttk.Button(buttons, text="确认未结算（允许重试）", command=lambda: self.record_review("reviewed_not_settled"))
        self.retry_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="取消 / 关闭", command=self.destroy).pack(side="right")
        self.records.bind("<<TreeviewSelect>>", self.update_buttons)
        self.update_buttons()
        self.grab_set()

    def update_buttons(self, event=None):
        state = "normal" if self.records.selection() else "disabled"
        self.confirm_button.configure(state=state)
        self.retry_button.configure(state=state)

    def record_review(self, state):
        if state not in ("confirmed", "reviewed_not_settled"):
            raise ValueError("不支持的人工核对结论")
        selected = self.records.selection()
        if not selected:
            return
        item = selected[0]
        key = self.key_by_item[item]
        entry = self.journal.entries[key]
        if entry.get("state") != "pending":
            messagebox.showerror("记录状态已变化", "该记录已经不属于待确认状态，请关闭后重新读取。", parent=self)
            return
        details = {name: value for name, value in entry.items() if name not in ("state", "time")}
        details.update(review_source="GUI人工核对", previous_state="pending", reviewed_at=datetime.now().isoformat(timespec="seconds"))
        details["evidence"] = "用户已在 NCC 人工核对，确认{}".format("已结算" if state == "confirmed" else "未结算，允许下次重试")
        try:
            self.journal.record(key, state, **details)
        except Exception as exc:
            messagebox.showerror("核对结论未保存", str(exc), parent=self)
            return
        self.records.delete(item)
        self.key_by_item.pop(item)
        conclusion = "已结算" if state == "confirmed" else "未结算，允许下次重试"
        self.emit("log", "人工核对结果已保存：{}；{}".format(key, conclusion))
        self.feedback.set("已保存：{}；{}。剩余待确认 {} 条。".format(entry.get("number", key), conclusion, len(self.key_by_item)))
        self.update_buttons()


class TimeSettingsDialog(tk.Toplevel):
    """只编辑草稿，保存成功后才更新主界面；取消不改变任何设置。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("时间设置")
        self.geometry("760x620")
        self.minsize(700, 580)
        self.transient(app.root)
        self.variables = {name: tk.StringVar(self, value=var.get()) for name, var in app.time_vars.items()}
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="所有时间均可保存。修改后下次开始运行时使用；恢复默认只重置时间参数。", wraplength=700).pack(fill="x", pady=(0, 12))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        descriptions = {
            "操作与循环": "普通操作后停顿统一用于点击、输入、按键和表格滚动。最长等待是检查窗口，条件满足就继续；连续稳定时间用于确认页面确实完成更新。",
            "超时": "点击最长等待取本项与“页面条件最长等待”中较短值。成功提示及目标消失核验分别使用自己的窗口。核验中的查询、翻页也有等待，总耗时可能超过单个窗口。",
            "检查与刷新": "这些项目控制检查频率和界面刷新，数值越小检查越频繁。后台保活由工作线程在操作及等待期间刷新。",
        }
        for group in ("操作与循环", "超时", "检查与刷新"):
            panel = ttk.Frame(notebook, padding=14)
            panel.columnconfigure(0, weight=1)
            notebook.add(panel, text=group)
            options = [option for option in TIME_OPTIONS if option[-1] == group]
            for row, (name, label, unit, minimum, maximum, increment, _) in enumerate(options):
                ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=6)
                ttk.Spinbox(panel, from_=minimum, to=maximum, increment=increment, textvariable=self.variables[name], width=10).grid(row=row, column=1, padx=(10, 8), pady=6)
                ttk.Label(panel, text=unit).grid(row=row, column=2, sticky="w")
                ttk.Label(panel, text="{:g}～{:g}".format(minimum, maximum), style="Hint.TLabel").grid(row=row, column=3, sticky="w", padx=(18, 0))
            ttk.Label(panel, text=descriptions[group], wraplength=650, style="Hint.TLabel").grid(row=len(options), column=0, columnspan=4, sticky="ew", pady=(18, 0))
        self.feedback = tk.StringVar(value="保存后关闭程序再打开，时间设置仍会保留。")
        ttk.Label(outer, textvariable=self.feedback, wraplength=700, style="Hint.TLabel").pack(fill="x", pady=12)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="恢复默认", command=self.restore_defaults).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存并关闭", command=self.save).pack(side="right", padx=8)
        self.grab_set()

    def restore_defaults(self):
        defaults = Settings()
        for name, variable in self.variables.items():
            variable.set("{:g}".format(float(getattr(defaults, name))))
        self.feedback.set("已填入全部默认时间，点击“保存并关闭”后生效；取消则放弃本次修改。")

    def save(self):
        try:
            values = validate_time_options({name: var.get() for name, var in self.variables.items()})
            self.app.save_time_settings(values)
        except Exception as exc:
            messagebox.showerror("时间设置未保存", str(exc), parent=self)
            return False
        self.destroy()
        return True


class SkipListResultsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title('清单读取结果核对')
        self.geometry('760x610')
        self.minsize(650, 450)
        self.transient(app.root)
        self.mode = tk.StringVar(value='最近一次')
        self.summary = tk.StringVar()
        self.detail = tk.StringVar()
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill='both', expand=True)
        bar = ttk.Frame(outer)
        bar.pack(fill='x')
        ttk.Label(bar, text='查看结果').pack(side='left')
        choice = ttk.Combobox(bar, textvariable=self.mode, values=['最近一次', '正式运行', '调试读取'], state='readonly', width=14)
        choice.pack(side='left', padx=8)
        choice.bind('<<ComboboxSelected>>', lambda _:self.reload())
        ttk.Button(bar, text='刷新显示', command=self.reload).pack(side='right')
        ttk.Label(outer, textvariable=self.summary, wraplength=700).pack(fill='x', pady=(14, 6))
        ttk.Label(outer, textvariable=self.detail, style='Hint.TLabel', wraplength=700).pack(fill='x', pady=(0, 12))
        table_box = ttk.Frame(outer)
        table_box.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table_box, columns=('cell', 'number'), show='headings', height=15)
        self.table.heading('cell', text='原单元格')
        self.table.heading('number', text='单据号')
        self.table.column('cell', width=110, stretch=False, anchor='center')
        self.table.column('number', width=450, anchor='w')
        self.table.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(table_box, orient='vertical', command=self.table.yview)
        scrollbar.pack(side='right', fill='y')
        self.table.configure(yscrollcommand=scrollbar.set)
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=(12, 0))
        self.csv_button = ttk.Button(actions, text='用 Excel 打开 CSV', command=self.open_csv)
        self.csv_button.pack(side='left')
        ttk.Button(actions, text='打开结果目录', command=self.open_folder).pack(side='left', padx=8)
        ttk.Button(actions, text='关闭', command=self.destroy).pack(side='right')
        self.reload()

    def reload(self):
        self.table.delete(*self.table.get_children())
        self.csv_button.configure(state='disabled')
        self.snapshot = None
        mode = {'正式运行':'run', '调试读取':'debug'}.get(self.mode.get())
        try:
            data = SkipListResultStore.load(self.app.base_dir, mode)
        except Exception as exc:
            self.summary.set('结果未能读取：' + str(exc))
            self.detail.set('可以打开结果目录查看文件。')
            return
        if data is None:
            self.summary.set('尚无读取结果。先点击“调试：仅读清单”，或完成一次正式读取。')
            self.detail.set(str(SkipListResultStore(self.app.base_dir).folder))
            return
        self.snapshot = data
        label = data.get('modeLabel', '')
        stamp = data.get('readAt') or data.get('attemptAt', '')
        day = data.get('businessDate') or '启动检查'
        self.detail.set('工作表：{}\n文件编号：{}　版本：{}　范围：{}\n读取时间：{}　业务日期：{}'.format(
            data.get('sheet',''), data.get('fileId',''), data.get('fileVersion') or '网页未提供',
            data.get('range','未确认'), stamp, day))
        if data['status'] != 'success':
            self.summary.set('{}：{}'.format(label, data.get('message', '本次尚未成功读取。')))
            return
        self.summary.set('{}：读取成功，{} 个编号，共 {} 行。'.format(label, data['uniqueCount'], data['recordCount']))
        for record in data['records']:
            self.table.insert('', 'end', values=(record['cell'], record['billNumber']))
        self.csv_button.configure(state='normal')

    def open_folder(self):
        folder = SkipListResultStore(self.app.base_dir).folder
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def open_csv(self):
        if not self.snapshot or self.snapshot['status'] != 'success':
            return
        mode = self.snapshot.get('mode')
        if mode not in ('run', 'debug'):
            return
        # 只打开程序自己的固定 CSV 路径，不执行结果 JSON 中给出的任意路径。
        path = SkipListResultStore(self.app.base_dir, mode).csv_path
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror('无法打开 CSV', str(exc), parent=self)


class SettlementApp:
    def __init__(self, root, base_dir=None, auto_update=True):
        self.root = root
        self.base_dir = Path(base_dir or application_directory())
        self.settings_path = self.base_dir / "出纳结算设置.json"
        self.events = queue.Queue()
        self.worker = None
        self.updater = UpdateManager(self.base_dir)
        self.update_dialog = None
        self._update_installing = False
        self._update_after_id = None
        self._startup_update_id = None
        self.task_mode = 'run'
        self.results_dialog = None
        self.control = None
        self.closing = False
        self.logs_dir = self.base_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("ncc_cashier_gui.{}".format(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.log_file = self.logs_dir / "出纳结算_{}.log".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.log_handler = RotatingFileHandler(str(self.log_file), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(self.log_handler)
        load_error = None
        try:
            self.settings = load_settings(self.settings_path)
        except Exception as exc:
            self.settings = Settings()
            load_error = "配置未能读取，已使用默认设置：{}".format(exc)
        today = date.today()
        self.date_mode = tk.StringVar(value=self.settings.date_mode)
        self.start_date = tk.StringVar(value=self.settings.start_date or today.replace(day=1).isoformat())
        self.end_date = tk.StringVar(value=self.settings.end_date or today.isoformat())
        self.sleep_minutes = tk.StringVar(value="{:g}".format(float(self.settings.sleep_minutes)))
        self.browser_path = tk.StringVar(value=self.settings.browser_path)
        self.skip_list_url = tk.StringVar(value=self.settings.skip_list_url)
        self.action_delay_seconds = tk.StringVar(value="{:g}".format(float(self.settings.action_delay_seconds)))
        self.response_timeout_seconds = tk.StringVar(value="{:g}".format(float(self.settings.response_timeout_seconds)))
        self.time_vars = {name: tk.StringVar(value="{:g}".format(float(getattr(self.settings, name)))) for name, *_ in TIME_OPTIONS}
        self.time_vars.update(sleep_minutes=self.sleep_minutes, action_delay_seconds=self.action_delay_seconds, response_timeout_seconds=self.response_timeout_seconds)
        self._log_after_id = self._date_after_id = None
        self.range_text = tk.StringVar()
        self.status = tk.StringVar(value="等待开始")
        self.read_result_status = tk.StringVar(value='尚未读取清单')
        self.update_status = tk.StringVar(value='每次启动自动检查更新')
        self._build()
        self._update_date_mode()
        self.root.protocol("WM_DELETE_WINDOW", self.close_window)
        self._schedule_refreshes()
        self.emit("log", "程序 " + APP_VERSION + " 已就绪。每次开始先打开文档等待你手动登录钉钉，确认成功后再打开 NCC；两项登录完成后可最小化后台运行。")
        self.emit("log", "当前时间设置：操作间隔 {:g} 秒，页面最长等待 {:g} 秒。".format(float(self.settings.action_delay_seconds), float(self.settings.response_timeout_seconds)))
        if load_error:
            self.emit("warning", load_error)
        self._poll_update_status()
        if auto_update:
            self._startup_update_id = self.root.after(1200,self._startup_check_update)

    def _build(self):
        self.root.title("NCC 结算（出纳）循环工具 " + APP_VERSION)
        self.root.geometry("940x930")
        self.root.minsize(800, 830)
        self.root.configure(background="#f3f5f8")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f3f5f8")
        style.configure("TLabel", background="#f3f5f8", font=("Microsoft YaHei UI", 10))
        style.configure("TRadiobutton", background="#f3f5f8", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("TLabelframe", background="#f3f5f8")
        style.configure("TLabelframe.Label", background="#f3f5f8", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"), foreground="#19364b")
        style.configure("Hint.TLabel", foreground="#5c6a76", font=("Microsoft YaHei UI", 9))
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(6, weight=1)
        header=ttk.Frame(outer)
        header.grid(row=0,column=0,sticky='ew')
        ttk.Label(header, text="结算（出纳）循环工具", style="Title.TLabel").pack(side='left')
        ttk.Button(header,text='自动更新…',command=self.open_update_dialog).pack(side='right')
        ttk.Label(outer, text="登录完成后可最小化浏览器和本窗口，后台继续运行。电脑保持开机且不睡眠即可。", style="Hint.TLabel", wraplength=850).grid(row=1, column=0, sticky="ew", pady=(5, 16))

        dates = ttk.LabelFrame(outer, text="处理日期", padding=14)
        dates.grid(row=2, column=0, sticky="ew")
        dates.columnconfigure(1, weight=1)
        self.mode_month = ttk.Radiobutton(dates, text="默认范围：系统当月 1 日至今天", variable=self.date_mode, value="month_to_date", command=self._update_date_mode)
        self.mode_month.grid(row=0, column=0, columnspan=5, sticky="w")
        self.mode_custom = ttk.Radiobutton(dates, text="自定义范围", variable=self.date_mode, value="custom", command=self._update_date_mode)
        self.mode_custom.grid(row=1, column=0, sticky="w", pady=(10, 0))
        custom = ttk.Frame(dates)
        custom.grid(row=1, column=1, columnspan=4, sticky="w", pady=(10, 0))
        self.start_entry = ttk.Entry(custom, textvariable=self.start_date, width=13)
        self.start_entry.pack(side="left")
        self.start_pick = ttk.Button(custom, text="选日期", command=lambda: DatePicker(self.start_entry, self.start_date.get(), self.start_date.set))
        self.start_pick.pack(side="left", padx=(5, 12))
        ttk.Label(custom, text="至").pack(side="left", padx=(0, 12))
        self.end_entry = ttk.Entry(custom, textvariable=self.end_date, width=13)
        self.end_entry.pack(side="left")
        self.end_pick = ttk.Button(custom, text="选日期", command=lambda: DatePicker(self.end_entry, self.end_date.get(), self.end_date.set))
        self.end_pick.pack(side="left", padx=5)
        ttk.Label(dates, textvariable=self.range_text, style="Hint.TLabel").grid(row=2, column=0, columnspan=5, sticky="w", pady=(12, 0))
        self.start_date.trace_add("write", lambda *_: self._update_range_preview())
        self.end_date.trace_add("write", lambda *_: self._update_range_preview())

        options = ttk.LabelFrame(outer, text="循环设置", padding=14)
        options.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        options.columnconfigure(1, weight=1)
        line = ttk.Frame(options)
        line.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(line, text="每轮完成后休眠").pack(side="left")
        self.sleep_entry = ttk.Spinbox(line, from_=0.1, to=1440, increment=1, textvariable=self.sleep_minutes, width=8)
        self.sleep_entry.pack(side="left", padx=8)
        ttk.Label(line, text="分钟，再从起始日期重新检查").pack(side="left")
        pacing = ttk.Frame(options)
        pacing.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(pacing, text="操作间隔（秒）").pack(side="left")
        self.action_delay_entry = ttk.Spinbox(pacing, from_=0.5, to=30, increment=0.5, textvariable=self.action_delay_seconds, width=8)
        self.action_delay_entry.pack(side="left", padx=(8, 20))
        ttk.Label(pacing, text="页面最长等待（秒）").pack(side="left")
        self.response_timeout_entry = ttk.Spinbox(pacing, from_=5, to=600, increment=5, textvariable=self.response_timeout_seconds, width=8)
        self.response_timeout_entry.pack(side="left", padx=8)
        self.time_button = ttk.Button(pacing, text="时间设置…", command=self.open_time_settings)
        self.time_button.pack(side="left", padx=(12, 0))
        ttk.Label(options, text="“时间设置…”可集中修改各项等待、保存或恢复默认。运行前调整，页面响应较慢时可调大。", style="Hint.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(options, text="浏览器路径").grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.browser_entry = ttk.Entry(options, textvariable=self.browser_path)
        self.browser_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=(12, 0))
        self.browser_browse = ttk.Button(options, text="浏览…", command=self.choose_browser)
        self.browser_browse.grid(row=3, column=2, pady=(12, 0))
        ttk.Label(options, text="默认使用之前收款结算单的浏览器，路径留空自动识别；找不到时使用程序同目录内置浏览器。\n“浏览…”可选手动调整。左侧业务单据日期查询范围保持 NCC 原值。", style="Hint.TLabel", wraplength=810).grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(options, text="跳过清单网址").grid(row=5, column=0, sticky="w", pady=(12, 0))
        self.skip_list_entry = ttk.Entry(options, textvariable=self.skip_list_url)
        self.skip_list_entry.grid(row=5, column=1, columnspan=2, sticky="ew", padx=8, pady=(12, 0))
        ttk.Label(options, text="每次开始先打开文档等待手动登录，确认可读取 Sheet3 后才进入 NCC。\n每个日期开始前重新刷新清单，命中编号自动跳过；读取失败则停止。", style="Hint.TLabel", wraplength=810).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        debug_line = ttk.Frame(options)
        debug_line.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        self.debug_button = ttk.Button(debug_line, text='调试：仅读清单', command=self.start_debug)
        self.debug_button.pack(side='left')
        self.read_results_button = ttk.Button(debug_line, text='核对读取结果', command=self.open_read_results)
        self.read_results_button.pack(side='left', padx=8)
        ttk.Label(debug_line, textvariable=self.read_result_status, style='Hint.TLabel', wraplength=470).pack(side='left')

        actions = ttk.Frame(outer)
        actions.grid(row=4, column=0, sticky="ew", pady=(16, 10))
        self.start_button = ttk.Button(actions, text="开始运行", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止任务", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.save_button = ttk.Button(actions, text="保存设置", command=self.save)
        self.save_button.pack(side="left")
        self.review_button = ttk.Button(actions, text="核对待确认记录", command=self.review_pending)
        self.review_button.pack(side="left", padx=8)
        ttk.Button(actions, text="打开日志目录", command=self.open_logs).pack(side="right")
        ttk.Label(outer, textvariable=self.status, foreground="#1f5c7c", wraplength=840).grid(row=5, column=0, sticky="ew", pady=(0, 8))
        log_box = ttk.Frame(outer)
        log_box.grid(row=6, column=0, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_box, wrap="word", height=10, state="disabled", background="#ffffff", foreground="#263a48", relief="flat", padx=10, pady=10, font=("Microsoft YaHei UI", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("error", foreground="#b32929")
        self.log_text.tag_configure("warning", foreground="#9c650b")
        ttk.Label(outer,textvariable=self.update_status,style='Hint.TLabel',wraplength=850).grid(row=7,column=0,sticky='ew',pady=(10,0))

    def task_running(self):
        return self.worker is not None and self.worker.is_alive()

    def _startup_check_update(self):
        self._startup_update_id=None
        self.check_update()

    def check_update(self):
        if not self._update_installing:
            threading.Thread(target=self.updater.check,name='NCCUpdateCheck',daemon=True).start()

    def _poll_update_status(self):
        self.update_status.set('更新：'+self.updater.status()['message'])
        self._update_after_id=self.root.after(500,self._poll_update_status)

    def open_update_dialog(self):
        from update_dialog import UpdateDialog
        if self.update_dialog is not None and self.update_dialog.winfo_exists():
            self.update_dialog.lift()
        else:
            self.update_dialog=UpdateDialog(self)

    def install_update(self):
        if self.task_running():
            return
        try:
            self._update_installing=True
            self.updater.start_installer(self.task_running)
        except Exception as exc:
            self._update_installing=False
            self.updater._state(status='error',message=str(exc))
            return
        self._set_running(True)
        self._destroy()

    def current_settings(self, time_values=None, validate_dates=True):
        values = validate_time_options(time_values if time_values is not None else {name: var.get() for name, var in self.time_vars.items()})
        settings = Settings(
            date_mode=self.date_mode.get(), start_date=self.start_date.get().strip(),
            end_date=self.end_date.get().strip(),
            browser_path=self.browser_path.get().strip(),
            skip_list_url=self.skip_list_url.get().strip(),
            **values
        )
        if validate_dates:
            resolve_date_range(settings)
        else:
            from skip_list import latest_preview_url
            latest_preview_url(settings.skip_list_url)
        if settings.browser_path and not Path(settings.browser_path).is_file():
            raise ValueError("指定的浏览器文件不存在，请重新选择 chrome.exe 或将路径留空。")
        return settings

    def _update_date_mode(self):
        running = self.worker is not None and self.worker.is_alive()
        custom_state = "normal" if self.date_mode.get() == "custom" and not running else "disabled"
        for widget in (self.start_entry, self.end_entry, self.start_pick, self.end_pick):
            widget.configure(state=custom_state)
        self._update_range_preview()

    def _update_range_preview(self):
        if self.date_mode.get() == "month_to_date":
            today = date.today()
            text = "本轮默认范围：{} 至 {}（每轮按系统日期重新计算）".format(today.replace(day=1), today)
        else:
            text = "自定义范围：{} 至 {}（包含起止日期，格式 YYYY-MM-DD）".format(self.start_date.get(), self.end_date.get())
        self.range_text.set(text)

    def _refresh_date_preview(self):
        self._update_range_preview()
        self._date_after_id = self.root.after(self._refresh_milliseconds("gui_date_refresh_seconds"), self._refresh_date_preview)

    def _refresh_milliseconds(self, name):
        return max(1, int(float(getattr(self.settings, name)) * 1000))

    def _schedule_refreshes(self):
        for timer in (self._log_after_id, self._date_after_id):
            if timer is not None:
                self.root.after_cancel(timer)
        self._log_after_id = self.root.after(self._refresh_milliseconds("gui_log_refresh_seconds"), self._drain_events)
        self._date_after_id = self.root.after(self._refresh_milliseconds("gui_date_refresh_seconds"), self._refresh_date_preview)

    def open_time_settings(self):
        if self.worker is not None and self.worker.is_alive():
            return None
        return TimeSettingsDialog(self)

    def save_time_settings(self, values):
        if self.worker is not None and self.worker.is_alive():
            raise ValueError("请先停止任务，再修改时间设置。")
        settings = self.current_settings(values)
        save_settings(settings, self.settings_path)
        self.settings = settings
        for name, variable in self.time_vars.items():
            variable.set("{:g}".format(float(getattr(settings, name))))
        self._schedule_refreshes()
        self.emit("log", "全部时间设置已保存，下次开始运行时使用。")

    def choose_browser(self):
        selected = filedialog.askopenfilename(parent=self.root, title="选择 Chromium / Chrome 浏览器", filetypes=[("浏览器程序", "*.exe"), ("所有文件", "*.*")])
        if selected:
            self.browser_path.set(selected)

    def save(self):
        try:
            settings = self.current_settings()
            save_settings(settings, self.settings_path)
        except Exception as exc:
            messagebox.showerror("设置未保存", str(exc), parent=self.root)
            return False
        self.settings = settings
        self._schedule_refreshes()
        self.emit("log", "设置已保存。")
        return True

    def start(self):
        if self._update_installing or self.task_running():
            return
        try:
            settings = self.current_settings()
            save_settings(settings, self.settings_path)
        except Exception as exc:
            messagebox.showerror("请检查设置", str(exc), parent=self.root)
            return
        self.settings = settings
        self.task_mode = 'run'
        self.control = Control(poll_interval=float(settings.control_poll_seconds))
        self.emit("log", "本次运行时间设置：操作间隔 {:g} 秒，页面最长等待 {:g} 秒。".format(float(settings.action_delay_seconds), float(settings.response_timeout_seconds)))
        self.emit("log", "全部时间参数：" + "；".join("{} {:g} {}".format(label, float(getattr(settings, name)), unit) for name, label, unit, *_ in TIME_OPTIONS))
        self._set_running(True)
        self.status.set("正在打开文档，请准备手动登录钉钉…")
        self.worker = threading.Thread(target=self._run_worker, args=(settings, self.control), name="NCCCashierWorker", daemon=False)
        self.worker.start()

    def start_debug(self):
        if self._update_installing or self.task_running():
            return
        try:
            settings = self.current_settings(validate_dates=False)
        except Exception as exc:
            messagebox.showerror('请检查读取设置', str(exc), parent=self.root)
            return
        self.task_mode = 'debug'
        self.control = Control(poll_interval=float(settings.control_poll_seconds))
        self._set_running(True)
        self.status.set('调试仅读取文档：等待手动登录钉钉，不会进入 NCC。')
        self.emit('log', '开始调试读取：本次只读取 Sheet3，不打开 NCC，不执行结算。')
        self.worker = threading.Thread(target=self._run_debug_worker, args=(settings, self.control),
            name='SkipListReadOnlyDebug', daemon=False)
        self.worker.start()

    def _run_debug_worker(self, settings, control):
        error_message, stopped = None, False
        try:
            from skip_list_debug import run_debug
            run_debug(settings, control, self.emit, self.base_dir)
        except StopRequested:
            stopped = True
            self.emit('log', '已停止调试读取。')
        except BaseException as exc:
            error_message = str(exc) or type(exc).__name__
            self.logger.error('调试读取异常\n%s', traceback.format_exc())
            self.emit('error', '调试读取失败：' + error_message)
        finally:
            self.events.put(('finished', {'error':error_message,
                'stopped':stopped or control.stop_event.is_set(), 'mode':'debug'}))

    def open_read_results(self):
        if self.results_dialog is not None and self.results_dialog.winfo_exists():
            self.results_dialog.reload()
            return self.results_dialog
        self.results_dialog = SkipListResultsDialog(self)
        return self.results_dialog

    def _run_worker(self, settings, control):
        adapter = None
        error_message = None
        stopped = False
        try:
            from ncc_browser import NccBrowserAdapter
            adapter = NccBrowserAdapter(settings, control, self.emit, self.base_dir)
            AutomationRunner(adapter, settings, control, self.emit).run()
        except StopRequested:
            stopped = True
            self.emit("log", "已收到停止信号。")
        except BaseException as exc:
            error_message = str(exc) or type(exc).__name__
            self.logger.error("任务异常\n%s", traceback.format_exc())
            self.emit("error", "任务已停止：{}".format(error_message))
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    self.logger.error("关闭本任务浏览器失败\n%s", traceback.format_exc())
                    self.emit("warning", "关闭本任务浏览器时出现异常，详情已写入日志。")
            self.events.put(("finished", {"error": error_message, "stopped": stopped or control.stop_event.is_set()}))

    def stop(self):
        if self.control is not None:
            self.control.stop_event.set()
            self.stop_button.configure(state="disabled")
            self.status.set("正在停止，等待当前操作结束并关闭本任务浏览器…")
            self.emit("log", "用户请求停止任务。")

    def review_pending(self):
        if self.worker is not None and self.worker.is_alive():
            return
        lock = None
        try:
            from ncc_browser import CommitJournal
            from ncc_safety import SingleInstanceLock
            path = self.logs_dir / "结算操作记录.json"
            lock = SingleInstanceLock(path).acquire()
            journal = CommitJournal(path)
            if not journal.pending():
                messagebox.showinfo("无需核对", "当前没有待确认的结算记录。", parent=self.root)
                return
            dialog = PendingReviewDialog(self.root, journal, self.emit)
            self.root.wait_window(dialog)
        except Exception as exc:
            self.emit("error", "读取或核对待确认记录失败：{}".format(exc))
            messagebox.showerror("无法核对记录", str(exc), parent=self.root)
        finally:
            if lock is not None:
                lock.release()

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        for widget in (self.start_button, self.debug_button, self.save_button, self.time_button, self.review_button, self.mode_month, self.mode_custom, self.sleep_entry, self.action_delay_entry, self.response_timeout_entry, self.browser_entry, self.browser_browse, self.skip_list_entry):
            widget.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        self._update_date_mode()
        if running:
            for widget in (self.start_entry, self.end_entry, self.start_pick, self.end_pick):
                widget.configure(state="disabled")

    def emit(self, kind, text):
        if kind == 'skip_result':
            self.logger.info('清单读取状态：%s；模式：%s；编号数：%s；核对文件：%s',
                text['status'], text.get('modeLabel'), text.get('uniqueCount', '未确认'), text.get('jsonPath'))
            self.events.put((kind, text))
            return
        text = str(text)
        if kind == "sleep":
            try:
                text = "进入休眠，预计 {:g} 分钟后开始下一轮。".format(float(text) / 60)
            except ValueError:
                text = "进入休眠：" + text
        level = logging.ERROR if kind == "error" else logging.WARNING if kind == "warning" else logging.INFO
        self.logger.log(level, text)
        self.events.put((kind, text))

    def _append_log(self, kind, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "[{}] {}\n".format(datetime.now().strftime("%H:%M:%S"), text), kind)
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _drain_events(self):
        for _ in range(200):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'skip_result':
                if payload['status'] == 'success':
                    self.read_result_status.set('{}：{} 个编号，{}'.format(payload['modeLabel'], payload['uniqueCount'], payload['readAt'][11:19]))
                else:
                    self.read_result_status.set(payload.get('message', '本次尚未确认读取结果'))
                if self.results_dialog is not None and self.results_dialog.winfo_exists():
                    self.results_dialog.reload()
                continue
            if kind == "finished":
                self.worker = None
                self._set_running(False)
                if payload["error"]:
                    self.status.set("任务因异常停止，请查看日志。")
                elif payload["stopped"]:
                    self.status.set("任务已停止")
                else:
                    self.status.set('调试读取完成，请点击“核对读取结果”。' if payload.get('mode') == 'debug' else "任务已结束")
                if self.closing:
                    self._destroy()
                    return
                if payload["error"]:
                    if payload.get('mode') == 'debug':
                        messagebox.showerror('调试读取失败', '{}\n\n本次未进入 NCC。\n日志：{}'.format(payload['error'], self.log_file), parent=self.root)
                    else:
                        messagebox.showerror("任务停止", "{}\n\n如果日志显示结算结果不明，请先在 NCC 人工核对对应单据。\n日志：{}".format(payload["error"], self.log_file), parent=self.root)
                continue
            self._append_log(kind, payload)
            if kind in ("status", "state", "progress"):
                self.status.set(payload)
        self._log_after_id = self.root.after(self._refresh_milliseconds("gui_log_refresh_seconds"), self._drain_events)

    def open_logs(self):
        try:
            os.startfile(str(self.logs_dir))
        except (OSError, AttributeError) as exc:
            messagebox.showinfo("日志目录", "{}\n\n{}".format(self.logs_dir, exc), parent=self.root)

    def close_window(self):
        if self.worker is not None and self.worker.is_alive():
            self.closing = True
            self.stop()
            self.status.set("正在停止任务，完成后窗口会自动关闭…")
            return
        self._destroy()

    def _destroy(self):
        for timer in (self._log_after_id, self._date_after_id,self._update_after_id,self._startup_update_id):
            if timer is not None:
                self.root.after_cancel(timer)
        self.log_handler.close()
        self.logger.removeHandler(self.log_handler)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = SettlementApp(root)
    if "--smoke" in sys.argv:
        root.after(400, app.close_window)
    elif '--debug-read' in sys.argv:
        root.after(100, app.start_debug)
    root.mainloop()


if __name__ == "__main__":
    main()
