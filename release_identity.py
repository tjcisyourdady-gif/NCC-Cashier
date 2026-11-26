"""NCC 独立仓库及签名发布身份。旧仓库仅保留一次性迁移入口。"""
import json
import sys
from pathlib import Path
APP_ID = 'ncc-cashier'
APP_VERSION = '1.9.5'
DISPLAY_VERSION = 'V1.9.5（小体积更新测试）'
RELEASE_NOTES = '小体积更新测试：更新包只含程序、源码和说明，复用已有浏览器，请先使用 V1.9.4 完整修复版。安装重启后标题显示 V1.9.5（小体积更新测试）。保留 V1.9.4 的更新重启修复，结算、日期循环和文档读取逻辑不变，现有设置及操作记录会保留。'
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
