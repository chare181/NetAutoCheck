@echo off
chcp 65001 >nul
setlocal
echo ========================================
echo   NetAuto - Network Device Manager
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.7+ first.
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python detected

python -c "import netmiko, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        echo         Run manually: pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo [OK] Dependencies ready
echo.

python NetAuto.py
pause
