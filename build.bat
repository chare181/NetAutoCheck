@echo off
chcp 65001 >nul
echo ========================================
echo   NetAuto 打包工具 - 生成免Python环境的exe
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，打包需要本机先安装Python 3.7+
    pause
    exit /b 1
)

echo [1/3] 安装打包依赖...
pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo ❌ 依赖安装失败
    pause
    exit /b 1
)

echo.
echo [2/3] 使用 PyInstaller 打包单文件 exe...
pyinstaller --onefile --name NetAuto --clean --noconfirm NetAuto.py
if errorlevel 1 (
    echo ❌ 打包失败
    pause
    exit /b 1
)

echo.
echo [3/3] 整理发布目录 release\ ...
if exist release rmdir /s /q release
mkdir release
copy /y dist\NetAuto.exe release\ >nul
copy /y devices.example.json release\ >nul
copy /y README.md release\ >nul

echo.
echo ========================================
echo   ✅ 打包完成！
echo ========================================
echo   发布文件位于: release\
echo     - NetAuto.exe            主程序（双击即用）
echo     - devices.example.json   设备配置模板
echo     - README.md              使用说明
echo.
echo   将 release\ 压缩发给别人即可，对方：
echo   1. 解压
echo   2. 复制 devices.example.json 改名为 devices.json 并填入设备信息
echo   3. 双击 NetAuto.exe
echo ========================================
pause
