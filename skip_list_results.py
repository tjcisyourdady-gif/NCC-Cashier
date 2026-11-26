"""保存可核对的读取快照，仅包含单据编号和来源，不保存认证信息。"""
import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HK = timezone(timedelta(hours=8))


def timestamp():
    return datetime.now(HK).isoformat(timespec='seconds')


def atomic_write(path, text, encoding='utf-8'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding=encoding, newline='',
                prefix=path.name + '.', suffix='.tmp', dir=str(path.parent), delete=False) as stream:
            name = stream.name
            stream.write(text)
        os.replace(name, str(path))
        name = None
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


class SkipListResultStore:
    def __init__(self, base_dir, mode='run', folder=None):
        if mode not in ('run', 'debug'):
            raise ValueError('读取模式无效')
        self.folder = Path(folder) if folder is not None else Path(base_dir) / 'logs' / '清单读取结果'
        self.mode = mode
        self.mode_label = '调试读取' if mode == 'debug' else '正式运行'
        self.latest = self.folder / '最新读取结果.json'
        self.csv_path = self.folder / (self.mode_label + '_单据号.csv')

    def _write(self, snapshot):
        # 仅选择明确允许的字段，浏览器会话、密钥和请求参数不得进入核对文件。
        allowed = ('status', 'attemptAt', 'readAt', 'businessDate', 'sheet', 'range',
                   'fileId', 'fileVersion', 'recordCount', 'uniqueCount', 'records', 'message')
        data = {key: snapshot[key] for key in allowed if key in snapshot}
        data['records'] = [{'cell':r['cell'], 'billNumber':r['billNumber']} for r in data.get('records', [])]
        data.update(mode=self.mode, modeLabel=self.mode_label,
                    jsonPath=str(self.latest), csvPath=str(self.csv_path))
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['原单元格', '单据号'])
        if data['status'] == 'success':
            writer.writerows((r['cell'], r['billNumber']) for r in data['records'])
        content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        # 读取中和失败先使旧成功标记失效，再清空对应 CSV。
        if data['status'] != 'success':
            atomic_write(self.latest, content)
            atomic_write(self.folder / (self.mode_label + '_最近读取.json'), content)
        atomic_write(self.csv_path, output.getvalue(), 'utf-8-sig')
        atomic_write(self.folder / (self.mode_label + '_最近读取.json'), content)
        if data['status'] == 'success' and self.mode == 'run':
            day = data.get('businessDate') or '启动检查'
            history = self.folder / ('正式运行_' + day + '.json')
            history_data = dict(data, jsonPath=str(history), csvPath='')
            atomic_write(history, json.dumps(history_data, ensure_ascii=False, indent=2) + '\n')
        atomic_write(self.latest, content)
        return data

    def begin(self, business_date, sheet, file_id):
        return self._write(dict(status='reading', attemptAt=timestamp(),
            businessDate=business_date.isoformat() if business_date is not None else None,
            sheet=sheet, fileId=file_id, records=[], message='正在读取，尚未确认结果。'))

    def success(self, snapshot):
        return self._write(dict(snapshot, status='success'))

    def failure(self, attempt, stopped=False):
        return self._write(dict(attempt, status='stopped' if stopped else 'failed', records=[],
            message='本次读取已停止。' if stopped else '本次读取失败，不能将旧结果当作当前清单。'))

    @staticmethod
    def load(base_dir, mode=None):
        folder = Path(base_dir) / 'logs' / '清单读取结果'
        name = {'run':'正式运行_最近读取.json', 'debug':'调试读取_最近读取.json'}.get(mode, '最新读取结果.json')
        path = folder / name
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('status') not in ('reading', 'success', 'failed', 'stopped'):
            raise ValueError('读取结果文件格式无效')
        records = data.get('records', [])
        if not isinstance(records, list) or any(not isinstance(r, dict) or not isinstance(r.get('cell'), str)
                or not isinstance(r.get('billNumber'), str) for r in records):
            raise ValueError('读取结果明细格式无效')
        if data['status'] == 'success':
            if (data.get('recordCount') != len(records) or data.get('uniqueCount') != len({r['billNumber'] for r in records})
                    or not isinstance(data.get('readAt'), str) or data.get('mode') not in ('run', 'debug')
                    or any(re.fullmatch(r'D[1-9][0-9]*', r['cell']) is None
                           or re.fullmatch(r'F[0-9]{10,}', r['billNumber']) is None for r in records)):
                raise ValueError('读取结果数量或编号校验失败')
        elif records:
            raise ValueError('未成功的读取结果不能包含有效编号')
        return data
