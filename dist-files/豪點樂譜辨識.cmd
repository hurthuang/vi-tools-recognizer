@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 豪點樂譜辨識
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0app\main.py" %*
if errorlevel 1 pause
