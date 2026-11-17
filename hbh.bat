@echo off
rem hbh.bat - happybrowser CLI launcher in headless mode (no window)
rem Equivalent to: hb --headless ...
rem NOTE: keep this file ASCII-only. cmd.exe parses batch files in the
rem system codepage (GBK on this machine); UTF-8 Chinese comments here
rem corrupt the script (stray ampersands split rem lines into garbage).
setlocal
chcp 65001 >nul
set "HB_ROOT=%~dp0"
if exist "%HB_ROOT%.venv\Scripts\hb.exe" (
    "%HB_ROOT%.venv\Scripts\hb.exe" --headless %*
) else (
    uv run --project "%HB_ROOT%." hb --headless %*
)
set "HB_RC=%errorlevel%"
endlocal & exit /b %HB_RC%
