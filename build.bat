@echo off
echo ========================================
echo   NetAuto Build - Package to single exe
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.7+ first.
    pause
    exit /b 1
)

echo [1/3] Installing build dependencies...
pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [FAIL] pip install failed
    pause
    exit /b 1
)

echo.
echo [2/3] Packaging with PyInstaller...
pyinstaller --onefile --name NetAuto --clean --noconfirm NetAuto.py
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed
    pause
    exit /b 1
)

echo.
echo [3/3] Preparing release folder...
if exist release rmdir /s /q release
mkdir release
copy /y dist\NetAuto.exe release\ >nul
copy /y devices.example.json release\ >nul
copy /y README.md release\ >nul

echo.
echo ========================================
echo   [OK] Build complete!
echo ========================================
echo   Output: release\
echo     - NetAuto.exe
echo     - devices.example.json
echo     - README.md
echo.
echo   Zip the release folder and share it:
echo   1. Unzip
echo   2. Copy devices.example.json to devices.json, fill in device info
echo   3. Double-click NetAuto.exe
echo ========================================
pause