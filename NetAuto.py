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
from email.mime.text import MIMEText
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
# 巡检采集的命令（按 COMMAND_MAPPING 的键取各厂商对应命令）
INSPECT_ITEMS = [
    ('版本信息', 'show_version'), ('CPU占用', 'show_cpu'),
    ('内存占用', 'show_memory'), ('温度状态', 'show_temperature'),
    ('接口状态', 'show_interfaces'),
]
inspect_state = {'thread': None, 'stop_event': None, 'desc': ''}

def _inspect_settings_path():
    return os.path.join(get_base_dir(), INSPECT_SETTINGS_FILE)

def load_inspect_settings():
    """读取巡检输出配置（邮箱/日志服务器等）"""
    default = {'local': {'enabled': True}, 'email': {'enabled': False}, 'syslog': {'enabled': False}}
    if os.path.exists(_inspect_settings_path()):
        try:
            with open(_inspect_settings_path(), 'r', encoding='utf-8') as f:
                return {**default, **json.load(f)}
        except Exception:
            pass
    return default

def save_inspect_settings(settings):
    try:
        with open(_inspect_settings_path(), 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 巡检配置保存失败: {e}")

def collect_inspection_report(targets, connections, devices):
    """对目标设备执行一轮巡检，生成报告文本"""
    lines = [f"网络设备巡检报告  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "=" * 60]
    for name in targets:
        conn = connections.get(name)
        # 掉线设备尝试自动重连
        if conn is None and name in devices:
            print(f"\n[巡检] {name} 未连接，尝试自动重连...")
            conn = connect_single_device(devices[name], name)
            if conn:
                connections[name] = conn
        if conn is None:
            lines.append(f"\n[{name}] ❌ 未连接，跳过本次巡检")
            continue
        try:
            info = devices.get(name, {})
            base = base_type(info.get('device_type', 'huawei'))
            cmds = COMMAND_MAPPING.get(base, COMMAND_MAPPING['huawei'])
            lines.append(f"\n[{name}] ({info.get('host', '?')}) 类型:{base}")
            for label, key in INSPECT_ITEMS:
                cmd = cmds.get(key)
                if not cmd:
                    continue
                try:
                    output = conn.send_command(cmd, read_timeout=30)
                    lines.append(f"--- {label}: {cmd} ---\n{output.strip()}")
                except Exception as e:
                    lines.append(f"--- {label} 采集失败: {type(e).__name__} {e} ---")
        except Exception as e:
            lines.append(f"\n[{name}] ❌ 巡检异常: {type(e).__name__} - {str(e)}")
    lines.append("\n" + "=" * 60)
    return '\n'.join(lines)

def deliver_inspection_report(report, settings):
    """按配置分发巡检报告：本地文件 / 邮件 / syslog 日志服务器"""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    if settings.get('local', {}).get('enabled', True):
        try:
            reports_dir = os.path.join(get_base_dir(), 'reports')
            ensure_dir(reports_dir)
            path = os.path.join(reports_dir, f"巡检报告_{ts}.txt")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"[巡检] 报告已保存: {path}")
        except Exception as e:
            print(f"[巡检] 本地保存失败: {e}")

    email = settings.get('email', {})
    if email.get('enabled'):
        try:
            msg = MIMEText(report, 'plain', 'utf-8')
            msg['From'] = email['username']
            msg['To'] = ','.join(email['to'])
            msg['Subject'] = Header(f"网络设备巡检报告 {ts}", 'utf-8')
            port = int(email.get('port', 465))
            if port == 465:
                server = smtplib.SMTP_SSL(email['server'], port, timeout=30)
            else:
                server = smtplib.SMTP(email['server'], port, timeout=30)
                if port == 587:
                    server.starttls()
            server.login(email['username'], email['password'])
            server.sendmail(email['username'], email['to'], msg.as_string())
            server.quit()
            print(f"[巡检] 报告已发送至: {','.join(email['to'])}")
        except Exception as e:
            print(f"[巡检] 邮件发送失败: {type(e).__name__} {e}")

    syslog = settings.get('syslog', {})
    if syslog.get('enabled'):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for line in report.splitlines()[:200]:  # 单条UDP限制，截断防超长
                sock.sendto(f"<134>{line}".encode('utf-8', 'ignore'),
                            (syslog['server'], int(syslog.get('port', 514))))
            sock.close()
            print(f"[巡检] 报告已发送至日志服务器 {syslog['server']}:{syslog.get('port', 514)}")
        except Exception as e:
            print(f"[巡检] 日志服务器发送失败: {e}")

def _inspect_loop(targets, interval_sec, settings, connections, devices):
    """巡检后台线程：立即执行一轮，之后按周期执行"""
    round_no = 1
    while not inspect_state['stop_event'].is_set():
        print(f"\n[巡检] ===== 第 {round_no} 轮开始 ({datetime.now().strftime('%H:%M:%S')}) =====")
        report = collect_inspection_report(targets, connections, devices)
        deliver_inspection_report(report, settings)
        print(f"[巡检] 第 {round_no} 轮完成，{interval_sec // 60} 分钟后进行下一轮")
        round_no += 1
        if inspect_state['stop_event'].wait(interval_sec):
            break

def start_inspection(devices, connections):
    """配置并启动周期巡检"""
    if inspect_state['thread'] and inspect_state['thread'].is_alive():
        print("⚠️ 巡检任务已在运行中，请先停止（菜单16→2）")
        return
    if not connections:
        print("❌ 当前没有已连接的设备，请先通过菜单 2/3/15 连接设备")
        return

    # 1) 选择巡检范围
    names = list(connections.keys())
    print("\n已连接设备:")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}")
    sel = input("巡检范围 - 输入 a 巡检全部，或编号逗号分隔(如 1,3): ").strip().lower()
    if sel == 'a' or not sel:
        targets = names
    else:
        targets = [names[int(x) - 1] for x in sel.split(',')
                   if x.strip().isdigit() and 1 <= int(x) <= len(names)]
    if not targets:
        print("❌ 未选择任何设备")
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
    settings = load_inspect_settings()
    has_prev = settings['email'].get('enabled') or settings['syslog'].get('enabled')
    if not (has_prev and input("沿用上次输出配置? (y/n): ").strip().lower() == 'y'):
        settings['local']['enabled'] = input("保存到本地文件? (y/n, 默认y): ").strip().lower() != 'n'
        if input("发送至邮箱? (y/n): ").strip().lower() == 'y':
            print("--- 邮箱配置（以QQ邮箱为例: smtp.qq.com 端口465，密码填授权码）---")
            settings['email'] = {
                'enabled': True,
                'server': input("SMTP服务器: ").strip(),
                'port': int(input("端口(465/587/25, 默认465): ").strip() or 465),
                'username': input("发件邮箱: ").strip(),
                'password': input("邮箱密码/授权码: ").strip(),
                'to': [x.strip() for x in input("收件邮箱(多个用逗号分隔): ").split(',') if x.strip()],
            }
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

def show_main_menu():
    """显示主菜单"""
    print("\n" + "="*60)
    print("            网络设备管理系统")
    print("="*60)
    print("            1. 添加单台设备 (自动检测类型)")
    print("            2. 选择设备并连接")
    print("            3. 批量连接所有设备")
    print("            4. 查看已连接设备列表")
    print("            5. 交互式SSH终端")
    print("            6. 执行配置命令")
    print("            7. 备份当前设备配置")
    print("            8. 批量备份所有设备")
    print("            9. 查看设备接口信息")
    print("            10. 查看设备基本信息")
    print("            11. 查看设备运行状态")
    print("            12. 删除设备")
    print("            13. 断开指定设备")
    print("            14. 断开所有设备")
    print("            15. 批量导入设备 (BatchDevice.csv/xlsx)")
    print("            16. 自动化巡检 (周期采集/邮件/日志服务器)")
    print("            0. 退出程序")
    print("="*60)
    choice = input("请选择你要执行的操作 (0-16): ").strip()
    return choice

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
        choice = show_main_menu()

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
