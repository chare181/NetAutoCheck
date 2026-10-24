# NetAuto - 网络设备管理系统 v2.0

跨平台网络设备管理工具，支持Windows和Linux，支持多种厂商设备。

## 功能特性

- 🔍 **自动检测设备类型** - 自动识别华为、华三、锐捷、思科、Juniper等
- 📱 **交互式SSH终端** - 直接在终端输入命令操作设备
- 🔐 **SSH / Telnet 双协议** - 支持自定义端口，自动兼容华为VRP5等老设备的旧版算法
- 📋 **批量管理** - 批量导入（CSV/Excel）、批量连接、批量备份
- 💾 **配置备份** - 自动备份设备配置
- 📊 **设备状态监测** - 查看CPU、内存、风扇、温度等状态
- ⏱️ **自动化巡检** - 5分钟~24小时周期采集，Excel 报表推送至本地 / 邮箱 / 日志服务器
- 🚨 **异常告警** - 设备不可达、接口Down、CPU/内存/温度超阈值、配置变更，即时邮件通知
- 🩺 **报错自动诊断** - 连接失败自动分析原因并给出解决办法
- 🖥️ **跨平台支持** - 完美支持Windows和Linux
- 📦 **免环境分发** - 一键打包为单个 exe，对方无需安装 Python

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
# 推荐：一次安装全部依赖（netmiko 负责设备连接，openpyxl 负责 Excel 报表/批量导入）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 或单独安装
pip install netmiko openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple
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

4. 自动化巡检    周期性采集状态、异常告警与报表推送
   1. 开始巡检（选择设备与周期）
   2. 停止巡检
   3. 查看巡检状态
   4. 配置指定设备 / 指定邮箱 / 告警阈值
```

## 目录结构

```
.
├── NetAuto.py                  # 主程序文件
├── devices.json                # 设备配置文件 (自动生成，已 gitignore)
├── devices.example.json        # 设备配置模板
├── BatchDevice.example.csv     # 批量导入模板
├── inspect_settings.json       # 巡检与告警配置 (自动生成，已 gitignore)
├── alert_state.json            # 告警状态持久化 (自动生成，已 gitignore)
├── backup/                     # 配置备份目录 (自动创建)
├── reports/                    # 巡检 xlsx 报表目录 (自动创建)
├── requirements.txt            # Python 依赖
├── build.bat                   # Windows 一键打包脚本
├── start.bat                   # Windows 启动脚本
├── start.sh                    # Linux 启动脚本
└── README.md                   # 本文件
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

## 批量导入设备（设备管理 → 2）

在程序同目录放置 `BatchDevice.csv` 或 `BatchDevice.xlsx`，选择「设备管理 → 2」即可批量导入并可选自动连接。模板参考 `BatchDevice.example.csv`。

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

## 自动化巡检（主菜单 4）

对当前已连接的全部或指定设备进行周期性巡检，后台线程运行，不影响菜单操作。

**巡检周期**：5分钟 / 30分钟 / 1小时 / 4小时 / 6小时 / 12小时 / 24小时

**采集内容**：版本信息、CPU占用、内存占用、温度状态、接口状态（自动按设备类型匹配厂商命令）

**输出方式**（可多选，配置自动保存复用）：
- 本地报表：`reports/inspection_report_YYYYMMDD_HHMMSS.xlsx`
- 邮件：发送 Excel 报表**附件**（含「巡检汇总」「告警记录」两个工作表），正文仅保留摘要
- 日志服务器：syslog UDP 发送异常摘要（默认 514 端口）

**报表内容（仅关键信息）**：设备名称、管理IP、设备类型、在线状态、CPU%、内存%、温度、接口总数/Up/Down、Down接口明细、采集时间。

## 异常告警

巡检每轮都会比对上一轮状态，发现异常时**立即向管理员发送告警邮件**（与巡检报表邮件分离）。

**已实现的告警**：

| 告警 | 级别 | 判定方式 |
|------|------|----------|
| 设备不可达 | CRITICAL | 连接失败 |
| 温度超阈值 | CRITICAL | 超过阈值（默认 70℃） |
| CPU 超阈值 | MAJOR | 连续 N 轮超过阈值（默认 80%） |
| 内存超阈值 | MAJOR | 连续 N 轮超过阈值（默认 85%） |
| 接口 Down | MAJOR / MINOR | 与上一轮对比新增 Down |
| 配置变更 | INFO | 运行配置指纹变化 |

**防误报机制**：

1. **边沿触发**：只在「正常→异常」和「异常→恢复」时发送，持续异常不重复轰炸
2. **连续确认**：CPU/内存需连续 N 轮（默认 2 轮）超阈值才告警，过滤瞬时尖峰
3. **抑制窗口**：同一告警默认 120 分钟内不重复发送，专治接口抖动
4. **忽略规则**：`administratively down` 接口不告警；可按设备配置 `ignore_interfaces`
5. **状态持久化**：告警状态存 `alert_state.json`，程序重启后不会重复报旧告警

**告警配置**（主菜单 4 → 4 → 3）：可设置 CPU/内存/温度阈值、连续确认轮数、抑制窗口、告警收件人，以及接口 Down / 配置变更告警开关。

告警邮件收件人优先取 `alerts.alert_to`，未配置时回退到巡检邮箱 `email.to`。

**固定巡检对象与收件人**（主菜单 4 → 4 配置）：

- **指定设备**：从全部已配置设备中勾选（可按 `a` 全选、`c` 仅已连接、或输编号如 `1,3`），保存后每次启动巡检自动沿用，无需重复选择；可随时取消指定恢复为启动时选择。未连接的设备在巡检时会自动尝试重连。
- **指定邮箱**：保存收件人列表（可多个，逗号分隔）。已配置过 SMTP 账号时可直接沿用服务器与账号，仅修改收件人；也可在 `配置巡检参数 → 查看当前配置` 中确认全部当前设置。

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

使用「设备管理 → 1」自动探测时，端口填 23 会自动按 Telnet 设备类型探测。

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
- [ ] 配置对比
- [ ] 疑似环路检测（多指标组合）
- [ ] SNMP Trap 被动接收（秒级告警）
- [ ] 告警升级与值班排班

## License

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！