#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提醒规则 —— app.py（网页版）和 widget.py（桌面卡片）共用的那一份脑子。

分两件事：
    1. 把 due 字符串解析成具体时刻（只写了日期就按当天 23:59 算）
    2. 算出「现在该弹哪几条」，包括冷却和「最多催几次」

设置存在 data/settings.json，改它不用重启别的进程。
"""

import json
import os
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

# 没设置过就是用这套
DEFAULTS = {
    "enabled": True,        # 总开关
    "default_minutes": 120,  # 默认提前 2 小时
    "repeat_minutes": 30,    # 没点完成，半小时后再催
    "max_repeats": 3,        # 一条最多催 3 次
}

# 一条待办最多被催几天（过期很久的就不再提了，不然天天烦）
OVERDUE_GRACE_DAYS = 3

DUE_FMT = "%Y-%m-%d %H:%M"


# ---------------------------------------------------------------- 时间
def parse_due(s):
    """'2026-11-25' → 当天 23:59；'2026-11-25 14:00' 也认。解析不了返回 None。"""
    s = (s or "").strip().replace("T", " ")
    if not s:
        return None
    for fmt in (DUE_FMT, "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            dt = dt.replace(hour=23, minute=59)
        return dt
    return None


def fmt_left(delta_minutes):
    """还剩多久，说人话：'1 小时 55 分' / '12 分钟' / '已过 3 小时'。"""
    m = int(round(delta_minutes))
    late = m < 0
    m = abs(m)
    if m >= 1440:
        body = f"{m // 1440} 天"
        if m % 1440:
            body += f" {m % 1440 // 60} 小时"
    elif m >= 60:
        body = f"{m // 60} 小时"
        if m % 60:
            body += f" {m % 60} 分"
    else:
        body = f"{m} 分钟"
    return f"已过 {body}" if late else f"还有 {body}"


# ---------------------------------------------------------------- 设置
def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return s
    if not isinstance(raw, dict):
        return s
    if isinstance(raw.get("enabled"), bool):
        s["enabled"] = raw["enabled"]
    for k in ("default_minutes", "repeat_minutes", "max_repeats"):
        try:
            v = int(raw[k])
        except (KeyError, TypeError, ValueError):
            continue
        if v >= 0:
            s[k] = v
    return s


def save_settings(settings):
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)
    return merged


def minutes_for(item, settings=None):
    """这一条提前多少分钟提醒。0 = 这条不提醒；没单独设就用全局默认。"""
    settings = settings or load_settings()
    v = item.get("remind_before")
    if v is None or v == "":
        return int(settings.get("default_minutes") or 0)
    try:
        v = int(v)
    except (TypeError, ValueError):
        return int(settings.get("default_minutes") or 0)
    return max(0, v)


# ---------------------------------------------------------------- 该弹谁
def due_reminders(items, settings=None, now=None):
    """返回现在该提醒的 [(item, due_datetime, 剩余分钟)]，按紧急程度排好。"""
    settings = settings or load_settings()
    if not settings.get("enabled", True):
        return []
    now = now or datetime.now()
    repeat = max(1, int(settings.get("repeat_minutes") or 30))
    max_times = max(1, int(settings.get("max_repeats") or 3))

    out = []
    for it in items:
        if it.get("done"):
            continue
        due = parse_due(it.get("due"))
        if due is None:
            continue
        window = minutes_for(it, settings)
        if window <= 0:                      # 这条设了「不提醒」
            continue
        if now < due - timedelta(minutes=window):
            continue                          # 还没进窗口
        if now > due + timedelta(days=OVERDUE_GRACE_DAYS):
            continue                          # 过期太久了，别再念

        # 「稍后 10 分钟」按下的冷却
        snooze = it.get("snooze_until")
        if snooze:
            try:
                if now < datetime.fromtimestamp(float(snooze)):
                    continue
            except (TypeError, ValueError, OSError):
                pass

        # 已经催过的：等 repeat 分钟，并且别超过次数上限
        count = 0
        try:
            count = int(it.get("remind_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count >= max_times:
            continue
        last = it.get("remind_at")
        if last:
            try:
                if now - datetime.fromtimestamp(float(last)) < timedelta(minutes=repeat):
                    continue
            except (TypeError, ValueError, OSError):
                pass

        out.append((it, due, (due - now).total_seconds() / 60))

    out.sort(key=lambda x: x[2])
    return out


def mark_reminded(item, now=None):
    """记一笔：这条已经催过了。返回改过的 item（调用方负责落盘）。"""
    now = now or datetime.now()
    try:
        item["remind_count"] = int(item.get("remind_count") or 0) + 1
    except (TypeError, ValueError):
        item["remind_count"] = 1
    item["remind_at"] = now.timestamp()
    item.pop("snooze_until", None)
    return item


def snooze(item, minutes=10, now=None):
    now = now or datetime.now()
    item["snooze_until"] = (now + timedelta(minutes=minutes)).timestamp()
    return item


def reset_clock(item):
    """改了截止时间就从头开始算，不然新的时间点永远不提醒了。"""
    item.pop("remind_at", None)
    item.pop("remind_count", None)
    item.pop("snooze_until", None)
