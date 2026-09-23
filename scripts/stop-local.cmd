@echo off
rem 停止全部本地桥接实例。
cd /d "%~dp0.."
"C:\Program Files\Git\bin\bash.exe" -lc "bash scripts/stop-local.sh"
