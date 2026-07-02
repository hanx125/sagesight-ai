@echo off
cd /d %~dp0

:: 设置控制台标题
title YOLO Detection Channel

:: 打印启动信息
echo ========================================
echo   YOLO Detection Channel - Start
echo ========================================
echo.
echo   Start time: %date% %time%
echo   Working directory: %CD%
echo.
echo   Press Ctrl+C to stop
echo ========================================
echo.

:: 启动检测程序
python main_detector.py

:: 程序退出后的信息
echo.
echo ========================================
echo   Program Exited
echo ========================================
echo.
pause
