#!/bin/bash

echo "========================================"
echo "  NetAuto - 网络设备管理系统 (Linux)"
echo "========================================"
echo ""

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到Python，请先安装Python 3.7+"
    echo "   Ubuntu/Debian: sudo apt install python3 python3-pip"
    echo "   CentOS/RHEL: sudo yum install python3 python3-pip"
    exit 1
fi

echo "✅ Python3环境检测通过"
echo ""

# 检查pip
if ! command -v pip3 &> /dev/null; then
    echo "⚠️  pip3未找到，尝试使用pip"
    PIP_CMD=pip
else
    PIP_CMD=pip3
fi

# 检查并安装依赖（优先使用 requirements.txt，保证 netmiko / openpyxl 版本一致）
echo "检查依赖..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REQ_FILE="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    $PIP_CMD install -r "$REQ_FILE" -i https://pypi.tuna.tsinghua.edu.cn/simple
else
    REQUIREMENTS=("netmiko" "paramiko" "openpyxl")
    for pkg in "${REQUIREMENTS[@]}"; do
        $PIP_CMD show "$pkg" > /dev/null 2>&1
        if [ $? -ne 0 ]; then
            echo "正在安装 $pkg..."
            $PIP_CMD install "$pkg" -i https://pypi.tuna.tsinghua.edu.cn/simple
        fi
    done
fi

echo ""
echo "启动NetAuto..."
python3 "$SCRIPT_DIR/NetAuto.py"