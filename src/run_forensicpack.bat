@echo off
title ForensicPack - Digital Forensics Auto-Archiver
echo.
echo  ====================================================
echo    ForensicPack  -  DFIR Auto-Archiver
echo  ====================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found in PATH.
    echo  Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

python "%~dp0forensicpack.py" gui

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] ForensicPack exited with an error.
    pause
)
