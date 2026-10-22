@echo off
chcp 65001 > nul
title Install ValoTracker Dependencies
echo ========================================================
echo       Installing ValoTracker Requirements
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found in PATH!
        echo Please install Python from https://www.python.org/
        pause
        exit /b 1
    ) else (
        py -m pip install -r requirements.txt
    )
) else (
    python -m pip install -r requirements.txt
)

echo.
echo ========================================================
echo   Dependencies installed successfully!
echo ========================================================
pause
