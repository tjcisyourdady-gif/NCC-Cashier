# NCC 出纳结算工具

NCC 结算（出纳）自动处理工具，提供日期循环、取消结算清单读取、后台执行和自动更新。

本项目的源码、发行包和更新清单均维护在独立仓库 [NCC-Cashier](https://github.com/tjcisyourdady-gif/NCC-Cashier)。

V1.9.4 修复“安装并重启”没有反应：确认安装助手启动后才关闭旧窗口，失败时保留窗口和错误日志，并修复重新启动环境及窗口显示。已用窗口版 onefile EXE 验证退出、替换自身、重新解包和自动启动的完整链路。

V1.9.2/V1.9.3 的安装启动器本身存在故障，本次请使用本地修复入口，或将完整包解压到新目录，复制旧版的“出纳结算设置.json”和“logs”后运行新版；之后即可继续使用程序内更新。不要用公开包的默认设置覆盖自己的设置文件。

## 使用

从 [Releases](https://github.com/tjcisyourdady-gif/NCC-Cashier/releases) 下载完整包，全部解压后运行 `NCC出纳结算工具.exe`。完整包包含运行环境、浏览器和源码。

首次使用请填写自己的钉钉清单网址并保存，然后使用“调试：仅读清单”核对读取结果。正式运行先等待手动登录文档，再等待登录 NCC。具体规则见包内“使用说明.txt”。

## 自动更新

每次启动在后台检查独立仓库根目录的 `latest.json`，优先使用国内直连镜像。清单须通过签名验证，完整包须通过 SHA-256 校验。停止任务后可在“自动更新…”中安装并重启，现有设置、登录数据、日志和操作记录会保留。

V1.9 和 V1.9.1 会从原地址收到一次 V1.9.2 迁移更新。安装后只从 NCC-Cashier 检查后续更新；原地址保留迁移入口，避免已安装的程序失去升级渠道。

## 源码运行与打包

开发环境使用 Python 3.8，并安装 `requirements.txt`。图形界面入口为 `settlement_gui.py`，只读调试入口为 `skip_list_debug.py`。打包还需安装 `requirements-build.txt`，并通过 `build_package.ps1` 指定现有完整 Chromium 目录。

```powershell
python -m pip install -r requirements.txt
python settlement_gui.py
python run_offline_tests.py test_app_updater
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_package.ps1 -PythonPath C:\Python38\python.exe -ChromiumSource C:\Browsers\chromium-1161 -Version V1.9.4 -PublicRelease
```

源码和公开包不包含私人文档默认网址、密码、认证信息、日志或结算结果。

## 发布维护

版本和仓库信息在 `release_identity.py`。构建完整包后，使用 `prepare_update_release.py` 生成已签名清单，再使用 `publish_ncc_release.py <发行目录> --publish` 发布到独立仓库。签名私钥由发布者的 Windows 账号加密，仅保留在本机 `.release-secrets` 中，不加入 Git 和发行包。

`--legacy-bridge` 只用于生成旧版程序的一次性迁移发行；后续常规版本使用独立仓库默认发布流程。
