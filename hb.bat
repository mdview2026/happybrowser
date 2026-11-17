@echo off
rem hb.bat - happybrowser CLI launcher (human / AI friendly)
rem NOTE: keep this file ASCII-only. cmd.exe parses batch files in the
rem system codepage (GBK on this machine); UTF-8 Chinese comments here
rem corrupt the script (stray ampersands split rem lines into garbage).
rem Usage: hb open https://example.com / hb find "#kw" / hb click "@1"
setlocal
chcp 65001 >nul
set "HB_ROOT=%~dp0"
if exist "%HB_ROOT%.venv\Scripts\hb.exe" (
    "%HB_ROOT%.venv\Scripts\hb.exe" %*
) else (
    uv run --project "%HB_ROOT%." hb %*
)
endlocal
