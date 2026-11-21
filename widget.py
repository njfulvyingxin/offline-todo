#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
offline-todo 桌面卡片 —— 把临期的事直接贴在桌面上，不用打开任何东西。

它和 app.py 共用同一份 data/todos.json：
    · 卡片上点一下 → 那条就完成（写回文件）
    · 网页版里改了 → 卡片 2 秒内自己刷新

用法：
    python widget.py            正常启动
    pythonw widget.py           不弹黑框（Windows 推荐）

操作：
    左键点一行      完成 / 取消完成
    按住拖动        挪位置（位置会记住）
    右键            菜单：打开完整界面、不挡窗口、刷新、关闭
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from datetime import date

import tkinter as tk
import tkinter.font as tkfont

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DATA_FILE = os.path.join(DATA_DIR, "todos.json")
CONF_FILE = os.path.join(DATA_DIR, "widget.json")

# 卡片外观
WIDTH = 272
PAD = 14
ROW_H = 30
HEAD_H = 30
MAX_ROWS = 6

CARD_BG = "#1b1e27"
CARD_LINE = "#2d3342"
HOVER_BG = "#262c39"
TEXT = "#e9ecf3"
TEXT_DONE = "#6f7686"
MUTED = "#8b93a5"
RED = "#ff6b6b"
ORANGE = "#ffab4a"
BLUE = "#7c93ff"
GREEN = "#46c98b"

MAGIC = "#0a0b0c"  # 这个颜色会被当成透明（仅 Windows 支持）

REFRESH_MS = 2000
PORT_START, PORT_TRIES = 8765, 20


# ---------------------------------------------------------------- 数据
def load_items():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_items(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def load_conf():
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def save_conf(conf):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONF_FILE, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False)
    except OSError:
        pass


def days_left(item, today):
    """返回还差几天；没有日期或日期写坏了返回 None。"""
    due = (item.get("due") or "").strip()
    if not due:
        return None
    try:
        return (date.fromisoformat(due) - today).days
    except ValueError:
        return None


def pick_rows(items, today):
    """挑出要显示的行：先放临期（<=1 天）的，不够再用最近要到的补齐。"""
    live = [it for it in items if not it.get("done")]
    hot, later = [], []
    for it in live:
        d = days_left(it, today)
        if d is None:
            continue
        if d <= 1:
            hot.append((d, it))
        elif d <= 30:
            later.append((d, it))
    hot.sort(key=lambda x: (x[0], str(x[1].get("title", ""))))
    later.sort(key=lambda x: (x[0], str(x[1].get("title", ""))))

    rows = hot[:MAX_ROWS]
    if len(rows) < MAX_ROWS:
        rows += later[: MAX_ROWS - len(rows)]
    return rows


def tag_of(d):
    if d is None:
        return "", MUTED
    if d < 0:
        return f"过期 {-d} 天", RED
    if d == 0:
        return "今天", ORANGE
    if d == 1:
        return "明天", ORANGE
    return f"还剩 {d} 天", MUTED


# ---------------------------------------------------------------- 和网页版联动
def running_port():
    """看看网页版是不是已经开着，开着就复用它的地址。"""
    for port in range(PORT_START, PORT_START + PORT_TRIES):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/list", timeout=0.15):
                return port
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


# ---------------------------------------------------------------- 卡片
class Widget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("offline-todo · 桌面卡片")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        if not os.environ.get("TODO_WIDGET_NO_ALPHA"):
            try:
                self.root.attributes("-alpha", 0.97)
            except tk.TclError:
                pass
        self.transparent = False
        # 圆角靠「透明的魔幻色」实现，只有 Windows 认；设 TODO_WIDGET_OPAQUE=1 可关掉
        if not os.environ.get("TODO_WIDGET_OPAQUE"):
            try:
                self.root.configure(bg=MAGIC)
                self.root.attributes("-transparentcolor", MAGIC)
                self.transparent = True
            except tk.TclError:
                self.transparent = False
        if not self.transparent:
            self.root.configure(bg=CARD_BG)

        self.canvas = tk.Canvas(
            self.root, width=WIDTH, height=120,
            bg=MAGIC if self.transparent else CARD_BG,
            highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        self.f_title = tkfont.Font(family=self._family(), size=11, weight="bold")
        self.f_row = tkfont.Font(family=self._family(), size=10)
        self.f_small = tkfont.Font(family=self._family(), size=9)

        self.conf = load_conf()
        self.stamp = None
        self.rows = []          # [(item_id, y0, y1, d)]
        self.hover_id = None
        self.drag = None        # (x_root, y_root, win_x, win_y, moved)

        self._bind()
        self._place_window()
        self.refresh()
        self._poll()

    # -- 杂项
    def _family(self):
        families = set(tkfont.families())
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                     "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei"):
            if name in families:
                return name
        return "TkDefaultFont"

    def _place_window(self):
        x = self.conf.get("x")
        y = self.conf.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x = self.root.winfo_screenwidth() - WIDTH - 40
            y = 80
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _bind(self):
        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", lambda e: self.set_hover(None))
        c.bind("<Button-3>", self.on_menu)
        self.root.bind("<Escape>", lambda e: self.close())

    # -- 事件
    def on_press(self, e):
        self.drag = (e.x_root, e.y_root,
                     self.root.winfo_x(), self.root.winfo_y(), False)

    def on_drag(self, e):
        if not self.drag:
            return
        x0, y0, wx, wy, _ = self.drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if not self.drag[4] and abs(dx) + abs(dy) > 3:
            self.drag = (x0, y0, wx, wy, True)
        if self.drag[4]:
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def on_release(self, e):
        if not self.drag:
            return
        moved = self.drag[4]
        self.drag = None
        if moved:
            self.conf["x"] = self.root.winfo_x()
            self.conf["y"] = self.root.winfo_y()
            save_conf(self.conf)
            return
        hit = self.row_at(e.y)
        if hit is not None:
            self.toggle(hit)

    def on_motion(self, e):
        hit = self.row_at(e.y)
        if hit != self.hover_id:
            self.set_hover(hit)

    def row_at(self, y):
        for item_id, y0, y1, _d in self.rows:
            if y0 <= y <= y1:
                return item_id
        return None

    def set_hover(self, item_id):
        old = self.hover_id
        self.hover_id = item_id
        for iid, y0, y1, _d in self.rows:
            if iid == old or iid == item_id:
                fill = HOVER_BG if iid == item_id else ""
                self.canvas.itemconfigure(f"rowbg{iid}", fill=fill)

    def on_menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="打开完整界面", command=self.open_web)
        self.topmost_var = tk.BooleanVar(value=bool(self.root.attributes("-topmost")))
        m.add_checkbutton(label="不挡窗口", variable=self.topmost_var,
                          command=self.toggle_topmost)
        m.add_command(label="刷新", command=self.refresh)
        m.add_separator()
        m.add_command(label="关闭卡片", command=self.close)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def open_web(self):
        port = running_port()
        if port:
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        py = sys.executable
        if py.endswith("pythonw.exe"):
            py = py[: -len("pythonw.exe")] + "python.exe"
        flags = 0
        if sys.platform == "win32":
            flags = 0x00000008  # DETACHED_PROCESS，网页版自己活着，不跟着卡片退出
        try:
            subprocess.Popen([py, os.path.join(BASE, "app.py")],
                             cwd=BASE, creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def close(self):
        self.save_pos()
        self.root.destroy()

    def save_pos(self):
        self.conf["x"] = self.root.winfo_x()
        self.conf["y"] = self.root.winfo_y()
        save_conf(self.conf)

    # -- 数据
    def toggle(self, item_id):
        items = load_items()
        for it in items:
            if it.get("id") == item_id:
                it["done"] = not it.get("done")
                break
        else:
            return
        save_items(items)
        self.stamp = self._stamp()
        self.refresh()

    def _stamp(self):
        try:
            st = os.stat(DATA_FILE)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _poll(self):
        if self._stamp() != self.stamp:
            self.refresh()
        self.root.after(REFRESH_MS, self._poll)

    # -- 画
    def refresh(self):
        self.stamp = self._stamp()
        today = date.today()
        items = load_items()
        pending = [it for it in items if not it.get("done")]
        rows = pick_rows(items, today)

        n_hot = len([1 for it in pending if (days_left(it, today) or 99) <= 1])
        height = PAD + HEAD_H + max(1, len(rows)) * ROW_H + PAD
        self.canvas.configure(height=height, width=WIDTH)
        self.canvas.delete("all")
        self.rows = []

        self._card(WIDTH, height)

        # 标题行
        self.canvas.create_text(PAD, PAD + 2, text="待办", anchor="nw",
                                fill=TEXT, font=self.f_title)
        head = f"临期 {n_hot}" if n_hot else "暂无临期"
        self.canvas.create_text(WIDTH - PAD, PAD + 5, text=head, anchor="ne",
                                fill=RED if n_hot else MUTED, font=self.f_small)

        y = PAD + HEAD_H
        if not rows:
            self.canvas.create_text(PAD, y + ROW_H // 2 - 6,
                                    text="桌面清空了，去干别的吧", anchor="nw",
                                    fill=MUTED, font=self.f_row)
            self.canvas.create_text(WIDTH - PAD, y + ROW_H // 2 - 6,
                                    text=f"共 {len(pending)} 件", anchor="ne",
                                    fill=MUTED, font=self.f_small)
            return

        for d, it in rows:
            iid = it.get("id")
            self._row(y, d, str(it.get("title", "")), iid)
            self.rows.append((iid, y, y + ROW_H, d))
            y += ROW_H

    def _card(self, w, h):
        r = 14
        pts = [r, 1, w - r, 1, w - 1, 1, w - 1, r, w - 1, h - r, w - 1, h - 1,
               w - r, h - 1, r, h - 1, 1, h - 1, 1, h - r, 1, r]
        self.canvas.create_polygon(pts, smooth=True, fill=CARD_BG,
                                   outline=CARD_LINE, width=1)

    def _row(self, y, d, title, iid):
        c = self.canvas
        tag, color = tag_of(d)

        c.create_rectangle(PAD - 6, y + 3, WIDTH - PAD + 6, y + ROW_H - 3,
                           fill="", outline="", tags=f"rowbg{iid}")

        # 左边那根小色条
        c.create_rectangle(PAD - 6, y + 8, PAD - 4, y + ROW_H - 8,
                           fill=color, outline="")

        # 圆圈
        cx, cy = PAD + 4, y + ROW_H / 2
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, outline=color, width=1.4)

        # 文字，放不下就截断
        left = cx + 15
        right = WIDTH - PAD - self.f_small.measure(tag) - 12 if tag else WIDTH - PAD
        avail = max(40, right - left)
        text = self._fit(title, avail, self.f_row)
        c.create_text(left, cy, text=text, anchor="w",
                      fill=TEXT, font=self.f_row)

        if tag:
            c.create_text(WIDTH - PAD, cy, text=tag, anchor="e",
                          fill=color, font=self.f_small)

    def _fit(self, text, width, font):
        if font.measure(text) <= width:
            return text
        ell = "…"
        cut = text
        while cut and font.measure(cut + ell) > width:
            cut = cut[:-1]
        return (cut + ell) if cut else ell

    def run(self):
        self.root.mainloop()


def main():
    if not os.path.exists(DATA_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        save_items([])
    Widget().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
