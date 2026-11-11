@echo off
setlocal
title Guide Document Creator - Server Controller
cd /d "%~dp0"
python sync_server.py
if errorlevel 1 (
    echo.
    echo [ERROR] Co loi xay ra khi chay trinh dieu khien Python.
    pause
)
