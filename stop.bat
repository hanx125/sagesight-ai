@echo off
cd /d %~dp0

:: 设置控制台标题
title Stop YOLO Detection Channel

:: 打印停止信息
echo ========================================
echo   Stop YOLO Detection Channel
echo ========================================
echo.
echo   Stopping processes...
echo.

:: 停止 Python 进程
taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq YOLO Detection*" 2>nul
if %ERRORLEVEL% EQU 0 (
    echo   [OK] Detection processes stopped
) else (
    echo   [WARN] No running processes found
)

:: 清理共享内存
powershell -Command "try { $shm = [System.IO.MemoryMappedFiles.MemoryMappedFile]::OpenExisting('yolo_detection_channel'); $shm.Dispose(); echo '   [OK] Shared memory cleaned' } catch { echo '   [INFO] No shared memory to clean' }" 2>nul

echo.
echo ========================================
echo   System Stopped
echo ========================================
echo.
echo   To restart: .\start.bat
echo.
timeout /t 2 /nobreak >nul
pause
