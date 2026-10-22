@echo off
chcp 65001 > nul
title ValoTracker Discord Bot
echo ========================================================
echo        Starting ValoTracker Discord Bot
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python not found in PATH!
        echo Please install Python from https://www.python.org/
        echo Make sure to check "Add Python to PATH" during installation.
        pause
        exit /b 1
    ) else (
        py bot.py
    )
) else (
    python bot.py
)

if %errorlevel% neq 0 (
    echo.
    echo Bot stopped with error code %errorlevel%.
    pause
)
