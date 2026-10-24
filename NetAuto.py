from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
import paramiko
from datetime import datetime
import time
import socket
import os
import json
import sys
import io
import platform
import threading
import smtplib
import re
import hashlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.header import Header

# 跨平台配置
IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'

# 设置控制台编码 - 跨平台兼容
if IS_WINDOWS:
    try:
        import ctypes
        # 设置Windows控制台输出编码为UTF-8
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        # 尝试设置标准输出编码
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

def clear_screen():
    """跨平台清屏函数"""
    if IS_WINDOWS:
        os.system('cls')
    else:
        os.system('clear')

def get_home_dir():
    """获取用户主目录，跨平台兼容"""
    return os.path.expanduser('~')

def get_base_dir():
    """获取程序运行基准目录

    - 源码运行: 脚本所在目录
    - PyInstaller exe 运行: exe 所在目录（而非临时解压目录）
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def base_type(device_type):
    """去掉 _telnet 后缀，获取基础设备类型（用于查厂商/命令映射）"""
    return device_type.replace('_telnet', '') if device_type else device_type

# SSH legacy 算法兼容参数（华为 VRP5 等老设备只支持 ssh-rsa/group1-sha1 等旧算法，
# 新版 paramiko 默认优先 SHA2 会导致协商阶段失败）
def _patch_paramiko_legacy_algos():
    """临时放宽 paramiko 传输层算法列表，兼容华为VRP5等老设备

    注意：disabled_algorithms/look_for_keys 等是 paramiko connect() 的参数，
    netmiko 的 ConnectHandler 并不接受，因此只能在 paramiko 层面临时放开。
    返回原始值，用完必须传给 _restore_paramiko_algos 恢复。
    """
    transport = paramiko.Transport
    saved = {
        '_preferred_pubkeys': transport._preferred_pubkeys,
        '_preferred_keys': transport._preferred_keys,
        '_preferred_kex': transport._preferred_kex,
        '_preferred_ciphers': transport._preferred_ciphers,
    }
    def _extend(current, legacy):
        # 移除老设备不支持的 SHA2 签名，前置 legacy 算法
        current = tuple(a for a in current if a not in ('rsa-sha2-256', 'rsa-sha2-512'))
        return tuple(a for a in legacy if a not in current) + current
    transport._preferred_pubkeys = _extend(saved['_preferred_pubkeys'], ('ssh-rsa',))
    transport._preferred_keys = _extend(saved['_preferred_keys'], ('ssh-rsa',))
    transport._preferred_kex = _extend(saved['_preferred_kex'],
                                       ('diffie-hellman-group14-sha1', 'diffie-hellman-group1-sha1'))
    transport._preferred_ciphers = _extend(saved['_preferred_ciphers'],
                                           ('aes128-cbc', '3des-cbc'))
    return saved

def _restore_paramiko_algos(saved):
    """恢复 paramiko 默认算法列表"""
    for attr, value in saved.items():
        setattr(paramiko.Transport, attr, value)

# 支持 Telnet 的设备类型（netmiko 内置）
TELNET_DEVICE_TYPES = [
    'huawei_telnet',
    'hp_comware_telnet',
    'cisco_ios_telnet',
    'arista_eos_telnet',
    'juniper_junos_telnet',
]

def is_ssh_negotiation_error(exc):
    """判断异常是否属于 SSH 算法协商失败（老设备典型症状）"""
    msg = str(exc).lower()
    keywords = ('negotiation', 'no matching', 'kex', 'incompatible',
                'handshake', 'key exchange', 'algorithm')
    return any(k in msg for k in keywords)

def _connect_with_legacy_algos(params):
    """在临时放开的 legacy 算法环境下建立 netmiko 连接"""
    saved = _patch_paramiko_legacy_algos()
    try:
        return ConnectHandler(**params)
    finally:
        _restore_paramiko_algos(saved)

def open_connection(params):
    """统一连接入口：先按常规参数连接，SSH 协商失败时自动降级 legacy 算法

    支持 params 中的自定义字段:
      - legacy_ssh: true  显式标记老设备，直接走 legacy 参数
      - protocol: telnet  使用 Telnet（device_type 自动加 _telnet 后缀）
    """
    params = dict(params)
    legacy_ssh = params.pop('legacy_ssh', False)
    protocol = str(params.pop('protocol', 'ssh')).lower()

    # Telnet 支持：自动补 _telnet 后缀
    if protocol == 'telnet' and not params['device_type'].endswith('_telnet'):
        params['device_type'] = params['device_type'] + '_telnet'

    if legacy_ssh:
        return _connect_with_legacy_algos(params)

    try:
        return ConnectHandler(**params)
    except (NetmikoAuthenticationException, NetmikoTimeoutException):
        raise  # 认证/超时问题降级无意义，直接抛出
    except Exception as e:
        # Telnet 不涉及 SSH 协商，不重试
        if protocol == 'telnet':
            raise
        if isinstance(e, paramiko.ssh_exception.SSHException) or is_ssh_negotiation_error(e):
            print("\n⚠️  SSH算法协商失败，尝试legacy兼容模式（适用于华为VRP5等老设备）...")
            conn = _connect_with_legacy_algos(params)
            print("✅ legacy模式连接成功（建议在devices.json中为该设备加 \"legacy_ssh\": true 跳过探测）")
            return conn
        raise

def diagnose_error(err, device_info):
    """根据连接错误自动诊断原因并给出解决办法"""
    msg = str(err).lower()
    host = device_info.get('host', '?')
    port = device_info.get('port', 22)

    if is_ssh_negotiation_error(err) or 'negotiation failed' in msg or 'no matching' in msg:
        return (
            "【诊断】SSH 算法协商失败：设备为老版本系统（如华为 VRP5），不支持新版加密算法\n"
            "【解决】在 devices.json 中为该设备添加 \"legacy_ssh\": true 后重试"
        )
    if isinstance(err, NetmikoAuthenticationException) or 'authentication' in msg:
        return (
            "【诊断】认证失败：用户名/密码错误，或设备未给该用户开通 SSH 权限\n"
            "【解决】1) 核对 devices.json 中的用户名密码\n"
            "        2) 华为设备检查: local-user xxx service-type ssh / stelnet server enable"
        )
    if isinstance(err, NetmikoTimeoutException) or 'timed out' in msg or 'timed-out' in msg or '10060' in msg:
        return (
            f"【诊断】连接超时：{host}:{port} 网络层不可达\n"
            f"【解决】1) ping {host} 测试连通性  2) 检查中间防火墙/ACL 是否放行 {port} 端口\n"
            f"        3) 确认设备管理地址和端口配置正确"
        )
    if 'refused' in msg or '10061' in msg:
        return (
            f"【诊断】连接被拒绝：{host}:{port} 端口未开放\n"
            "【解决】1) 设备上启用 SSH: stelnet server enable（华为）/ ip ssh version 2（思科）\n"
            f"        2) 核对端口是否为 {port}（如为 Telnet 设备，protocol 填 telnet 且端口通常为 23）"
        )
    if isinstance(err, socket.error):
        return (
            f"【诊断】底层网络错误：{str(err)}\n"
            f"【解决】检查本机到 {host} 的链路、网卡状态、VPN/代理是否影响"
        )
    if 'typeerror' in msg or 'unexpected keyword' in msg:
        return (
            "【诊断】程序内部兼容性问题（组件版本不匹配）\n"
            "【解决】升级到最新版本代码；若使用 exe 请用最新 build.bat 重新打包"
        )
    return (
        f"【诊断】未归类错误：{type(err).__name__}: {str(err)}\n"
        "【解决】可依次排查: 设备类型是否匹配（菜单1自动检测）、编码(gbk/utf-8)、端口与协议"
    )


def ensure_dir(path):
    """确保目录存在，跨平台兼容"""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

SUPPORTED_DEVICE_TYPES = [
    'huawei',
    'hp_comware',
    'ruijie',
    'cisco_ios',
    'cisco_xe',
    'cisco_xr',
    'cisco_nxos',
    'juniper_junos',
    'arista_eos',
    'hp_procurve',
]

DEVICE_TYPE_INFO = {
    'huawei': {'vendor': '华为', 'encoding': 'gbk', 'has_secret': False},
    'hp_comware': {'vendor': '华三', 'encoding': 'gbk', 'has_secret': False},
    'ruijie': {'vendor': '锐捷', 'encoding': 'utf-8', 'has_secret': False},
    'cisco_ios': {'vendor': '思科 IOS', 'encoding': 'utf-8', 'has_secret': True},
    'cisco_xe': {'vendor': '思科 IOS-XE', 'encoding': 'utf-8', 'has_secret': True},
    'cisco_xr': {'vendor': '思科 IOS-XR', 'encoding': 'utf-8', 'has_secret': True},
    'cisco_nxos': {'vendor': '思科 Nexus', 'encoding': 'utf-8', 'has_secret': True},
    'juniper_junos': {'vendor': 'Juniper', 'encoding': 'utf-8', 'has_secret': False},
    'arista_eos': {'vendor': 'Arista', 'encoding': 'utf-8', 'has_secret': False},
    'hp_procurve': {'vendor': 'HP ProCurve', 'encoding': 'utf-8', 'has_secret': False},
}

# 思科设备基础配置模板 - 用于继承
CISCO_BASE_CONFIG = {
    'show_interfaces': 'show ip interface brief',
    'show_config': 'show running-config',
    'show_saved_config': 'show startup-config',
    'show_cpu': 'show processes cpu',
    'show_memory': 'show memory',
    'show_fan': 'show environment fan',
    'show_temperature': 'show environment temperature',
    'show_power': 'show environment power',
    'show_version': 'show version',
}

COMMAND_MAPPING = {
    'huawei': {
        'show_interfaces': 'display ip interface brief',
        'show_config': 'display current-configuration',
        'show_saved_config': 'display saved-configuration',
        'show_cpu': 'display cpu-usage',
        'show_memory': 'display memory-usage',
        'show_fan': 'display fan',
        'show_temperature': 'display temperature',
        'show_power': 'display power',
        'show_version': 'display version',
    },
    'hp_comware': {
        'show_interfaces': 'display ip interface brief',
        'show_config': 'display current-configuration',
        'show_saved_config': 'display saved-configuration',
        'show_cpu': 'display cpu-usage',
        'show_memory': 'display memory',
        'show_fan': 'display fan',
        'show_temperature': 'display temperature',
        'show_power': 'display power',
        'show_version': 'display version',
    },
    'ruijie': {
        'show_interfaces': 'show interfaces status',
        'show_config': 'show running-config',
        'show_saved_config': 'show startup-config',
        'show_cpu': 'show cpu',
        'show_memory': 'show memory',
        'show_fan': 'show fan',
        'show_temperature': 'show temperature',
        'show_power': 'show power',
        'show_version': 'show version',
    },
    # 思科设备配置 - 使用继承减少重复
    'cisco_ios': CISCO_BASE_CONFIG.copy(),
    'cisco_xe': CISCO_BASE_CONFIG.copy(),
    'cisco_xr': CISCO_BASE_CONFIG.copy(),
    'cisco_nxos': {
        **CISCO_BASE_CONFIG,
        'show_cpu': 'show system resources',
        'show_memory': 'show system memory',
    },
    'juniper_junos': {
        'show_interfaces': 'show interfaces terse',
        'show_config': 'show configuration',
        'show_saved_config': 'show configuration',
        'show_cpu': 'show system cpu',
        'show_memory': 'show system memory',
        'show_fan': 'show chassis fan',
        'show_temperature': 'show chassis temperature',
        'show_power': 'show chassis power',
        'show_version': 'show version',
    },
}

def get_command(device_type, command_key):
    """根据设备类型获取对应命令（自动剥离 _telnet 后缀）"""
    mapping = COMMAND_MAPPING.get(base_type(device_type), COMMAND_MAPPING['huawei'])
    return mapping.get(command_key, '')

def detect_device_type(ip, username, password, port=22, timeout=10):
    """自动检测设备类型

    尝试用不同的 device_type 连接设备，返回检测到的类型和提示符

    返回: (device_type, prompt) 或 (None, None)
    """
    print(f"\n{'='*60}")
    print(f"🔍 正在探测设备类型: {ip}")
    print(f"{'='*60}")

    types_to_try = TELNET_DEVICE_TYPES if port == 23 else SUPPORTED_DEVICE_TYPES

    for device_type in types_to_try:
        print(f"尝试 {device_type} ...", end=' ', flush=True)

        device_params = {
            'device_type': device_type,
            'host': ip,
            'username': username,
            'password': password,
            'port': port,
            'timeout': timeout,
            'conn_timeout': timeout,
            'banner_timeout': timeout,
            'auth_timeout': timeout,
        }

        try:
            conn = open_connection(device_params)
            prompt = conn.find_prompt()
            conn.disconnect()

            vendor_info = DEVICE_TYPE_INFO.get(base_type(device_type), {})
            print(f"✅ 成功!")
            print(f"\n{'='*60}")
            print(f"🎉 设备类型检测结果:")
            print(f"{'='*60}")
            print(f"  IP 地址: {ip}")
            print(f"  厂商: {vendor_info.get('vendor', '未知')}")
            print(f"  device_type: {device_type}")
            print(f"  提示符: {prompt}")
            print(f"  推荐编码: {vendor_info.get('encoding', 'utf-8')}")
            print(f"{'='*60}")

            return device_type, prompt

        except NetmikoAuthenticationException:
            print(f"❌ 认证失败")
            return None, None

        except NetmikoTimeoutException:
            print(f"⏱️", end=' ', flush=True)

        except socket.error:
            print(f"🌐", end=' ', flush=True)

        except Exception:
            print(f"❓", end=' ', flush=True)

    print(f"\n❌ 未找到匹配的设备类型")
    return None, None

def auto_config_device(ip, username, password, port=22, secret=None, timeout=10):
    """自动配置设备参数

    1. 自动检测设备类型
    2. 根据检测到的类型自动设置 encoding
    3. 如果是思科设备且提供了 secret，设置 secret

    返回: 设备配置字典 或 None
    """
    device_type, prompt = detect_device_type(ip, username, password, port, timeout)

    if not device_type:
        return None

    device_info = {
        'device_type': device_type,
        'host': ip,
        'username': username,
        'password': password,
        'port': port,
        'encoding': DEVICE_TYPE_INFO[base_type(device_type)]['encoding'],
    }

    if secret:
        device_info['secret'] = secret

    if DEVICE_TYPE_INFO[base_type(device_type)]['has_secret'] and not secret:
        print(f"\n⚠️  提示: {DEVICE_TYPE_INFO[base_type(device_type)]['vendor']} 设备通常需要特权密码")
        print(f"   如需配置，请在 devices.json 中添加 'secret' 字段")

    hostname = prompt.strip('[]<>#').split('-')[0]
    device_name = input(f"\n请输入设备名称 (默认: {hostname}): ").strip()
    if not device_name:
        device_name = hostname

    return {
        'name': device_name,
        'info': device_info,
        'device_type': device_type,
        'prompt': prompt,
    }

def add_device_interactive():
    """交互式添加设备

    用户输入 IP、用户名、密码、端口、特权密码(可选)
    自动检测设备类型并配置参数

    返回: (设备名, 设备信息) 或 (None, None)
    """
    print("\n" + "="*60)
    print("📝 交互式添加设备")
    print("="*60)

    ip = input("请输入设备 IP 地址: ").strip()
    if not ip:
        print("❌ IP 地址不能为空")
        return None, None

    username = input("请输入用户名 : ").strip() or "admin"

    password = input("请输入密码: ").strip()
    if not password:
        print("❌ 密码不能为空")
        return None, None

    port_input = input("请输入 SSH 端口 (默认: 22): ").strip()
    port = int(port_input) if port_input else 22

    secret_input = input("请输入特权密码 (可选，回车跳过): ").strip()
    secret = secret_input if secret_input else None

    print("\n" + "-"*60)
    print("开始自动检测设备类型...")
    result = auto_config_device(ip, username, password, port, secret)

    if result:
        print(f"\n✅ 设备配置完成!")
        print(f"   设备名称: {result['name']}")
        print(f"   设备类型: {result['device_type']}")
        return result['name'], result['info']

    return None, None

def load_devices_from_file(filename=None):
    """从 JSON 文件加载设备配置（默认读取程序目录下的 devices.json）"""
    if filename is None:
        filename = os.path.join(get_base_dir(), 'devices.json')
    try:
        if not os.path.exists(filename):
            print(f"❌ 设备配置文件 {filename} 不存在")
            return {}
        with open(filename, 'r', encoding='utf-8') as f:
            devices = json.load(f)
        print(f"✅ 成功加载 {len(devices)} 个设备配置")
        return devices
    except json.JSONDecodeError:
        print(f"❌ {filename} 格式不正确")
        return {}
    except Exception as e:
        print(f"❌ 加载设备配置时出错: {str(e)}")
        return {}

def save_devices_to_file(devices, filename=None):
    """保存设备配置到 JSON 文件（默认写入程序目录下的 devices.json）"""
    if filename is None:
        filename = os.path.join(get_base_dir(), 'devices.json')
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(devices, f, indent=4, ensure_ascii=False)
        print(f"✅ 设备配置已保存到 {filename}")
        return True
    except Exception as e:
        print(f"❌ 保存设备配置失败: {str(e)}")
        return False

def parse_bool(value):
    """解析表格中的布尔值"""
    return str(value).strip().lower() in ('true', '1', 'yes', 'y', '是')

def import_devices_from_file(devices):
    """扫描程序目录下的 BatchDevice 表格文件，批量导入设备

    支持 BatchDevice.csv / BatchDevice.xlsx，字段:
    name*, host*, username*, password*, device_type, port, secret, encoding, protocol, legacy_ssh

    返回: 新导入的设备 {name: info}
    """
    base_dir = get_base_dir()
    candidates = [f for f in os.listdir(base_dir)
                  if f.lower().startswith('batchdevice')
                  and not f.lower().startswith('batchdevice.example')
                  and f.lower().endswith(('.csv', '.xlsx', '.xls'))]

    if not candidates:
        print(f"\n❌ 在 {base_dir} 下未找到 BatchDevice.csv / BatchDevice.xlsx")
        print("   请参考 BatchDevice.example.csv 模板填写后重试")
        return {}

    print(f"\n找到 {len(candidates)} 个设备表格:")
    for i, f in enumerate(candidates, 1):
        print(f"  {i}. {f}")

    if len(candidates) == 1:
        selected = candidates[0]
        print(f"自动选择: {selected}")
    else:
        sel = input("请选择要导入的文件编号: ").strip()
        if not sel.isdigit() or not (1 <= int(sel) <= len(candidates)):
            print("❌ 无效选择")
            return {}
        selected = candidates[int(sel) - 1]

    filepath = os.path.join(base_dir, selected)
    ext = os.path.splitext(selected)[1].lower()

    # 读取行数据
    rows = []
    try:
        if ext == '.csv':
            import csv
            content = None
            for enc in ('utf-8-sig', 'gbk'):  # Excel 导出的中文 CSV 多为 GBK
                try:
                    with open(filepath, 'r', encoding=enc) as f:
                        content = list(csv.reader(f))
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                print("❌ CSV 编码无法识别（支持 UTF-8 / GBK）")
                return {}
            rows = content
        elif ext == '.xlsx':
            try:
                from openpyxl import load_workbook
            except ImportError:
                print("❌ 读取 xlsx 需要 openpyxl: pip install openpyxl")
                print("   或将文件另存为 CSV 后重试")
                return {}
            wb = load_workbook(filepath, read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                rows.append(['' if c is None else str(c) for c in row])
            wb.close()
        else:  # .xls
            print("❌ 不支持旧版 .xls 格式，请在 Excel 中另存为 .xlsx 或 .csv")
            return {}
    except Exception as e:
        print(f"❌ 读取文件失败: {str(e)}")
        return {}

    if len(rows) < 2:
        print("❌ 表格为空或只有表头")
        return {}

    # 解析表头
    headers = [h.strip().lower() for h in rows[0]]
    required = ['name', 'host', 'username', 'password']
    missing = [h for h in required if h not in headers]
    if missing:
        print(f"❌ 表头缺少必需字段: {', '.join(missing)}")
        print(f"   当前表头: {', '.join(headers)}")
        return {}

    idx = {h: headers.index(h) for h in headers}
    imported = {}
    skipped = 0

    for lineno, row in enumerate(rows[1:], 2):
        if not row or not ''.join(row).strip():
            continue  # 跳过空行

        def cell(key, default=''):
            i = idx.get(key)
            return row[i].strip() if i is not None and i < len(row) else default

        name, host = cell('name'), cell('host')
        username, password = cell('username'), cell('password')
        if not all([name, host, username, password]):
            print(f"  ⚠️ 第{lineno}行缺少必填字段，已跳过")
            skipped += 1
            continue
        if name in devices:
            print(f"  ⚠️ 设备 {name} 已存在，已覆盖")

        device_type = cell('device_type', 'huawei') or 'huawei'
        protocol = cell('protocol', 'ssh').lower() or 'ssh'
        port_str = cell('port')
        if port_str.isdigit():
            port = int(port_str)
        else:
            port = 23 if protocol == 'telnet' else 22

        info = {
            'device_type': device_type,
            'host': host,
            'username': username,
            'password': password,
            'port': port,
            'encoding': cell('encoding') or DEVICE_TYPE_INFO.get(
                base_type(device_type), {}).get('encoding', 'utf-8'),
        }
        secret = cell('secret')
        if secret:
            info['secret'] = secret
        if protocol == 'telnet':
            info['protocol'] = 'telnet'
        if parse_bool(cell('legacy_ssh')):
            info['legacy_ssh'] = True

        devices[name] = info
        imported[name] = info

    if not imported:
        print(f"❌ 没有成功导入任何设备（跳过 {skipped} 行）")
        return {}

    save_devices_to_file(devices)
    print(f"\n✅ 成功导入 {len(imported)} 台设备" +
          (f"，跳过 {skipped} 行" if skipped else ""))

    choice = input("是否立即批量连接这些设备? (y/n): ").strip().lower()
    if choice == 'y':
        return imported
    return {}


# ==================== 自动化巡检 ====================
INSPECT_INTERVALS = [
    ('5分钟', 300), ('30分钟', 1800), ('1小时', 3600), ('4小时', 14400),
    ('6小时', 21600), ('12小时', 43200), ('24小时', 86400),
]
INSPECT_SETTINGS_FILE = 'inspect_settings.json'
ALERT_STATE_FILE = 'alert_state.json'
# 巡检采集的命令（按 COMMAND_MAPPING 的键取各厂商对应命令）
INSPECT_ITEMS = [
    ('版本信息', 'show_version'), ('CPU占用', 'show_cpu'),
    ('内存占用', 'show_memory'), ('温度状态', 'show_temperature'),
    ('接口状态', 'show_interfaces'),
]
inspect_state = {'thread': None, 'stop_event': None, 'desc': ''}

# 告警默认配置：全局阈值 + 设备级覆盖（settings['devices'][设备名]）
DEFAULT_ALERT_SETTINGS = {
    'enabled': True,
    'cpu_threshold': 80,          # CPU 使用率告警阈值(%)
    'memory_threshold': 85,       # 内存使用率告警阈值(%)
    'temperature_threshold': 70,  # 温度告警阈值(℃)
    'confirm_rounds': 2,          # 连续 N 轮超阈值才告警，抑制瞬时尖峰
    'suppress_minutes': 120,      # 同一告警抑制窗口(分钟)
    'check_interface_down': True, # 接口 Down 告警
    'check_config_change': True,  # 配置变更告警
    'alert_to': [],               # 告警收件人，留空则用 email.to
    'devices': {},                # 例: {"Core1": {"cpu_threshold": 90, "ignore_interfaces": ["GE0/0/1"]}}
}

def _inspect_settings_path():
    return os.path.join(get_base_dir(), INSPECT_SETTINGS_FILE)

def _alert_state_path():
    return os.path.join(get_base_dir(), ALERT_STATE_FILE)

def load_inspect_settings():
    """读取巡检与告警配置"""
    default = {
        'local': {'enabled': True},
        'email': {'enabled': False},
        'syslog': {'enabled': False},
        'alerts': dict(DEFAULT_ALERT_SETTINGS),
    }
    if os.path.exists(_inspect_settings_path()):
        try:
            with open(_inspect_settings_path(), 'r', encoding='utf-8') as f:
                saved = json.load(f)
            merged = {**default, **saved}
            merged['alerts'] = {**DEFAULT_ALERT_SETTINGS, **saved.get('alerts', {})}
            return merged
        except Exception:
            pass
    return default

def save_inspect_settings(settings):
    try:
        with open(_inspect_settings_path(), 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 巡检配置保存失败: {e}")

def load_alert_state():
    if os.path.exists(_alert_state_path()):
        try:
            with open(_alert_state_path(), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_alert_state(state):
    try:
        with open(_alert_state_path(), 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 告警状态保存失败: {e}")

# ==================== 输出解析 ====================

def _first_int(patterns, output):
    for pat in patterns:
        m = re.search(pat, output)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None

def parse_cpu_usage(output):
    """从各厂商 CPU 命令输出中提取使用率(%)"""
    if not output:
        return None
    return _first_int([
        r'CPU Usage\s*:\s*(\d+)\s*%',                       # 华为 VRP
        r'CPU utilization for five seconds:\s*(\d+)',        # 思科
        r'CPU utilization.*?(\d+)%',                         # 思科/锐捷变体
        r'CPU usage\s*:\s*(\d+)',                            # 华三
        r'(\d+)%\s*(?:in last 5 minutes|\(5 min\))',
    ], output)

def parse_memory_usage(output):
    """从内存命令输出中提取使用率(%)"""
    if not output:
        return None
    return _first_int([
        r'Memory Using Percentage Is\s*:?\s*(\d+)\s*%',      # 华为
        r'Memory utilization\s*:?\s*(\d+)\s*%',              # 华为/华三
        r'Memory [Uu]sage\s*:?\s*(\d+)\s*%',
        r'Used Memory Percentage\s*:?\s*(\d+)\s*%',
    ], output)

def parse_temperature(output):
    """提取输出中的最高温度(℃)，取不到返回 None"""
    if not output:
        return None
    temps = [int(x) for x in re.findall(r'(\d+)\s*(?:degrees? Celsius|°C|℃)', output)]
    if not temps:
        # 华为 VRP 部分版本仅输出 "Temperature: 35" 结构
        temps = [int(x) for x in re.findall(r'Temperature[^\d]{0,12}(\d{1,3})', output)]
    temps = [t for t in temps if 0 <= t <= 120]
    return max(temps) if temps else None

def parse_interface_stats(output):
    """解析接口列表：返回(总数, up数, down数, down接口列表)

    华为/华三 `display ip interface brief` 与思科 `show ip interface brief`
    末两列均为“物理/链路状态 + 协议状态”，因此统一取每行最后两列判断。
    """
    total = up = down = 0
    down_list = []
    if not output:
        return total, up, down, down_list
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[0]
        if not re.match(r'^[A-Za-z][\w\-\./:]*$', name) or not any(c.isdigit() for c in name):
            continue
        if 'administratively' in line.lower():   # 人为 shutdown，不告警
            continue
        phy, proto = parts[-2].lower(), parts[-1].lower()
        if phy in ('administratively', 'admin'):   # 兼容列拆分后的形态
            continue
        if phy not in ('up', 'down', '*down') and proto not in ('up', 'down'):
            continue
        total += 1
        if phy.startswith('up') and proto.startswith('up'):
            up += 1
        else:
            down += 1
            down_list.append(name)
    return total, up, down, down_list

def config_digest(output):
    """配置文本指纹，用于检测配置变更"""
    if not output:
        return None
    normalized = '\n'.join(l.rstrip() for l in output.splitlines() if l.strip())
    return hashlib.md5(normalized.encode('utf-8', 'ignore')).hexdigest()

# ==================== 巡检采集 ====================

def collect_inspection_data(targets, connections, devices, settings):
    """执行一轮巡检，返回结构化行数据（用于报表与告警判断）"""
    rows = []
    alert_cfg = settings.get('alerts', {})
    for name in targets:
        info = devices.get(name, {})
        base = base_type(info.get('device_type', 'huawei'))
        cmds = COMMAND_MAPPING.get(base, COMMAND_MAPPING['huawei'])
        row = {
            'name': name, 'host': info.get('host', '?'), 'type': base,
            'reachable': False, 'cpu': None, 'memory': None, 'temperature': None,
            'if_total': 0, 'if_up': 0, 'if_down': 0, 'down_interfaces': [],
            'config_hash': None, 'error': '', 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        conn = connections.get(name)
        if conn is None:
            print(f"\n[巡检] {name} 未连接，尝试自动重连...")
            try:
                conn = connect_single_device(info, name)
            except Exception:
                conn = None
            if conn:
                connections[name] = conn

        if conn is None:
            row['error'] = '设备不可达/连接失败'
            rows.append(row)
            continue

        try:
            for label, key in INSPECT_ITEMS:
                cmd = cmds.get(key)
                if not cmd:
                    continue
                try:
                    output = conn.send_command(cmd, read_timeout=30)
                except Exception as e:
                    row['error'] = f'{label}采集失败: {type(e).__name__}'
                    continue
                if key == 'show_cpu':
                    row['cpu'] = parse_cpu_usage(output)
                elif key == 'show_memory':
                    row['memory'] = parse_memory_usage(output)
                elif key == 'show_temperature':
                    row['temperature'] = parse_temperature(output)
                elif key == 'show_interfaces':
                    total, up, down, down_list = parse_interface_stats(output)
                    row.update(if_total=total, if_up=up, if_down=down, down_interfaces=down_list)

            if alert_cfg.get('check_config_change'):
                cfg_cmd = cmds.get('show_config')
                if cfg_cmd:
                    try:
                        row['config_hash'] = config_digest(conn.send_command(cfg_cmd, read_timeout=60))
                    except Exception:
                        row['config_hash'] = None

            row['reachable'] = True
        except Exception as e:
            row['error'] = f'{type(e).__name__}: {e}'
        rows.append(row)
    return rows

# ==================== 告警判定 ====================

def _device_cfg(alert_cfg, name):
    """合并全局告警配置与设备级覆盖"""
    cfg = dict(alert_cfg)
    cfg.update(alert_cfg.get('devices', {}).get(name, {}))
    return cfg

def evaluate_alerts(rows, settings, state):
    """对比上一轮状态产生告警（边沿触发 + 连续确认 + 抑制窗口）

    返回 (alerts, recovered)；alerts 为本次新触发的告警列表。
    """
    alert_cfg = settings.get('alerts', {})
    if not alert_cfg.get('enabled', True):
        return [], []

    alerts, recovered = [], []
    now = datetime.now()
    confirm = max(1, int(alert_cfg.get('confirm_rounds', 2)))
    suppress = int(alert_cfg.get('suppress_minutes', 120)) * 60

    for row in rows:
        name = row['name']
        cfg = _device_cfg(alert_cfg, name)
        dev = state.setdefault(name, {})
        active = dev.setdefault('active', {})

        def emit(kind, level, message, scope):
            """仅在状态从未告警变为告警时推送，抑制窗口内的重复发生不再推送"""
            key = f'{kind}:{scope}' if scope else kind
            if key in active:
                return
            last_sent = dev.setdefault('last_sent', {})
            last = last_sent.get(key, 0)
            if last and (now.timestamp() - last) < suppress:
                return  # 抑制期内重复发生（如接口抖动），静默
            active[key] = {'since': now.strftime('%Y-%m-%d %H:%M:%S'), 'level': level}
            last_sent[key] = now.timestamp()
            alerts.append({'level': level, 'device': name, 'type': message,
                           'time': now.strftime('%Y-%m-%d %H:%M:%S')})

        def clear(kind, scope, message):
            key = f'{kind}:{scope}' if scope else kind
            if key in active:
                del active[key]
                recovered.append({'device': name, 'type': message,
                                  'time': now.strftime('%Y-%m-%d %H:%M:%S')})

        # 1) 设备可达性
        if not row['reachable']:
            emit('unreachable', 'CRITICAL', '设备不可达 / 连接失败', '')
            dev.pop('cpu_high_count', None)
            dev.pop('memory_high_count', None)
            continue
        clear('unreachable', '', '设备已恢复连接')

        # 2) CPU 阈值（连续确认）
        cpu = row['cpu']
        if cpu is not None:
            if cpu >= int(cfg.get('cpu_threshold', 80)):
                dev['cpu_high_count'] = dev.get('cpu_high_count', 0) + 1
                if dev['cpu_high_count'] >= confirm:
                    emit('cpu_high', 'MAJOR', f"CPU使用率 {cpu}% 超过阈值 {cfg.get('cpu_threshold')}%", '')
            else:
                dev['cpu_high_count'] = 0
                clear('cpu_high', '', 'CPU使用率恢复正常')

        # 3) 内存阈值（连续确认）
        mem = row['memory']
        if mem is not None:
            if mem >= int(cfg.get('memory_threshold', 85)):
                dev['memory_high_count'] = dev.get('memory_high_count', 0) + 1
                if dev['memory_high_count'] >= confirm:
                    emit('memory_high', 'MAJOR', f"内存使用率 {mem}% 超过阈值 {cfg.get('memory_threshold')}%", '')
            else:
                dev['memory_high_count'] = 0
                clear('memory_high', '', '内存使用率恢复正常')

        # 4) 温度阈值
        temp = row['temperature']
        if temp is not None:
            if temp >= int(cfg.get('temperature_threshold', 70)):
                emit('temperature_high', 'CRITICAL', f'设备温度 {temp}℃ 超过阈值 {cfg.get("temperature_threshold")}℃', '')
            else:
                clear('temperature_high', '', '设备温度恢复正常')

        # 5) 接口 Down（与上一轮比对，仅新增/恢复时告警）
        if cfg.get('check_interface_down', True):
            ignore = set(cfg.get('ignore_interfaces', []))
            prev_down = set(dev.get('down_interfaces', []))
            cur_down = set(row['down_interfaces']) - ignore
            for iface in sorted(cur_down - prev_down):
                emit('if_down', 'MAJOR' if not prev_down else 'MINOR',
                     f'接口 {iface} Down', iface)
            for iface in sorted(prev_down - cur_down):
                clear('if_down', iface, f'接口 {iface} 已恢复 Up')
            dev['down_interfaces'] = sorted(cur_down)

        # 6) 配置变更（每次变更各告警一次，不重复）
        if cfg.get('check_config_change') and row['config_hash']:
            prev_hash = dev.get('config_hash')
            if prev_hash and prev_hash != row['config_hash']:
                alerts.append({'level': 'INFO', 'device': name,
                               'type': '设备配置发生变更',
                               'time': now.strftime('%Y-%m-%d %H:%M:%S')})
            dev['config_hash'] = row['config_hash']

    return alerts, recovered

# ==================== 报表与通知 ====================

def _now_ts():
    return datetime.now().strftime('%Y%m%d_%H%M%S')

def build_excel_report(rows, alerts, path):
    """生成关键信息巡检报表（.xlsx）"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = '巡检汇总'

    headers = ['设备名称', '管理IP', '设备类型', '状态', 'CPU(%)', '内存(%)',
               '温度(℃)', '接口总数', 'Up', 'Down', 'Down接口', '采集时间']
    ws.append(headers)

    head_fill = PatternFill('solid', fgColor='1F4E78')
    head_font = Font(color='FFFFFF', bold=True)
    thin = Side(style='thin', color='BFBFBF')
    for cell in ws[1]:
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)

    abnormal_fill = PatternFill('solid', fgColor='FFC7CE')
    for row in rows:
        status = '在线' if row['reachable'] else '异常'
        down_text = ', '.join(row['down_interfaces'][:10])
        if len(row['down_interfaces']) > 10:
            down_text += f' 等{len(row["down_interfaces"])}个'
        ws.append([
            row['name'], row['host'], row['type'], status,
            row['cpu'] if row['cpu'] is not None else '-',
            row['memory'] if row['memory'] is not None else '-',
            row['temperature'] if row['temperature'] is not None else '-',
            row['if_total'], row['if_up'], row['if_down'],
            down_text, row['time'],
        ])
        if not row['reachable'] or row['if_down']:
            for cell in ws[ws.max_row]:
                cell.fill = abnormal_fill

    for i, width in enumerate([18, 16, 14, 8, 9, 9, 10, 10, 7, 8, 34, 20], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{ws.max_row}'

    ws2 = wb.create_sheet('告警记录')
    ws2.append(['时间', '级别', '设备', '告警内容'])
    for cell in ws2[1]:
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal='center')
    for a in alerts:
        ws2.append([a['time'], a['level'], a['device'], a['type']])
    for i, width in enumerate([20, 10, 18, 50], 1):
        ws2.column_dimensions[get_column_letter(i)].width = width
    if not alerts:
        ws2.append(['-', '-', '-', '本轮无告警'])

    wb.save(path)
    return path

def _smtp_send(email_cfg, to_list, subject, body, attachment=None):
    """发送邮件，attachment 为附件文件路径"""
    if attachment:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        with open(attachment, 'rb') as f:
            part = MIMEApplication(
                f.read(), _subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        part.add_header('Content-Disposition', 'attachment',
                        filename=os.path.basename(attachment))
        msg.attach(part)
    else:
        msg = MIMEText(body, 'plain', 'utf-8')
    msg['From'] = email_cfg['username']
    msg['To'] = ','.join(to_list)
    msg['Subject'] = Header(subject, 'utf-8')

    port = int(email_cfg.get('port', 465))
    if port == 465:
        server = smtplib.SMTP_SSL(email_cfg['server'], port, timeout=30)
    else:
        server = smtplib.SMTP(email_cfg['server'], port, timeout=30)
        if port == 587:
            server.starttls()
    try:
        server.login(email_cfg['username'], email_cfg['password'])
        server.sendmail(email_cfg['username'], to_list, msg.as_string())
    finally:
        server.quit()

def deliver_inspection_report(rows, alerts, recovered, settings):
    """分发报表：本地 xlsx / 邮件附件 / syslog；告警单独即时推送"""
    ts = _now_ts()
    path = None

    if settings.get('local', {}).get('enabled', True):
        try:
            reports_dir = os.path.join(get_base_dir(), 'reports')
            ensure_dir(reports_dir)
            path = os.path.join(reports_dir, f'inspection_report_{ts}.xlsx')
            build_excel_report(rows, alerts, path)
            print(f"[巡检] 报表已保存: {path}")
        except Exception as e:
            print(f"[巡检] 报表生成失败: {type(e).__name__} {e}")
            path = None

    email = settings.get('email', {})
    if email.get('enabled') and email.get('to'):
        try:
            abnormal = [r for r in rows if not r['reachable'] or r['if_down']]
            body = (f"网络设备巡检报表 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"巡检设备: {len(rows)} 台 | 异常设备: {len(abnormal)} 台 | 本轮告警: {len(alerts)} 条\n"
                    f"详见附件 Excel 报表。")
            _smtp_send(email, email['to'], f'网络设备巡检报表 {ts}', body, path)
            print(f"[巡检] 报表已发送至: {','.join(email['to'])}")
        except Exception as e:
            print(f"[巡检] 邮件发送失败: {type(e).__name__} {e}")

    syslog = settings.get('syslog', {})
    if syslog.get('enabled'):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for r in rows:
                if r['reachable'] and not r['if_down']:
                    continue
                line = f"[巡检] {r['name']}({r['host']}) 状态:{'在线' if r['reachable'] else '异常'} Down接口:{r['if_down']}"
                sock.sendto(f"<134>{line}".encode('utf-8', 'ignore'),
                            (syslog['server'], int(syslog.get('port', 514))))
            sock.close()
            print(f"[巡检] 异常摘要已发送至 {syslog['server']}:{syslog.get('port', 514)}")
        except Exception as e:
            print(f"[巡检] 日志服务器发送失败: {e}")

def send_alert_email(alerts, recovered, settings):
    """触发告警时即时发送告警邮件给管理员"""
    if not alerts and not recovered:
        return
    email = settings.get('email', {})
    recipients = settings.get('alerts', {}).get('alert_to') or email.get('to', [])
    if not email.get('enabled') or not recipients:
        print("[告警] 未配置告警收件人（email 或 alerts.alert_to），跳过邮件通知")
        return

    lines = []
    if alerts:
        lines.append('【新告警】')
        for a in alerts:
            lines.append(f"[{a['level']}] {a['device']}: {a['type']}  ({a['time']})")
    if recovered:
        lines.append('\n【已恢复】')
        for r in recovered:
            lines.append(f"{r['device']}: {r['type']}  ({r['time']})")
    body = '\n'.join(lines)

    subject = f"[告警] {len(alerts)}条新告警"
    if recovered:
        subject += f" / {len(recovered)}条恢复"
    try:
        _smtp_send(email, recipients, subject, body)
        print(f"[告警] 已发送至: {','.join(recipients)}")
    except Exception as e:
        print(f"[告警] 邮件发送失败: {type(e).__name__} {e}")

# ==================== 巡检主循环 ====================

def _inspect_loop(targets, interval_sec, settings, connections, devices):
    """巡检后台线程：立即执行一轮，之后按周期执行"""
    round_no = 1
    state = load_alert_state()
    while not inspect_state['stop_event'].is_set():
        print(f"\n[巡检] ===== 第 {round_no} 轮开始 ({datetime.now().strftime('%H:%M:%S')}) =====")
        rows = collect_inspection_data(targets, connections, devices, settings)
        alerts, recovered = evaluate_alerts(rows, settings, state)
        save_alert_state(state)

        if alerts or recovered:
            send_alert_email(alerts, recovered, settings)

        deliver_inspection_report(rows, alerts, recovered, settings)
        abnormal = sum(1 for r in rows if not r['reachable'])
        print(f"[巡检] 第 {round_no} 轮完成: 在线 {len(rows) - abnormal}/{len(rows)} 台，"
              f"告警 {len(alerts)} 条，{interval_sec // 60} 分钟后下一轮")
        round_no += 1
        if inspect_state['stop_event'].wait(interval_sec):
            break

def _list_devices_for_selection(devices, connections):
    """列出全部设备及连接状态"""
    names = list(devices.keys())
    print("\n设备列表:")
    for i, n in enumerate(names, 1):
        status = '已连接' if n in connections else '未连接'
        print(f"  {i}. {n:<16} {devices[n].get('host', '?'):<16} [{status}]")
    return names

def choose_inspection_targets(devices, connections, save=True):
    """选择巡检设备，可选择保存为“指定设备”"""
    if not devices:
        print("❌ 没有可用的设备配置")
        return []
    names = _list_devices_for_selection(devices, connections)
    sel = input("巡检范围 - a=全部 / c=仅已连接 / 编号逗号分隔(如 1,3): ").strip().lower()

    if sel == 'a' or not sel:
        targets = names
    elif sel == 'c':
        targets = [n for n in names if n in connections]
    else:
        targets = [names[int(x) - 1] for x in sel.split(',')
                   if x.strip().isdigit() and 1 <= int(x) <= len(names)]
    targets = list(dict.fromkeys(targets))
    if not targets:
        print("❌ 未选择任何设备")
        return []

    if save and input("是否将该选择保存为“指定设备”? (y/n): ").strip().lower() == 'y':
        settings = load_inspect_settings()
        settings['targets'] = targets
        save_inspect_settings(settings)
        print(f"✅ 已保存指定设备: {', '.join(targets)}")
    return targets

def configure_email(settings):
    """配置邮件通道；已有配置时只需修改收件人"""
    old = settings.get('email', {})
    if old.get('enabled') and old.get('server'):
        print(f"\n当前发件: {old.get('username', '')} @ {old.get('server')}:{old.get('port', 465)}")
        print(f"当前收件人: {', '.join(old.get('to', [])) or '(未设置)'}")
        if input("沿用发件服务器与账号? (y/n, 默认y): ").strip().lower() != 'n':
            to = input("收件邮箱(逗号分隔，直接回车保持不变): ").strip()
            recipients = [x.strip() for x in to.split(',') if x.strip()] or old.get('to', [])
            if not recipients:
                print("⚠️ 未设置收件人，邮件通道未启用")
                settings['email'] = {**old, 'enabled': False}
                return
            settings['email'] = {**old, 'enabled': True, 'to': recipients}
            print(f"✅ 邮件收件人已更新: {', '.join(recipients)}")
            return

    print("--- 邮箱配置（QQ邮箱示例: smtp.qq.com / 465 / 授权码）---")
    port_str = input("端口(465/587/25, 默认465): ").strip()
    recipients = [x.strip() for x in input("收件邮箱(多个用逗号分隔): ").split(',') if x.strip()]
    if not recipients:
        print("⚠️ 未填写收件人，邮件通道未启用")
        settings['email'] = {**old, 'enabled': False}
        return
    settings['email'] = {
        'enabled': True,
        'server': input("SMTP服务器: ").strip(),
        'port': int(port_str) if port_str.isdigit() else 465,
        'username': input("发件邮箱: ").strip(),
        'password': input("邮箱密码/授权码: ").strip(),
        'to': recipients,
    }
    print(f"✅ 邮件通道已启用，收件人: {', '.join(recipients)}")

def show_inspection_config():
    """查看当前巡检配置"""
    settings = load_inspect_settings()
    targets = settings.get('targets', [])
    email = settings.get('email', {})
    syslog = settings.get('syslog', {})
    a = settings.get('alerts', {})

    print("\n===== 当前巡检配置 =====")
    print(f"指定设备: {', '.join(targets) if targets else '(未设置，启动时选择)'}")
    print(f"本地文件: {'开启' if settings.get('local', {}).get('enabled', True) else '关闭'}")
    if email.get('enabled'):
        print(f"邮件发送: 开启 | {email.get('server')}:{email.get('port', 465)} | 收件人 {', '.join(email.get('to', []))}")
    else:
        saved_to = f"（已保存收件人: {', '.join(email['to'])}）" if email.get('to') else ""
        print(f"邮件发送: 关闭{saved_to}")
    if syslog.get('enabled'):
        print(f"日志服务器: 开启 | {syslog.get('server')}:{syslog.get('port', 514)}")
    else:
        print("日志服务器: 关闭")

    print(f"告警功能: {'开启' if a.get('enabled', True) else '关闭'}")
    if a.get('enabled', True):
        print(f"  阈值: CPU {a.get('cpu_threshold')}% | 内存 {a.get('memory_threshold')}% | 温度 {a.get('temperature_threshold')}℃")
        print(f"  连续确认: {a.get('confirm_rounds')} 轮 | 抑制窗口: {a.get('suppress_minutes')} 分钟")
        print(f"  接口Down告警: {'开' if a.get('check_interface_down', True) else '关'} | "
              f"配置变更告警: {'开' if a.get('check_config_change', True) else '关'}")
        print(f"  告警收件人: {', '.join(a.get('alert_to') or []) or '（同巡检邮箱）'}")
    print("="*24)

def configure_alerts(settings):
    """配置告警阈值、确认轮数、抑制窗口与告警收件人"""
    alerts = settings.setdefault('alerts', dict(DEFAULT_ALERT_SETTINGS))
    print("\n===== 告警配置（直接回车保持当前值）=====")

    def ask_int(prompt, key):
        cur = alerts.get(key)
        val = input(f"{prompt} [{cur}]: ").strip()
        if val.isdigit():
            alerts[key] = int(val)

    ask_int('CPU 告警阈值(%)', 'cpu_threshold')
    ask_int('内存 告警阈值(%)', 'memory_threshold')
    ask_int('温度 告警阈值(℃)', 'temperature_threshold')
    ask_int('连续确认轮数', 'confirm_rounds')
    ask_int('告警抑制窗口(分钟)', 'suppress_minutes')

    cur_to = ', '.join(alerts.get('alert_to') or [])
    val = input(f"告警收件人（逗号分隔，回车保持不变，输入 - 表示使用巡检邮箱）[{cur_to or '同巡检邮箱'}]: ").strip()
    if val == '-':
        alerts['alert_to'] = []
    elif val:
        alerts['alert_to'] = [x.strip() for x in val.split(',') if x.strip()]

    alerts['enabled'] = input(f"启用告警功能? (y/n, 当前{'y' if alerts.get('enabled', True) else 'n'}): ").strip().lower() != 'n'
    alerts['check_interface_down'] = input(
        f"启用接口Down告警? (y/n, 当前{'y' if alerts.get('check_interface_down', True) else 'n'}): "
    ).strip().lower() != 'n'
    alerts['check_config_change'] = input(
        f"启用配置变更告警? (y/n, 当前{'y' if alerts.get('check_config_change', True) else 'n'}): "
    ).strip().lower() != 'n'
    print("✅ 告警配置已更新")

def configure_inspection(devices, connections):
    """巡检参数配置：指定设备 / 指定邮箱 / 告警阈值"""
    while True:
        print("\n===== 巡检参数配置 =====")
        print("  1. 配置指定设备（巡检哪些设备）")
        print("  2. 配置指定邮箱（收件人列表）")
        print("  3. 配置告警阈值与抑制")
        print("  4. 查看当前配置")
        print("  0. 返回")
        choice = input("请选择: ").strip()
        if choice == '0':
            return
        if choice == '1':
            settings = load_inspect_settings()
            saved = [n for n in settings.get('targets', []) if n in devices]
            if saved:
                print(f"\n当前指定设备({len(saved)}台): {', '.join(saved)}")
                act = input("1. 重新选择  2. 取消指定(恢复启动时选择)  0. 返回: ").strip()
                if act == '1':
                    choose_inspection_targets(devices, connections, save=True)
                elif act == '2':
                    settings.pop('targets', None)
                    save_inspect_settings(settings)
                    print("✅ 已取消指定设备")
                continue
            choose_inspection_targets(devices, connections, save=True)
        elif choice == '2':
            settings = load_inspect_settings()
            configure_email(settings)
            save_inspect_settings(settings)
        elif choice == '3':
            settings = load_inspect_settings()
            configure_alerts(settings)
            save_inspect_settings(settings)
        elif choice == '4':
            show_inspection_config()
        else:
            print("❌ 无效的选择，请重新输入")

def start_inspection(devices, connections):
    """配置并启动周期巡检"""
    if inspect_state['thread'] and inspect_state['thread'].is_alive():
        print("⚠️ 巡检任务已在运行中，请先停止（菜单16→2）")
        return
    if not devices:
        print("❌ 没有可巡检的设备配置，请先添加或批量导入设备")
        return

    settings = load_inspect_settings()

    # 1) 巡检范围：优先使用已保存的“指定设备”
    saved = [n for n in settings.get('targets', []) if n in devices]
    if saved:
        print(f"\n已保存指定设备({len(saved)}台): {', '.join(saved)}")
        if input("使用该指定设备列表? (y/n，n=重新选择): ").strip().lower() != 'n':
            targets = saved
        else:
            targets = choose_inspection_targets(devices, connections, save=True)
    else:
        targets = choose_inspection_targets(devices, connections, save=True)
    if not targets:
        return

    # 2) 选择巡检周期
    print("\n巡检周期:")
    for i, (label, sec) in enumerate(INSPECT_INTERVALS, 1):
        print(f"  {i}. {label}")
    idx = input("请选择 (1-7): ").strip()
    if not idx.isdigit() or not (1 <= int(idx) <= len(INSPECT_INTERVALS)):
        print("❌ 无效选择")
        return
    interval_label, interval_sec = INSPECT_INTERVALS[int(idx) - 1]

    # 3) 输出方式配置
    has_prev = settings['email'].get('enabled') or settings['syslog'].get('enabled')
    if has_prev and input("\n沿用上次输出配置? (y/n): ").strip().lower() == 'y':
        show_inspection_config()
    else:
        settings['local']['enabled'] = input("保存到本地文件? (y/n, 默认y): ").strip().lower() != 'n'
        if input("发送至邮箱? (y/n): ").strip().lower() == 'y':
            configure_email(settings)
        else:
            settings['email']['enabled'] = False
        if input("发送至日志服务器(syslog)? (y/n): ").strip().lower() == 'y':
            settings['syslog'] = {
                'enabled': True,
                'server': input("日志服务器IP: ").strip(),
                'port': int(input("端口(默认514): ").strip() or 514),
            }
        else:
            settings['syslog']['enabled'] = False
        save_inspect_settings(settings)

    # 4) 启动后台巡检线程
    inspect_state['stop_event'] = threading.Event()
    t = threading.Thread(target=_inspect_loop,
                         args=(targets, interval_sec, settings, connections, devices),
                         daemon=True)
    t.start()
    inspect_state['thread'] = t
    inspect_state['desc'] = f"{len(targets)}台设备 / 每{interval_label}"
    print(f"\n✅ 巡检已启动: {inspect_state['desc']}（后台运行，可继续使用其他菜单）")

def stop_inspection():
    """停止巡检任务"""
    if inspect_state['stop_event']:
        inspect_state['stop_event'].set()
        inspect_state['thread'] = None
        print("✅ 巡检任务已停止")
    else:
        print("⚠️ 当前没有运行中的巡检任务")

def manage_inspection(devices, connections):
    """自动化巡检子菜单"""
    print("\n===== 自动化巡检 =====")
    print("  1. 开始巡检")
    print("  2. 停止巡检")
    print("  3. 查看巡检状态")
    print("  4. 配置指定设备 / 指定邮箱")
    print("  0. 返回")
    choice = input("请选择: ").strip()
    if choice == '1':
        start_inspection(devices, connections)
    elif choice == '2':
        stop_inspection()
    elif choice == '3':
        if inspect_state['thread'] and inspect_state['thread'].is_alive():
            print(f"✅ 巡检运行中: {inspect_state['desc']}")
        else:
            print("当前没有运行中的巡检任务")
    elif choice == '4':
        configure_inspection(devices, connections)

# 主菜单分组：(板块名, 说明, [(子项名称, 原菜单码), ...])
MENU_GROUPS = [
    ('设备管理', '添加 / 批量导入 / 删除设备', [
        ('添加单台设备 (自动检测类型)', '1'),
        ('批量导入设备 (BatchDevice.csv/xlsx)', '15'),
        ('删除设备', '12'),
    ]),
    ('连接管理', '连接 / 断开 / 已连接列表', [
        ('连接单台设备', '2'),
        ('批量连接所有设备', '3'),
        ('查看已连接设备列表', '4'),
        ('断开指定设备', '13'),
        ('断开所有设备', '14'),
    ]),
    ('设备运维', 'SSH终端 / 配置下发 / 备份 / 信息查询', [
        ('交互式SSH终端', '5'),
        ('执行配置命令', '6'),
        ('备份当前设备配置', '7'),
        ('批量备份所有设备', '8'),
        ('查看设备接口信息', '9'),
        ('查看设备基本信息', '10'),
        ('查看设备运行状态', '11'),
    ]),
]
INSPECTION_MENU_CODE = '16'

def show_main_menu(connections=None):
    """显示分组主菜单，返回对应的操作码（与原编号一致）"""
    while True:
        print("\n" + "="*60)
        print("            网络设备管理系统 v2.0")
        print("="*60)
        for i, (name, desc, _) in enumerate(MENU_GROUPS, 1):
            print(f"  {i}. {name}     {desc}")
        print(f"  {len(MENU_GROUPS) + 1}. 自动化巡检   周期性采集状态并推送报告")
        print("  0. 退出程序")
        if connections:
            names = list(connections.keys())
            preview = ', '.join(names[:3]) + ('...' if len(names) > 3 else '')
            print(f"\n  当前已连接 {len(names)} 台: {preview}")
        print("="*60)

        choice = input(f"请选择功能板块 (0-{len(MENU_GROUPS) + 1}): ").strip()
        if choice == '0':
            return '0'
        if choice == str(len(MENU_GROUPS) + 1):
            return INSPECTION_MENU_CODE
        if not choice.isdigit() or not (1 <= int(choice) <= len(MENU_GROUPS)):
            print("❌ 无效的选择，请重新输入")
            continue

        name, _, items = MENU_GROUPS[int(choice) - 1]
        while True:
            print(f"\n--- {name} ---")
            for i, (label, _) in enumerate(items, 1):
                print(f"  {i}. {label}")
            print("  0. 返回上级")
            sub = input("请选择: ").strip()
            if sub == '0':
                break
            if sub.isdigit() and 1 <= int(sub) <= len(items):
                return items[int(sub) - 1][1]
            print("❌ 无效的选择，请重新输入")

def show_device_selection(devices, title="可用设备列表"):
    """显示设备选择菜单"""
    if not devices:
        print(f"\n❌ 没有{title.replace('可用', '')}的设备")
        return None

    print("\n" + "="*60)
    print(f"{title}:")
    print("="*60)
    device_list = list(devices.keys())
    for i, name in enumerate(device_list, 1):
        info = devices[name]
        print(f"{i}. {name} - {info.get('device_type', 'unknown')} - {info['host']}")
    print("0. 返回主菜单")
    print("="*60)

    choice = input("请选择设备编号: ").strip()
    if choice == '0':
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(device_list):
            return device_list[idx]
    except ValueError:
        pass
    print("❌ 无效的选择")
    return None

def connect_single_device(device_info, device_name):
    """连接单个设备"""
    try:
        print(f"正在尝试连接设备: {device_name} ({device_info['host']})")

        connect_params = {
            **device_info,
            'timeout': 15,
            'conn_timeout': 15,
            'banner_timeout': 15,
            'auth_timeout': 10,
        }

        connection = open_connection(connect_params)
        print(f"✅ 成功连接到设备: {device_name}")
        return connection

    except NetmikoTimeoutException as e:
        print(f"❌ 连接超时 - 设备 {device_name} 无法访问")
        print(diagnose_error(e, device_info))
        return None
    except NetmikoAuthenticationException as e:
        print(f"❌ 认证失败 - 设备 {device_name} 用户名或密码错误")
        print(diagnose_error(e, device_info))
        return None
    except socket.error as e:
        print(f"❌ 网络错误: {str(e)}")
        print(diagnose_error(e, device_info))
        return None
    except Exception as e:
        print(f"❌ 未知错误: {type(e).__name__} - {str(e)}")
        print(diagnose_error(e, device_info))
        return None

def batch_connect_devices(devices):
    """批量连接所有设备"""
    connections = {}
    print(f"\n开始批量连接 {len(devices)} 个设备...")
    for name, info in devices.items():
        conn = connect_single_device(info, name)
        if conn:
            connections[name] = conn
    print(f"\n✅ 成功连接 {len(connections)}/{len(devices)} 个设备")
    return connections

def show_connected_devices(connections):
    """显示已连接设备列表"""
    if not connections:
        print("\n❌ 当前没有连接的设备")
        return
    print("\n" + "="*60)
    print("已连接设备列表:")
    print("="*60)
    for name, conn in connections.items():
        print(f"- {name}")
    print("="*60)

def execute_config(connection, device_name):
    """执行配置文件"""
    try:
        configfile = input("请输入配置文件名 (默认: config.txt): ").strip()
        if not configfile:
            configfile = "config.txt"
        if not os.path.exists(configfile):
            print(f"❌ 配置文件 {configfile} 不存在")
            return
        print(f"\n正在执行配置文件: {configfile} (设备: {device_name})")
        output = connection.send_config_from_file(configfile)
        print(f"✅ {device_name} 配置文件执行成功")
        if output:
            print("\n" + "="*50)
            print(output)
            print("="*50)
    except Exception as e:
        print(f"❌ {device_name} 配置文件执行失败: {str(e)}")

def backup_single_config(connection, device_info, device_name):
    """备份单个设备配置 - 跨平台兼容"""
    try:
        print(f"\n开始备份 {device_name} 配置...")
        hostname = connection.find_prompt().strip('[]<>#').split('-')[0]
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        backup_dir = os.path.join(get_base_dir(), "backup")
        
        # 使用跨平台目录创建函数
        ensure_dir(backup_dir)

        device_type = device_info['device_type']
        cmd = get_command(device_type, 'show_saved_config')
        if not cmd:
            cmd = 'display saved-configuration'

        config_content = connection.send_command(cmd)
        
        # 使用 os.path.join 构建跨平台路径
        filename = os.path.join(backup_dir, f"{device_name}_{hostname}_{timestamp}.txt")

        encoding = device_info.get('encoding', 'utf-8')
        with open(filename, 'w', encoding=encoding, errors='replace') as f:
            f.write(config_content)

        print(f"✅ {device_name} 备份成功，文件保存为: {filename}")
        return True
    except Exception as e:
        print(f"❌ {device_name} 备份失败: {str(e)}")
        return False

def batch_backup_configs(connections, devices):
    """批量备份所有已连接设备"""
    if not connections:
        print("❌ 没有连接的设备")
        return

    print(f"\n开始批量备份 {len(connections)} 个设备...")
    success_count = 0

    for name, conn in connections.items():
        if backup_single_config(conn, devices[name], name):
            success_count += 1

    print(f"\n✅ 批量备份完成: {success_count}/{len(connections)} 成功")

def show_interfaces(connection, device_name, device_info):
    """查看设备接口信息"""
    try:
        print(f"\n{'='*60}")
        print(f"{device_name} 接口信息")
        print(f"{'='*60}")

        device_type = device_info['device_type']
        cmd = get_command(device_type, 'show_interfaces')
        if not cmd:
            cmd = 'display ip interface brief'

        output = connection.send_command(cmd)
        print(output)
        print("="*60)
    except Exception as e:
        print(f"❌ {device_name} 获取接口信息失败: {str(e)}")

def show_device_info(connection, device_name, device_info):
    """查看设备基本信息"""
    try:
        print(f"\n{'='*60}")
        print(f"{device_name} 设备信息")
        print(f"{'='*60}")
        print(f"设备类型: {device_info['device_type']}")
        print(f"IP 地址: {device_info['host']}")
        print(f"用户名: {device_info['username']}")
        print(f"端 口: {device_info['port']}")
        if 'secret' in device_info:
            print(f"特权密码: 已设置")
        print(f"编 码: {device_info.get('encoding', 'utf-8')}")

        prompt = connection.find_prompt()
        print(f"设备提示符: {prompt}")

        hostname = prompt.strip('[]<>#').split('-')[0]
        print(f"主机名: {hostname}")
        print("="*60)
    except Exception as e:
        print(f"❌ 获取设备信息失败: {str(e)}")

def show_device_status(connection, device_name, device_info):
    """查看设备运行状态 (CPU、内存、风扇、温度、电源)"""
    device_type = device_info['device_type']
    mapping = COMMAND_MAPPING.get(device_type, {})

    status_items = [
        ('CPU 使用率', 'show_cpu'),
        ('内存使用率', 'show_memory'),
        ('风扇状态', 'show_fan'),
        ('温度状态', 'show_temperature'),
        ('电源状态', 'show_power'),
        ('设备版本', 'show_version'),
    ]

    print(f"\n{'='*60}")
    print(f"{device_name} 运行状态")
    print(f"{'='*60}")

    for title, cmd_key in status_items:
        cmd = mapping.get(cmd_key)
        if not cmd:
            print(f"\n{title}: 不支持该命令")
            continue

        print(f"\n--- {title} ---")
        try:
            output = connection.send_command(cmd, read_timeout=30)
            print(output)
        except Exception as e:
            print(f"获取失败: {str(e)}")

    print(f"\n{'='*60}")

def interactive_ssh(connection, device_name):
    """交互式SSH终端 - 允许用户手动输入命令"""
    print(f"\n{'='*60}")
    print(f"进入 {device_name} 交互式SSH终端")
    print("说明: 输入 'exit' 或 'quit' 退出交互模式")
    print("      输入 '?' 或 'help' 查看帮助")
    print(f"{'='*60}")

    try:
        # 获取设备提示符
        prompt = connection.find_prompt()
        print(f"当前提示符: {prompt}")
        
        while True:
            # 获取命令输入
            command = input(f"{prompt}").strip()
            
            # 退出条件
            if command.lower() in ['exit', 'quit', 'bye']:
                print("退出交互式终端")
                break
            
            # 帮助信息
            if command.lower() in ['?', 'help']:
                print("\n交互式终端帮助:")
                print("  exit/quit/bye - 退出交互模式")
                print("  ?/help - 显示此帮助信息")
                print("  其他命令 - 直接发送到设备执行")
                continue
            
            # 空命令跳过
            if not command:
                continue
            
            # 发送命令并获取输出
            try:
                output = connection.send_command(command, read_timeout=60)
                print(output)
            except Exception as e:
                print(f"命令执行失败: {str(e)}")
    
    except KeyboardInterrupt:
        print("\n用户中断，退出交互式终端")
    except Exception as e:
        print(f"❌ 交互式终端出错: {str(e)}")

def delete_device(devices, connections):
    """删除设备"""
    device_name = show_device_selection(devices, "选择要删除的设备")
    if not device_name:
        return devices

    if device_name in connections:
        print(f"⚠️  {device_name} 当前已连接，先断开吗? (y/n): ", end='')
        if input().strip().lower() == 'y':
            connections[device_name].disconnect()
            del connections[device_name]
            print(f"已断开 {device_name}")

    del devices[device_name]
    print(f"✅ 已删除设备: {device_name}")
    save_devices_to_file(devices)
    return devices

def disconnect_single(connection, device_name):
    """断开单个设备"""
    try:
        connection.disconnect()
        print(f"✅ 已断开设备: {device_name}")
        return True
    except Exception as e:
        print(f"❌ 断开 {device_name} 时发生错误: {str(e)}")
        return False

def disconnect_all(connections):
    """断开所有设备"""
    if not connections:
        print("没有连接的设备")
        return

    print(f"\n正在断开 {len(connections)} 个设备...")
    for name, conn in list(connections.items()):
        disconnect_single(conn, name)

def main():
    print("="*60)
    print("        网络设备管理系统 v2.0")
    print(f"        平台: {platform.system()} {platform.release()}")
    print("        特性: 自动检测设备类型 | 跨平台支持")
    print("="*60)

    devices = load_devices_from_file()
    if not devices:
        print("⚠️  没有找到设备配置，将进入添加设备流程")

    connections = {}
    current_device = None

    while True:
        choice = show_main_menu(connections)

        if choice == '1':
            device_name, device_info = add_device_interactive()
            if device_name and device_info:
                devices[device_name] = device_info
                save_devices_to_file(devices)
                conn = connect_single_device(device_info, device_name)
                if conn:
                    connections[device_name] = conn
                    current_device = device_name

        elif choice == '2':
            device_name = show_device_selection(devices)
            if device_name and device_name not in connections:
                conn = connect_single_device(devices[device_name], device_name)
                if conn:
                    connections[device_name] = conn
                    current_device = device_name
            elif device_name in connections:
                print(f"ℹ️ {device_name} 已连接")
                current_device = device_name

        elif choice == '3':
            new_connections = batch_connect_devices(devices)
            connections.update(new_connections)
            if new_connections:
                current_device = next(iter(new_connections.keys()))

        elif choice == '4':
            show_connected_devices(connections)

        elif choice == '5':
            if current_device and current_device in connections:
                interactive_ssh(connections[current_device], current_device)
            elif connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    interactive_ssh(connections[device_name], device_name)
            else:
                print("❌ 请先连接设备")

        elif choice == '6':
            if current_device and current_device in connections:
                execute_config(connections[current_device], current_device)
            elif connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    execute_config(connections[device_name], device_name)
            else:
                print("❌ 请先连接设备")

        elif choice == '7':
            if current_device and current_device in connections:
                backup_single_config(
                    connections[current_device],
                    devices[current_device],
                    current_device
                )
            elif connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    backup_single_config(
                        connections[device_name],
                        devices[device_name],
                        device_name
                    )
            else:
                print("❌ 请先连接设备")

        elif choice == '8':
            batch_backup_configs(connections, devices)

        elif choice == '9':
            if current_device and current_device in connections:
                show_interfaces(
                    connections[current_device],
                    current_device,
                    devices[current_device]
                )
            elif connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    show_interfaces(
                        connections[device_name],
                        device_name,
                        devices[device_name]
                    )
            else:
                print("❌ 请先连接设备")

        elif choice == '10':
            if current_device and current_device in connections:
                show_device_info(
                    connections[current_device],
                    current_device,
                    devices[current_device]
                )
            elif connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    show_device_info(
                        connections[device_name],
                        device_name,
                        devices[device_name]
                    )
            else:
                print("❌ 请先连接设备")

        elif choice == '11':
            if current_device and current_device in connections:
                show_device_status(
                    connections[current_device],
                    current_device,
                    devices[current_device]
                )
            elif connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    show_device_status(
                        connections[device_name],
                        device_name,
                        devices[device_name]
                    )
            else:
                print("❌ 请先连接设备")

        elif choice == '12':
            devices = delete_device(devices, connections)
            if current_device not in devices:
                current_device = next(iter(connections.keys()), None) if connections else None

        elif choice == '13':
            if connections:
                device_name = show_device_selection(
                    {k: v for k, v in devices.items() if k in connections}
                )
                if device_name:
                    if disconnect_single(connections[device_name], device_name):
                        del connections[device_name]
                        if current_device == device_name:
                            current_device = next(iter(connections.keys()), None)
            else:
                print("❌ 没有连接的设备")

        elif choice == '14':
            disconnect_all(connections)
            connections.clear()
            current_device = None

        elif choice == '15':
            imported = import_devices_from_file(devices)
            if imported:
                new_connections = batch_connect_devices(imported)
                connections.update(new_connections)
                if new_connections:
                    current_device = next(iter(new_connections.keys()))

        elif choice == '16':
            manage_inspection(devices, connections)

        elif choice == '0':
            stop_inspection()
            disconnect_all(connections)
            print("\n程序退出")
            break

        else:
            print("❌ 无效的选择，请重新输入")

if __name__ == "__main__":
    main()
