"""NCC 独立仓库及签名发布身份。旧仓库仅保留一次性迁移入口。"""
import json
import sys
from pathlib import Path
APP_ID = 'ncc-cashier'
APP_VERSION = '1.9.6'
DISPLAY_VERSION = 'V1.9.6（启动更新提示）'
RELEASE_NOTES = '新增启动更新提示：每次打开程序自动后台检查，发现新版本后显示版本及更新说明，可选“更新”或“忽略”。选择更新会自动下载、校验、安装并重启；忽略或关闭提示仅对本次启动生效。无更新或检查失败不弹窗；最小化、后台或任务运行时延后提示，停止任务后才允许安装。继续使用不含浏览器的轻量更新包，复用 V1.9.4 完整版的现有浏览器，保留已有设置、登录数据及操作记录。'
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
