from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
from datetime import datetime
import time
import socket
import os
import json
import sys
import io
import platform

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
    """根据设备类型获取对应命令"""
    mapping = COMMAND_MAPPING.get(device_type, COMMAND_MAPPING['huawei'])
    return mapping.get(command_key, '')

def detect_device_type(ip, username, password, port=22, timeout=10):
    """自动检测设备类型

    尝试用不同的 device_type 连接设备，返回检测到的类型和提示符

    返回: (device_type, prompt) 或 (None, None)
    """
    print(f"\n{'='*60}")
    print(f"🔍 正在探测设备类型: {ip}")
    print(f"{'='*60}")

    for device_type in SUPPORTED_DEVICE_TYPES:
        print(f"尝试 {device_type} ...", end=' ', flush=True)

        device_params = {
            'device_type': device_type,
            'host': ip,
            'username': username,
            'password': password,
            'port': port,
            'timeout': timeout,
            'banner_timeout': timeout,
            'auth_timeout': timeout,
        }

        try:
            conn = ConnectHandler(**device_params)
            prompt = conn.find_prompt()
            conn.disconnect()

            vendor_info = DEVICE_TYPE_INFO.get(device_type, {})
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
        'encoding': DEVICE_TYPE_INFO[device_type]['encoding'],
    }

    if secret:
        device_info['secret'] = secret

    if DEVICE_TYPE_INFO[device_type]['has_secret'] and not secret:
        print(f"\n⚠️  提示: {DEVICE_TYPE_INFO[device_type]['vendor']} 设备通常需要特权密码")
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

def load_devices_from_file(filename='devices.json'):
    """从 JSON 文件加载设备配置"""
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

def save_devices_to_file(devices, filename='devices.json'):
    """保存设备配置到 JSON 文件"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(devices, f, indent=4, ensure_ascii=False)
        print(f"✅ 设备配置已保存到 {filename}")
        return True
    except Exception as e:
        print(f"❌ 保存设备配置失败: {str(e)}")
        return False

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
    print("            0. 退出程序")
    print("="*60)
    choice = input("请选择你要执行的操作 (0-14): ").strip()
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
            'banner_timeout': 15,
            'auth_timeout': 10,
        }

        connection = ConnectHandler(**connect_params)
        print(f"✅ 成功连接到设备: {device_name}")
        return connection

    except NetmikoTimeoutException:
        print(f"❌ 连接超时 - 设备 {device_name} 无法访问")
        return None
    except NetmikoAuthenticationException:
        print(f"❌ 认证失败 - 设备 {device_name} 用户名或密码错误")
        return None
    except socket.error as e:
        print(f"❌ 网络错误: {str(e)}")
        return None
    except Exception as e:
        print(f"❌ 未知错误: {type(e).__name__} - {str(e)}")
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
        backup_dir = "backup"
        
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

        elif choice == '0':
            disconnect_all(connections)
            print("\n程序退出")
            break

        else:
            print("❌ 无效的选择，请重新输入")

if __name__ == "__main__":
    main()
