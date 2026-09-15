"""
    Neuro-cli
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/neurocode-py
"""

import time
import requests
import socket
import platform
import psutil
from src.main.ui.i18n import tr


def _safe_call(callback, default):
    try:
        return callback()
    except (OSError, PermissionError, requests.RequestException):
        return default

os = platform.system()
cpu_core_count = _safe_call(lambda: psutil.cpu_count(logical=False), None)
cpu_usage = _safe_call(lambda: psutil.cpu_percent(interval=None), None)
memory = _safe_call(psutil.virtual_memory, None)
total_mem = memory.total if memory else None
used_mem = memory.used if memory else None
avaliable_mem = memory.available if memory else None
disk = _safe_call(lambda: psutil.disk_usage('/'), None)
total_disk = disk.total if disk else None
used_disk = disk.used if disk else None
avaliable_disk = disk.free if disk else None
local_nw = _safe_call(psutil.net_if_addrs, {})

def local_time():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

def ip(include_global: bool = False):
    local_address = tr("unknown")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as local_socket:
            local_socket.connect(("8.8.8.8", 80))
            local_address = local_socket.getsockname()[0]
    except OSError:
        pass

    global_address = tr("not_queried")
    if include_global:
        try:
            global_address = requests.get("https://myip.ipip.net", timeout=5).text
        except requests.RequestException:
            global_address = tr("unknown")
    return global_address, local_address

if __name__ == "__main__":
    print(local_time())
    print(ip(include_global=True))
