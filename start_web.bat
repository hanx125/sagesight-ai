@echo off
chcp 65001 >nul
echo ============================================================
echo SageSight AI 检测系统 - Web管理平台
echo ============================================================
echo.
echo 正在启动Web服务...
echo.
echo 访问地址:
echo   - 主页:       http://localhost:5000
echo   - 任务管理:   http://localhost:5000/tasks
echo   - 事件列表:   http://localhost:5000/events
echo   - 监控墙:     http://localhost:5000/monitor
echo.
echo 按 Ctrl+C 停止服务
echo ============================================================
echo.

cd /d "%~dp0"

REM 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 启动Web应用
python web_app.py

pause
