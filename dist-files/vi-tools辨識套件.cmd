@echo off
chcp 65001 >nul
cd /d "%~dp0"
title vi-tools 辨識套件
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0app\main.py" %*
if errorlevel 1 pause
