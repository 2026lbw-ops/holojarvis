@echo off
REM 启动贾维斯语音助手 (Windows)
REM 用法：run.bat            带桌宠 HUD
REM       run.bat --no-pet   纯命令行
REM       run.bat --text     纯文字交互
REM       run.bat --check    只做启动自检
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u -m jarvis %*
