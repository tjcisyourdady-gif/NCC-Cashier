"""NCC 独立仓库及签名发布身份。旧仓库仅保留一次性迁移入口。"""
import json
import sys
from pathlib import Path
APP_ID = 'ncc-cashier'
APP_VERSION = '1.9.2'
DISPLAY_VERSION = 'V1.9.2（独立仓库版）'
RELEASE_NOTES = 'NCC 已迁至独立仓库 NCC-Cashier。安装重启后，后续更新只从 NCC 专用仓库检查和下载，仍优先使用国内直连通道。本次保持结算、日期循环和清单读取逻辑不变，保留已有设置、登录数据和操作记录。'
EXE_NAME = 'NCC出纳结算工具.exe'
GITHUB_OWNER = 'tjcisyourdady-gif'
GITHUB_REPO = 'NCC-Cashier'
GITHUB_BRANCH = 'main'
MANIFEST_PATH = 'latest.json'
LEGACY_GITHUB_REPO = 'YuiDesk'
LEGACY_MANIFEST_PATH = 'updates/ncc-cashier/latest.json'
TAG_PREFIX = 'ncc-cashier-v'
DOWNLOAD_MIRRORS = ('https://ghfast.top/', 'https://gh-proxy.com/')
_key_path = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'update_public_key.json'
_key = json.loads(_key_path.read_text(encoding='utf-8-sig'))
SIGNING_KEY_ID = _key['key_id']
SIGNING_MODULUS = _key['modulus']
