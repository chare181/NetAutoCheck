@echo off
chcp 65001 >nul
echo ========================================
echo   NetAuto - 网络设备管理系统 (Windows)
echo ========================================
echo.
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，请先安装Python 3.7+
    echo    下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo ✅ Python环境检测通过
echo.
python NetAuto.py
pause