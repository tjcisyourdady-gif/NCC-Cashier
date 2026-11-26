"""刷新钉钉预览，并通过只读接口取得取消结算清单；不使用桌面输入。"""
import base64
import hashlib
import hmac
import json
import math
import re
import struct
import time
from collections import defaultdict
from email.utils import formatdate
from urllib.parse import parse_qs, urlencode, unquote, urlsplit, urlunsplit
from skip_list_results import timestamp


SHEET_NAME = '3、取消结算清单【慎重填报】'
DEFAULT_SKIP_LIST_URL = ''
BILL_PATTERN = re.compile(r'F\d{10,}', re.ASCII)


class SkipListError(RuntimeError):
    pass


def latest_preview_url(value):
    """固定文件身份，但不将循环锁在复制链接时的历史版本。"""
    url = urlsplit(str(value).strip())
    if (url.scheme != 'https' or url.hostname != 'alidocs.dingtalk.com'
            or url.path != '/uni-preview' or url.username or url.password or url.port):
        raise ValueError('跳过清单须填写钉钉表格的 HTTPS 预览网址。')
    query = parse_qs(url.query, keep_blank_values=True)
    ids = query.get('fileId', query.get('cloudSpaceDentryId', []))
    if len(ids) != 1 or not ids[0].isdigit():
        raise ValueError('跳过清单网址缺少有效的文件编号。')
    for key in ('version', 'fileSize'):
        query.pop(key, None)
    if any(key.lower() in ('accesskeyid', 'accesskeysecret', 'ststoken', 'token', 'aliyuncontent') for key in query):
        raise ValueError('请填写文件预览网址，不要填写含临时登录信息的内部网址。')
    return urlunsplit(('https', url.netloc, url.path, urlencode(query, doseq=True), ''))


def bill_number(row):
    number = row.values.get('业务单据编号')
    if number is None:
        number = str(row.key or '').rsplit('|', 1)[-1]
    return str(number).strip().upper()


class PreviewDecoder:
    """WPS V3.9 二进制预览字段格式，依据实际预览响应解析。"""
    PRIMITIVES = {1:'b', 2:'B', 3:'h', 4:'H', 5:'i', 6:'I', 7:'f', 8:'d', 10:'I', 11:'d'}

    def __init__(self, data):
        self.data, self.offset, self.fields = data, 0, {}

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise SkipListError('跳过清单数据不完整，未继续结算。')
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def text(self, size, unicode=False):
        return self.take(size * (2 if unicode else 1)).decode('utf-16-le' if unicode else 'latin1')

    def descriptor(self):
        field = self.fields.get(self.number('H'))
        if field is None:
            raise SkipListError('跳过清单出现未知字段，未继续结算。')
        return field

    def object(self, depth):
        result = {}
        while self.offset < len(self.data):
            name, kind = self.descriptor()
            if kind == 15:
                return result
            if name in result:
                raise SkipListError('跳过清单字段重复。')
            result[name] = self.value(kind, depth + 1)
        if depth:
            raise SkipListError('跳过清单嵌套数据不完整。')
        return result

    def value(self, kind, depth):
        if depth > 100:
            raise SkipListError('跳过清单数据嵌套异常。')
        if kind in self.PRIMITIVES:
            return self.number(self.PRIMITIVES[kind])
        if kind == 9:
            return self.number('B') > 0
        if kind in (12, 13):
            return self.text(self.number('I' if kind == 12 else 'B'), kind == 12)
        if kind == 14:
            return self.object(depth)
        if kind == 16:
            length = self.number('I')
            if length > len(self.data):
                raise SkipListError('跳过清单数组长度异常。')
            if not length:
                return []
            subtype = self.number('H')
            return [self.value(self.descriptor()[1] if subtype in (14, 16) else subtype, depth + 1)
                    for _ in range(length)]
        if kind == 17:
            return self.take(self.number('I'))
        raise SkipListError('跳过清单预览格式不受支持。')

    def decode(self):
        for _ in range(self.number('H')):
            index = self.number('H')
            name = self.text(self.number('B'))
            kind = self.number('H')
            if index in self.fields:
                raise SkipListError('跳过清单字段字典重复。')
            self.fields[index] = (name, kind)
        result = self.object(0)
        if self.offset != len(self.data):
            raise SkipListError('跳过清单仍有未解析数据。')
        return result


def column_pages(meta):
    try:
        region, pages = meta['rg'], meta['p']
        widths, heights = meta['cW'], meta['rH']
        if (len(region) != 4 or region[0] != 0 or region[1] != 0 or region[2] < 3
                or not 0 <= region[3] < 100000 or len(widths) <= 3 or len(heights) <= region[3]
                or not all(isinstance(h, (int, float)) and math.isfinite(h) and h > 0 for h in heights)
                or not all(isinstance(w, (int, float)) and math.isfinite(w) and w > 0 for w in widths)):
            raise ValueError()
        selected = [(index + 1, p['rg']) for index, p in enumerate(pages) if p['rg'][0] <= 3 <= p['rg'][2]]
        selected.sort(key=lambda item: item[1][1])
        next_row = 0
        for _, rg in selected:
            if len(rg) != 4 or rg[1] != next_row or rg[3] < rg[1] or rg[3] > region[3]:
                raise ValueError()
            next_row = rg[3] + 1
        if next_row != region[3] + 1:
            raise ValueError()
        return selected
    except (KeyError, TypeError, IndexError, ValueError):
        raise SkipListError('无法确认跳过清单 D 列的完整范围，未继续结算。') from None


def extract_records(meta, fragments):
    """按单元格坐标提取 D 列，不从整页文字中模糊搜索编号。"""
    cells = defaultdict(list)
    for _, rg, data in fragments:
        if not isinstance(data, dict) or not isinstance(data.get('es'), list):
            raise SkipListError('跳过清单分片缺少完整明细，未继续结算。')
        left = meta['lM'] + sum(meta['cW'][rg[0]:3])
        right = left + meta['cW'][3]
        for element in data.get('es', []):
            if not isinstance(element, dict) or element.get('es'):
                raise SkipListError('跳过清单出现无法确认的嵌套布局，未继续结算。')
            if 'tx' not in element:
                continue
            text, x, y = element['tx'], element.get('x'), element.get('y')
            if not isinstance(text, str) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                raise SkipListError('跳过清单文字位置异常。')
            if not left <= x < right:
                continue
            top, row = meta['tM'], rg[1]
            while row <= rg[3] and y >= top + meta['rH'][row]:
                top += meta['rH'][row]
                row += 1
            if row > rg[3] or y < top:
                raise SkipListError('跳过清单文字超出行范围。')
            cells[row].append((y, x, text))
    texts = {row: ''.join(piece[2] for piece in sorted(pieces)).strip() for row, pieces in cells.items()}
    if texts.pop(0, None) != '单据号':
        raise SkipListError('未确认 Sheet3 的 D 列表头为“单据号”，未继续结算。')
    records = []
    for row, text in sorted(texts.items()):
        if not text:
            continue
        number = text.upper()
        if BILL_PATTERN.fullmatch(number) is None:
            raise SkipListError('跳过清单 D{} 的编号格式无法确认，未继续结算。'.format(row + 1))
        records.append({'cell':'D{}'.format(row + 1), 'billNumber':number})
    return records


def extract_numbers(meta, fragments):
    return frozenset(r['billNumber'] for r in extract_records(meta, fragments))


class DingTalkSkipList:
    def __init__(self, context, settings, control, emit, on_page_created=None, result_store=None):
        self.context, self.settings, self.control, self.emit = context, settings, control, emit
        self.url = latest_preview_url(settings.skip_list_url)
        self.file_id = parse_qs(urlsplit(self.url).query).get('fileId', parse_qs(urlsplit(self.url).query).get('cloudSpaceDentryId'))[0]
        self.page = None
        self.on_page_created = on_page_created
        self.result_store = result_store
        self.last_snapshot = None

    def _session(self, frame_url):
        parsed = urlsplit(frame_url)
        query = parse_qs(parsed.query)
        if (parsed.scheme != 'https' or parsed.hostname != 'space.dingtalk.com'
                or parsed.path != '/preview/v1/' or query.get('objectId') != [self.file_id]):
            return None
        if not query.get('aliyunContent'):
            return None
        try:
            content = json.loads(query['aliyunContent'][0])
            source = parse_qs(urlsplit(content['src']).query)
            session = {key: source[key][0] for key in ('url', 'bucket', 'accessKeyId', 'accessKeySecret')}
            session['token'] = unquote(content['stsToken'])
            version = query.get('version', [''])[0]
            session['fileVersion'] = version if version.isdigit() else ''
            root = urlsplit(session['url'])
            if (root.scheme != 'https' or root.hostname != 'sh.preview.dingtalk.com' or root.query
                    or root.fragment or root.username or root.password or root.port
                    or not root.path.startswith('/dingTalkDocTransferPrivate/')
                    or session['bucket'] != 'dingtalk-cspase-sh'):
                raise ValueError()
            return session
        except (ValueError, KeyError, TypeError, IndexError):
            raise SkipListError('跳过清单预览会话格式异常，未继续结算。') from None

    def wait_for_login(self):
        """每次开始先等待人工登录，并确认指定工作表实际可以读取。"""
        self.refresh(None)
        self.control.checkpoint()
        self.emit('log', '文档登录及 Sheet3 读取已确认，接下来打开 NCC 登录页。')

    def _get(self, session, suffix, as_json=False):
        self.control.checkpoint()
        url = session['url'].rstrip('/') + suffix
        date_header = formatdate(usegmt=True)
        headers = {'x-oss-date': date_header, 'x-oss-security-token': session['token']}
        canonical = '\n'.join(['GET', '', '', date_header] +
            [key + ':' + headers[key] for key in sorted(headers)] +
            ['/' + session['bucket'] + urlsplit(url).path])
        signature = base64.b64encode(hmac.new(session['accessKeySecret'].encode(), canonical.encode(), hashlib.sha1).digest()).decode()
        headers['Authorization'] = 'OSS ' + session['accessKeyId'] + ':' + signature
        headers['Cache-Control'] = 'no-cache'
        try:
            response = self.context.request.get(url, headers=headers,
                timeout=int(float(self.settings.response_timeout_seconds) * 1000),
                max_redirects=0, ignore_https_errors=False)
            try:
                if response.status != 200 or response.url != url:
                    raise SkipListError('跳过清单接口返回 HTTP {}，未继续结算。'.format(response.status))
                body = response.body()
            finally:
                response.dispose()
            self.control.checkpoint()
            return json.loads(body.decode('utf-8')) if as_json else PreviewDecoder(body).decode()
        except SkipListError:
            raise
        except Exception:
            # Playwright 的原始异常会包含带认证信息的调用参数，不写入日志。
            self.control.checkpoint()
            raise SkipListError('跳过清单接口读取失败，未继续结算；请检查登录和网络。') from None

    def refresh(self, business_date):
        self.control.checkpoint()
        self.last_snapshot = None
        attempt = None
        if self.result_store is not None:
            attempt = self.result_store.begin(business_date, SHEET_NAME, self.file_id)
            self.emit('skip_result', attempt)
        try:
            snapshot = self._read_snapshot(business_date)
            if self.result_store is not None:
                snapshot = self.result_store.success(snapshot)
            self.last_snapshot = snapshot
            if self.result_store is not None:
                self.emit('skip_result', snapshot)
                self.emit('log', '清单核对文件：' + snapshot['csvPath'])
            return frozenset(r['billNumber'] for r in snapshot['records'])
        except BaseException:
            if self.result_store is not None and attempt is not None:
                stopped = self.control.stop_event.is_set()
                self.emit('skip_result', self.result_store.failure(attempt, stopped))
            raise

    def _read_snapshot(self, business_date):
        self.control.checkpoint()
        first = self.page is None
        try:
            if first:
                self.page = self.context.new_page()
                if self.on_page_created:
                    self.on_page_created(self.page)
                self.emit('status', '请在工具浏览器的跳过清单页登录钉钉；登录后自动继续。')
                self.page.goto(self.url, wait_until='domcontentloaded')
            else:
                if self.page.is_closed():
                    raise SkipListError('跳过清单标签页已关闭，请重新开始任务。')
                # 网站可能把当前版本写回地址栏；下个日期仍须从未固定版本的入口刷新。
                current = urlsplit(self.page.url)
                if 'version' in parse_qs(current.query) or current.hostname != 'alidocs.dingtalk.com':
                    self.page.goto(self.url, wait_until='domcontentloaded')
                else:
                    self.page.reload(wait_until='domcontentloaded')
        except SkipListError:
            raise
        except Exception:
            self.control.checkpoint()
            raise SkipListError('跳过清单网页刷新失败，未继续结算。') from None
        timeout = self.settings.login_timeout_seconds if first else self.settings.response_timeout_seconds
        deadline, session = time.monotonic() + float(timeout), None
        while session is None and time.monotonic() < deadline:
            self.control.checkpoint()
            for frame in self.page.frames:
                session = self._session(frame.url)
                if session:
                    break
            if session is None:
                self.control.wait(float(self.settings.condition_poll_seconds))
        if session is None:
            raise SkipListError('跳过清单尚未登录或预览未加载，已停止；未使用旧清单继续结算。')
        self.emit('status', '文档已登录，正在通过接口读取 Sheet3 单据号…')
        root = self._get(session, '/doc/meta.json', as_json=True)
        matches = [i + 1 for i, sheet in enumerate(root.get('s', [])) if sheet.get('n') == SHEET_NAME]
        if len(matches) != 1 or root.get('wt') != 1:
            raise SkipListError('未确认指定的 Sheet3 或其预览格式，未继续结算。')
        prefix = '/doc/s{}'.format(matches[0])
        meta = self._get(session, prefix + '/meta.bin')
        fragments = [(index, rg, self._get(session, prefix + '/fp{}.bin'.format(index)))
                     for index, rg in column_pages(meta)]
        records = extract_records(meta, fragments)
        count = len(set(r['billNumber'] for r in records))
        label = business_date.isoformat() if business_date is not None else '启动检查'
        self.emit('log', '{}：跳过清单已刷新，Sheet3 共 {} 个单据号。'.format(label, count))
        return dict(status='success', readAt=timestamp(),
            businessDate=business_date.isoformat() if business_date is not None else None,
            sheet=SHEET_NAME, fileId=self.file_id, fileVersion=session.get('fileVersion',''),
            range='D1:D{}'.format(meta['rg'][3] + 1),
            recordCount=len(records), uniqueCount=count, records=records)
