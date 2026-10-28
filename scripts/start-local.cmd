@echo off
rem Recruiter-Na local bridge launcher (detached-friendly).
cd /d "%~dp0.."
if not exist "workspace" mkdir "workspace"
"C:\Program Files\Git\bin\bash.exe" -lc "bash scripts/start-local.sh" >> "workspace\bridge.log" 2>&1
