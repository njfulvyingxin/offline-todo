#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
offline-todo —— 一个只在你电脑上跑的待办清单。

没有账号、没有云端、不联网。数据就是一个 JSON 文件，想备份就复制它。
启动后会在浏览器里开一个页面当界面，关掉终端窗口就退出。

用法：
    python app.py            默认从 8765 端口开始找空位
    python app.py --port 9000
"""

import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DATA_FILE = os.path.join(DATA_DIR, "todos.json")
UI_FILE = os.path.join(BASE, "ui.html")
LOCK = threading.Lock()

DEFAULT_PORT = 8765
PORT_TRIES = 20


# ---------------------------------------------------------------- 数据
def load():
    """读清单。文件不存在或坏了都返回空列表，不让程序崩在启动这一步。"""
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(items):
    """先写临时文件再替换。中途断电也不会把原文件写坏。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def next_id(items):
    return max([int(it.get("id", 0)) for it in items] + [0]) + 1


# ---------------------------------------------------------------- 网页
class Handler(BaseHTTPRequestHandler):
    server_version = "offline-todo/1.0"

    def log_message(self, *args):
        pass  # 默认实现会往终端刷日志，这里安静点

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(UI_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, "找不到 ui.html".encode("utf-8"), "text/plain; charset=utf-8")
        elif self.path == "/api/list":
            self._json({"items": load(), "today": date.today().isoformat()})
        elif self.path == "/favicon.ico":
            self._send(204)
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return self._json({"ok": False, "error": "请求不是合法的 JSON"}, 400)

        if self.path == "/quit":
            self._json({"ok": True})

            def bye():
                self.server.shutdown()
                self.server.server_close()
                # shutdown() 只停循环，主线程还在等，所以直接结束进程
                os._exit(0)

            threading.Thread(target=bye, daemon=True).start()
            return

        with LOCK:
            items = load()

            if self.path == "/api/add":
                title = (data.get("title") or "").strip()
                if not title:
                    return self._json({"ok": False, "error": "内容不能空着"}, 400)
                items.append({
                    "id": next_id(items),
                    "title": title,
                    "due": (data.get("due") or "").strip(),
                    "done": False,
                    "created": date.today().isoformat(),
                })

            elif self.path == "/api/toggle":
                for it in items:
                    if it.get("id") == data.get("id"):
                        it["done"] = not it.get("done")
                        break
                else:
                    return self._json({"ok": False, "error": "找不到这一条"}, 404)

            elif self.path == "/api/delete":
                before = len(items)
                items = [it for it in items if it.get("id") != data.get("id")]
                if len(items) == before:
                    return self._json({"ok": False, "error": "找不到这一条"}, 404)

            elif self.path == "/api/edit":
                title = (data.get("title") or "").strip()
                if not title:
                    return self._json({"ok": False, "error": "内容不能空着"}, 400)
                for it in items:
                    if it.get("id") == data.get("id"):
                        it["title"] = title
                        it["due"] = (data.get("due") or "").strip()
                        break
                else:
                    return self._json({"ok": False, "error": "找不到这一条"}, 404)

            else:
                return self._json({"ok": False, "error": "没有这个接口"}, 404)

            save(items)
            return self._json({"ok": True})


# ---------------------------------------------------------------- 提醒
def due_soon():
    """48 小时内到期 + 已过期，按紧急程度排好。"""
    today = date.today()
    hot = []
    for it in load():
        if it.get("done") or not it.get("due"):
            continue
        try:
            d = (date.fromisoformat(it["due"]) - today).days
        except ValueError:
            continue
        if d <= 2:
            hot.append((d, it["title"]))
    hot.sort()
    return hot


def native_popup(lines):
    """只有 Windows 才弹原生框；其它系统交给页面顶部的提醒条。"""
    if sys.platform != "win32" or not lines:
        return
    try:
        import ctypes
        txt = "\n".join(lines)
        ctypes.windll.user32.MessageBoxW(None, txt, "offline-todo", 0x40 | 0x1000)
    except Exception:
        pass


def popup_remind():
    hot = due_soon()
    if not hot:
        return
    lines = []
    for d, title in hot[:8]:
        tag = f"过期 {abs(d)} 天" if d < 0 else ("今天到期" if d == 0 else ("明天到期" if d == 1 else f"还剩 {d} 天"))
        lines.append(f"· {tag}   {title}")
    if len(hot) > 8:
        lines.append(f"…… 还有 {len(hot) - 8} 件")
    native_popup(lines)


# ---------------------------------------------------------------- 启动
def pick_port(preferred):
    for port in range(preferred, preferred + PORT_TRIES):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"{preferred}~{preferred + PORT_TRIES} 之间没有空端口，用 --port 换一个")


def main():
    ap = argparse.ArgumentParser(description="offline-todo 本地待办")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8765")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    if not os.path.exists(UI_FILE):
        print("缺少 ui.html，它应该和 app.py 放在同一目录。")
        return 1

    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    print("=" * 48, flush=True)
    print("  offline-todo 已启动", flush=True)
    print(f"  {url}", flush=True)
    print("  关掉这个窗口就退出（页面底部也有「关掉」）", flush=True)
    print("=" * 48, flush=True)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    threading.Thread(target=popup_remind, daemon=True).start()

    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
