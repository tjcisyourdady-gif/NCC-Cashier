"""财务提交使用的进程互斥与成功提示判定。兼容 Python 3.8。"""

import hashlib
import os
from pathlib import Path
import re
import unicodedata


class AlreadyRunningError(RuntimeError):
    """同一结算记录目录已有程序持有运行锁。"""


class SingleInstanceLock:
    """按日志绝对路径互斥；锁文件必须保留，不能在解锁时删除。"""

    def __init__(self, path):
        self.journal_path = Path(path).expanduser().resolve()
        identity = os.path.normcase(str(self.journal_path))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        self.lock_path = self.journal_path.parent / (".ncc_settlement_" + digest + ".lock")
        self._file = None

    @property
    def held(self):
        return self._file is not None

    def acquire(self):
        if self._file is not None:
            return self
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+b")
        except OSError as exc:
            raise RuntimeError("无法创建结算运行锁：%s；%s" % (self.lock_path, exc)) from exc
        try:
            # Windows 字节范围锁需要一个实际字节。不能截断或替换锁文件。
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise AlreadyRunningError(
                "无法取得结算运行锁，可能已有程序正在使用同一操作记录。"
                "请关闭另一个结算程序后重试：%s" % self.journal_path
            ) from exc
        except BaseException:
            handle.close()
            raise
        self._file = handle
        return self

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # close 同样会释放本进程的锁；清理不能掩盖原业务异常。
            pass
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()


_FAILURE = re.compile(
    r"失败|错误|异常|部分|未成功|无成功|没有成功|未能成功|不成功|尚未成功|"
    r"未结算成功|不能成功|无一成功|不允许|不足"
)
_ZERO = r"(?<![\d.])(?:0+(?:\.0+)?|零|〇)(?![\d.])"
_SEPARATOR = r"[\s:：=,，;；、()（）\[\]【】]*"
_COUNT_LABEL = r"(?:数量|条数|笔数|张数|单数|总数|个数|项数|数|率)"
_ZERO_AFTER_SUCCESS = re.compile(
    r"成功" + _SEPARATOR + r"(?:结算" + _SEPARATOR + r")?"
    r"(?:" + _COUNT_LABEL + _SEPARATOR + r")?"
    r"(?:(?:为|是|共|计)" + _SEPARATOR + r")?" + _ZERO
)
_ZERO_BEFORE_SUCCESS = re.compile(
    _ZERO + _SEPARATOR + r"(?:[条笔张项个单]" + _SEPARATOR + r")?"
    r"(?:(?:记录|单据)" + _SEPARATOR + r")?"
    r"(?:结算" + _SEPARATOR + r")?成功"
)


def is_settlement_success(text):
    """只接受明确的结算成功文案；零成功和混合结果均返回 False。

    此函数仅判定提示文字，调用方仍应核验目标单据的实际状态。
    """
    if not isinstance(text, str):
        return False
    value = unicodedata.normalize("NFKC", text).strip()
    if "结算" not in value or "成功" not in value:
        return False
    if _FAILURE.search(value):
        return False
    if _ZERO_AFTER_SUCCESS.search(value) or _ZERO_BEFORE_SUCCESS.search(value):
        return False
    return True
