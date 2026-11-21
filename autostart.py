#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
offline-todo 开机自启 —— 装上去 / 撤下来，两条命令的事。

    python autostart.py --install     开机后桌面卡片自己出来
    python autostart.py --status      看现在装了没、装在哪
    python autostart.py --uninstall   撤掉（不会动你的待办数据）

它做的事很小：往系统的「登录时自动运行」目录里放一个几行的启动脚本。
Windows 是 %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup
Linux 是 ~/.config/autostart
macOS 没做（要开机自启得自己加 LaunchAgent，这个项目暂时不碰）。

想撤掉，`--uninstall` 或者直接去那个目录把 offline-todo-widget 那个文件删了都行。
"""

import argparse
import os
import sys

NAME = "offline-todo-widget"


def here(*parts):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def pythonw():
    """优先用 pythonw，Windows 上它不会带一个黑框出来。"""
    exe = sys.executable or "python3"
    if exe.lower().endswith("python.exe"):
        cand = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def startup_path():
    """返回这个系统里「登录时自动运行」那个文件应该放哪。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        folder = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        return os.path.join(folder, NAME + ".cmd")
    if sys.platform == "darwin":
        return None
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", NAME + ".desktop")


def render():
    widget = here("widget.py")
    if sys.platform == "win32":
        body = (
            "@echo off\r\n"
            "rem offline-todo widget autostart -- delete this file to disable\r\n"
            'start "" "%s" "%s"\r\n' % (pythonw(), widget)
        )
        return body.encode("ascii", "replace")
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=offline-todo widget\n"
        "Comment=Desktop todo card\n"
        "Exec=%s %s\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n" % (pythonw(), widget)
    ).encode("utf-8")


def install():
    path = startup_path()
    if path is None:
        print("macOS 上这个脚本还没做，先手动开吧：python3 widget.py")
        return 1
    if not os.path.exists(here("widget.py")):
        print("找不到 widget.py，它得和 autostart.py 放在同一目录。")
        return 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(render())
    except OSError as e:
        print(f"写不进去：{path}\n{e}")
        return 1
    print("装好了，下次登录桌面卡片会自己出来。")
    print("文件在：" + path)
    return 0


def uninstall():
    path = startup_path()
    if path is None:
        print("macOS 上没有装过。")
        return 0
    if not os.path.exists(path):
        print("本来就没装：" + path)
        return 0
    try:
        os.remove(path)
    except OSError as e:
        print(f"删不掉：{path}\n{e}")
        return 1
    print("撤掉了：" + path)
    return 0


def status():
    path = startup_path()
    if path is None:
        print("macOS：不支持")
        return 0
    if os.path.exists(path):
        print("已装：" + path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f.read().splitlines()[:6]:
                    print("    " + line)
        except OSError:
            pass
    else:
        print("没装。想装就：python autostart.py --install")
    return 0


def main():
    ap = argparse.ArgumentParser(description="offline-todo 桌面卡片开机自启")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="装到登录自启目录")
    g.add_argument("--uninstall", action="store_true", help="从登录自启目录撤掉")
    g.add_argument("--status", action="store_true", help="看现在装了没")
    args = ap.parse_args()
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    return status()


if __name__ == "__main__":
    sys.exit(main())
