@echo off
setlocal
py -3 -B "%~dp0web.py" --open %*
if errorlevel 1 pause
