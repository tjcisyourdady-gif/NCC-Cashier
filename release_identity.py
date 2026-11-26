"""NCC 独立仓库及签名发布身份。旧仓库仅保留一次性迁移入口。"""
import json
import sys
from pathlib import Path
APP_ID = 'ncc-cashier'
APP_VERSION = '1.9.4'
DISPLAY_VERSION = 'V1.9.4（更新重启修复版）'
RELEASE_NOTES = '修复点击“安装并重启”后没有反应。确认安装助手启动成功后才退出旧窗口，启动失败会保留程序并显示日志；修复重启时的环境和窗口显示。旧版安装器本身有此故障，本次须通过修复入口或手动完整包升级一次，之后使用程序内更新。结算、日期循环及文档读取逻辑不变，保留设置和操作记录。'
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
