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
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime

import tkinter as tk
import tkinter.font as tkfont

import remind

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DATA_FILE = os.path.join(DATA_DIR, "todos.json")
CONF_FILE = os.path.join(DATA_DIR, "widget.json")
ALIVE_FILE = os.path.join(DATA_DIR, "widget.alive")

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
CHECK_MS = 15000      # 每 15 秒看一眼该不该提醒
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


def minutes_left(item, now):
    """还差多少分钟；没有日期或写坏了返回 None。"""
    due = remind.parse_due(item.get("due"))
    if due is None:
        return None
    return (due - now).total_seconds() / 60


def pick_rows(items, now):
    """挑出要显示的行：先放一天内的，不够再用最近要到的补齐。"""
    live = [it for it in items if not it.get("done")]
    hot, later = [], []
    for it in live:
        m = minutes_left(it, now)
        if m is None:
            continue
        if m <= 1440:
            hot.append((m, it))
        elif m <= 30 * 1440:
            later.append((m, it))
    hot.sort(key=lambda x: (x[0], str(x[1].get("title", ""))))
    later.sort(key=lambda x: (x[0], str(x[1].get("title", ""))))

    rows = hot[:MAX_ROWS]
    if len(rows) < MAX_ROWS:
        rows += later[: MAX_ROWS - len(rows)]
    return rows


def short_left(m):
    """卡片上那种短说法：'8 小时' / '47 分钟' / '3 天'，不啰嗦。"""
    if m is None:
        return ""
    late = m < 0
    a = abs(m)
    if a < 60:
        body = f"{int(round(a))} 分钟"
    elif a < 1440:
        body = f"{int(a // 60)} 小时"
    else:
        body = f"{int(a // 1440)} 天"
    return f"已过 {body}" if late else body


def tag_of(m):
    """右侧那行小字：'还有 8 小时' / '已过 15 小时' / '还剩 5 天'。"""
    if m is None:
        return "", MUTED
    if m < 0:
        return short_left(m), RED
    if m <= 1440:
        return f"还有 {short_left(m)}", ORANGE
    return f"还剩 {int(m // 1440)} 天", MUTED


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
        self.rows = []          # [(item_id, y0, y1, minutes_left)]
        self.hover_id = None
        self.drag = None        # (x_root, y_root, win_x, win_y, moved)
        self.popups = {}        # item_id -> 正在显示的那张提醒卡

        self._bind()
        self._place_window()
        self.refresh()
        self._poll()
        self._check_reminders()

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
        self._touch_alive()
        if self._stamp() != self.stamp:
            self.refresh()
        self.root.after(REFRESH_MS, self._poll)

    # -- 提醒
    def _touch_alive(self):
        """告诉 app.py「卡片还活着」，免得网页版启动时再弹一次重复的提醒。"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(ALIVE_FILE, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except OSError:
            pass

    def _check_reminders(self):
        items = load_items()
        settings = remind.load_settings()
        due = remind.due_reminders(items, settings)
        if due:
            for it, due_dt, m in due:
                remind.mark_reminded(it)
                self._remind(it, due_dt, m)
            try:
                save_items(items)
            except OSError:
                pass
            self.stamp = self._stamp()
            self.refresh()
        self.root.after(CHECK_MS, self._check_reminders)

    def _remind(self, item, due_dt, m):
        iid = item.get("id")
        old = self.popups.get(iid)
        if old is not None:
            try:
                old.top.lift()
                return
            except tk.TclError:
                self.popups.pop(iid, None)
        self.popups[iid] = Popup(self, item, due_dt, m)

    def close_popup(self, iid, snooze_minutes=None, done=False):
        p = self.popups.pop(iid, None)
        if p is not None:
            p.destroy()
        if done:
            self.toggle(iid)
            return
        if snooze_minutes:
            items = load_items()
            for it in items:
                if it.get("id") == iid:
                    remind.snooze(it, snooze_minutes)
                    break
            else:
                return
            try:
                save_items(items)
            except OSError:
                pass

    # -- 画
    def refresh(self):
        self.stamp = self._stamp()
        now = datetime.now()
        items = load_items()
        pending = [it for it in items if not it.get("done")]
        rows = pick_rows(items, now)

        n_hot = 0
        for it in pending:
            m = minutes_left(it, now)
            if m is not None and m <= 1440:
                n_hot += 1

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

        for m, it in rows:
            iid = it.get("id")
            self._row(y, m, str(it.get("title", "")), iid)
            self.rows.append((iid, y, y + ROW_H, m))
            y += ROW_H

    def _card(self, w, h):
        r = 14
        pts = [r, 1, w - r, 1, w - 1, 1, w - 1, r, w - 1, h - r, w - 1, h - 1,
               w - r, h - 1, r, h - 1, 1, h - 1, 1, h - r, 1, r]
        self.canvas.create_polygon(pts, smooth=True, fill=CARD_BG,
                                   outline=CARD_LINE, width=1)

    def _row(self, y, m, title, iid):
        c = self.canvas
        tag, color = tag_of(m)

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


# ---------------------------------------------------------------- 提醒卡片
class Popup:
    """一条提醒 = 一张浮在右下角的小卡片，点完就消失。"""

    W = 340
    H = 128

    def __init__(self, app, item, due_dt, m):
        self.app = app
        self.iid = item.get("id")
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        try:
            self.top.attributes("-alpha", 0.98)
        except tk.TclError:
            pass

        bg = CARD_BG
        transparent = False
        if not os.environ.get("TODO_WIDGET_OPAQUE"):
            try:
                self.top.configure(bg=MAGIC)
                self.top.attributes("-transparentcolor", MAGIC)
                bg, transparent = MAGIC, True
            except tk.TclError:
                transparent = False
        if not transparent:
            self.top.configure(bg=bg)

        self.c = tk.Canvas(self.top, width=self.W, height=self.H, bg=bg,
                           highlightthickness=0, bd=0)
        self.c.pack()
        self._draw(item, due_dt, m)
        self._place()
        self.top.bind("<Escape>", lambda e: self.app.close_popup(self.iid))

    def _place(self):
        # 构造时自己还没进 app.popups，所以 len() 正好是「我上面已经叠了几张」
        n = len(self.app.popups)
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        x = sw - self.W - 28
        y = sh - self.H - 110 - n * (self.H + 10)
        self.top.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.top.lift()

    def _draw(self, item, due_dt, m):
        c = self.c
        W, H = self.W, self.H
        r = 16
        c.create_polygon(
            [r, 1, W - r, 1, W - 1, 1, W - 1, r, W - 1, H - r, W - 1, H - 1,
             W - r, H - 1, r, H - 1, 1, H - 1, 1, H - r, 1, r],
            smooth=True, fill=CARD_BG, outline=CARD_LINE, width=1)

        c.create_text(16, 14, text="⏰ 该做了", anchor="nw", fill=BLUE,
                      font=self.app.f_small)
        title = self.app._fit(str(item.get("title", "")), W - 32, self.app.f_title)
        c.create_text(16, 34, text=title, anchor="nw", fill=TEXT,
                      font=self.app.f_title)
        when = due_dt.strftime("%m-%d %H:%M")
        c.create_text(16, 60, text=f"{remind.fmt_left(m)} · {when}", anchor="nw",
                      fill=RED if m < 0 else ORANGE, font=self.app.f_small)

        self._button(28, 88, 62, "完成", GREEN,
                     lambda: self.app.close_popup(self.iid, done=True))
        self._button(98, 88, 110, "稍后 10 分钟", TEXT,
                     lambda: self.app.close_popup(self.iid, snooze_minutes=10))
        self._button(216, 88, 96, "知道了", MUTED,
                     lambda: self.app.close_popup(self.iid))

    def _button(self, x, y, w, label, color, on_click):
        c = self.c
        h, r = 26, 13
        tag = f"btn{id(on_click)}"
        c.create_polygon(
            [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r,
             x + w, y + h, x + w - r, y + h, x + r, y + h, x, y + h,
             x, y + h - r, x, y + r],
            smooth=True, fill="#252b38", outline=color, width=1, tags=tag)
        c.create_text(x + w / 2, y + h / 2, text=label, fill=color,
                      font=self.app.f_small, tags=tag)
        c.tag_bind(tag, "<Button-1>", lambda e: on_click())
        c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_items([])
    # 先留个「我在」的痕迹，网页版看到就不会重复弹启动提醒
    try:
        with open(ALIVE_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass
    Widget().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
