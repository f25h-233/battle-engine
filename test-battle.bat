@echo off
chcp 65001 >nul
cd /d "%~dp0"
python test_battle.py
if errorlevel 1 (
  echo.
  echo 启动失败——请查看上方错误信息。
  pause
)
