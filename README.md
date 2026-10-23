# NetAuto - 网络设备管理系统 v2.0

跨平台网络设备管理工具，支持Windows和Linux，支持多种厂商设备。

## 功能特性

- 🔍 **自动检测设备类型** - 自动识别华为、华三、锐捷、思科、Juniper等
- 📱 **交互式SSH终端** - 直接在终端输入命令操作设备
- 📋 **批量管理** - 批量连接、备份多台设备
- 💾 **配置备份** - 自动备份设备配置
- 📊 **设备状态监测** - 查看CPU、内存、风扇、温度等状态
- 🖥️ **跨平台支持** - 完美支持Windows和Linux

## 支持的设备

| 厂商 | device_type |
|------|-------------|
| 华为 | huawei |
| 华三 | hp_comware |
| 锐捷 | ruijie |
| 思科 IOS | cisco_ios |
| 思科 IOS-XE | cisco_xe |
| 思科 IOS-XR | cisco_xr |
| 思科 NX-OS | cisco_nxos |
| Juniper | juniper_junos |
| Arista | arista_eos |

## 快速开始

### Windows

双击 `start.bat` 即可启动，或手动运行：

```bash
python NetAuto.py
```

### Linux

```bash
chmod +x start.sh
./start.sh
```

或手动运行：

```bash
python3 NetAuto.py
```

## 依赖安装

```bash
# 国内用户推荐使用清华源
pip install netmiko -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 菜单说明

主菜单按功能分组，选择板块后才展开对应子项（输入 0 可逐级返回）：

```
1. 设备管理      添加 / 批量导入 / 删除设备
   1. 添加单台设备 (自动检测类型)
   2. 批量导入设备 (BatchDevice.csv/xlsx)
   3. 删除设备

2. 连接管理      连接 / 断开 / 已连接列表
   1. 连接单台设备
   2. 批量连接所有设备
   3. 查看已连接设备列表
   4. 断开指定设备
   5. 断开所有设备

3. 设备运维      SSH终端 / 配置下发 / 备份 / 信息查询
   1. 交互式SSH终端
   2. 执行配置命令
   3. 备份当前设备配置
   4. 批量备份所有设备
   5. 查看设备接口信息
   6. 查看设备基本信息
   7. 查看设备运行状态

4. 自动化巡检    周期性采集状态并推送报告
```

## 目录结构

```
.
├── NetAuto.py          # 主程序文件
├── devices.json        # 设备配置文件 (自动生成)
├── backup/             # 配置备份目录 (自动创建)
├── start.bat           # Windows启动脚本
├── start.sh            # Linux启动脚本
└── README.md           # 本文件
```

## 设备配置示例 (devices.json)

```json
{
    "Router-01": {
        "device_type": "huawei",
        "host": "192.168.1.1",
        "username": "admin",
        "password": "your_password",
        "port": 22,
        "encoding": "gbk"
    },
    "Cisco-Switch": {
        "device_type": "cisco_ios",
        "host": "192.168.1.2",
        "username": "admin",
        "password": "your_password",
        "port": 22,
        "secret": "enable_password",
        "encoding": "utf-8"
    }
}
```

## 批量导入设备（菜单 15）

在程序同目录放置 `BatchDevice.csv` 或 `BatchDevice.xlsx`，选择菜单 15 即可批量导入并可选自动连接。模板参考 `BatchDevice.example.csv`。

表头字段（`*` 为必填）：

| 字段 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| name | * | 设备名称 | - |
| host | * | IP 地址 | - |
| username | * | 登录用户名 | - |
| password | * | 登录密码 | - |
| device_type | | 设备类型（见上表） | huawei |
| port | | 端口 | SSH=22 / Telnet=23 |
| secret | | 特权密码（思科等） | 空 |
| encoding | | 编码 | 按厂商自动 |
| protocol | | ssh / telnet | ssh |
| legacy_ssh | | 老设备兼容模式 true/false | false |

注意：旧版 `.xls` 请先在 Excel 中另存为 `.xlsx` 或 `.csv`。

## 自动化巡检（菜单 16）

对当前已连接的全部或指定设备进行周期性巡检，后台线程运行，不影响菜单操作。

**巡检周期**：5分钟 / 30分钟 / 1小时 / 4小时 / 6小时 / 12小时 / 24小时

**采集内容**：版本信息、CPU占用、内存占用、温度状态、接口状态（自动按设备类型匹配厂商命令）

**输出方式**（可多选，配置自动保存复用）：
- 本地文件：`reports/巡检报告_YYYYMMDD_HHMMSS.txt`
- 邮件：SMTP 发送（支持 465/587/25 端口，QQ邮箱等需填授权码）
- 日志服务器：syslog UDP 发送（默认 514 端口）

**容错机制**：巡检时设备掉线会自动尝试重连，失败则记录原因并跳过该设备。

## 报错自动诊断

连接失败时程序会自动分析错误类型并给出解决办法，覆盖：

| 错误特征 | 诊断 | 建议 |
|---------|------|------|
| 算法协商失败 | 老设备（VRP5）不支持新算法 | 配置 `"legacy_ssh": true` |
| 认证失败 | 用户名/密码错误或无SSH权限 | 核对凭据、检查 service-type ssh |
| 连接超时 | 网络层不可达 | ping 测试、检查防火墙/ACL |
| 连接被拒绝 | 端口未开放 | 启用 stelnet server、核对端口/协议 |

## 老设备兼容（华为 VRP5 等）

部分老设备（如华为 VRP5 V200R003C00）只支持 `ssh-rsa`、`diffie-hellman-group1-sha1` 等旧版 SSH 算法，新版 SSH 客户端默认禁用这些算法，会在**协商阶段卡住或直接失败**。

本工具已内置自动兼容：检测到协商失败时会自动降级到 legacy 算法重试。

如需跳过探测直接走兼容模式，在设备配置中添加 `"legacy_ssh": true`：

```json
{
    "Old-Huawei": {
        "device_type": "huawei",
        "host": "192.168.1.254",
        "username": "admin",
        "password": "your_password",
        "port": 22,
        "legacy_ssh": true,
        "encoding": "gbk"
    }
}
```

## Telnet 支持

对于仅支持 Telnet 的设备，将端口改为 23 并添加 `"protocol": "telnet"`：

```json
{
    "Old-Switch": {
        "device_type": "huawei",
        "host": "192.168.1.3",
        "username": "admin",
        "password": "your_password",
        "port": 23,
        "protocol": "telnet",
        "encoding": "gbk"
    }
}
```

使用菜单 1 自动探测时，端口填 23 会自动按 Telnet 设备类型探测。

## 打包分发（免 Python 环境）

如果要把工具发给没有 Python 环境的同事使用：

```bash
# 在开发机上双击运行（或在终端执行）
build.bat
```

打包完成后，将 `release\` 目录压缩发出即可。对方电脑**无需安装 Python**，解压后：

1. 复制 `devices.example.json` 改名为 `devices.json` 并填入设备信息
2. 双击 `NetAuto.exe` 运行


## 跨平台兼容性

### 已实现的跨平台功能

✅ Windows控制台UTF-8编码支持
✅ Linux/macOS支持
✅ 跨平台路径处理 (os.path.join)
✅ 跨平台目录创建
✅ 跨平台系统判断

### 平台特性

**Windows:**
- 自动设置代码页 65001 (UTF-8)
- 支持GBK编码设备
- 使用 cls 清屏

**Linux/macOS:**
- 使用 clear 清屏
- 原生UTF-8支持
- 使用python3运行

## 常见问题

### Windows编码问题

如果看到乱码，请确保：
1. 使用 start.bat 启动
2. 或手动执行 `chcp 65001`
3. 检查Windows终端的编码设置

### Linux权限问题

```bash
chmod +x start.sh
```

### 设备连接超时

- 检查IP地址是否可达
- 检查SSH端口是否开放
- 检查防火墙设置
- 检查用户名密码是否正确

### 虚拟环境报错 "did not find executable"

Python 本体被移动或重装后，旧 `.venv` 会失效（venv 只是指向本机 Python 的引用）。重建即可：

```bash
# Windows
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

已在 Python 3.14 + netmiko 4.8 环境验证通过。

## 开发计划

- [ ] Web界面
- [ ] 定时任务
- [ ] 配置对比
- [ ] 告警通知

## License

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！