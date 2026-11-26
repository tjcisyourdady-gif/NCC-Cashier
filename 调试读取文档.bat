@echo off
setlocal
chcp 65001 >nul
if exist "%~dp0NCC出纳结算工具.exe" (
    start "" "%~dp0NCC出纳结算工具.exe" --debug-read
    exit /b 0
)
set "NCC_DEBUG_PYTHON=%~dp0..\win7_build_env\python38\python.exe"
set "PYTHONUTF8=1"
if exist "%NCC_DEBUG_PYTHON%" (
    "%NCC_DEBUG_PYTHON%" "%~dp0skip_list_debug.py"
) else (
    python "%~dp0skip_list_debug.py"
)
echo.
echo 本入口仅读取文档，不进入 NCC。结果在本目录 logs\清单读取结果。
pause
