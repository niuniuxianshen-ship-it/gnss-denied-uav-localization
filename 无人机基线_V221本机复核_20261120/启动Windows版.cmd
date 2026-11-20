@echo off
setlocal
py -3 -B "%~dp0desktop.py" %*
if errorlevel 1 pause
