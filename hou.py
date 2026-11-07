# -*- coding: utf-8 -*-
"""按日期算节气，再落到第几候。用太阳视黄经推，误差在分钟级。"""
import datetime
import json
import math
import os

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
HOU_PATH = os.path.join(PLUGIN_DIR, "hou.json")

# 节气名，从立春起，太阳黄经依次 315,330,345,0,15,...
JIE_NAMES = [
    "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
    "立夏", "小满", "芒种", "夏至", "小暑", "大暑",
    "立秋", "处暑", "白露", "秋分", "寒露", "霜降",
    "立冬", "小雪", "大雪", "冬至", "小寒", "大寒",
]


def _sun_longitude(dt: datetime.datetime) -> float:
    """太阳视黄经（度）。低精度公式，够定节气。"""
    # 儒略日
    y, m = dt.year, dt.month
    day = dt.day + (dt.hour + dt.minute / 60) / 24
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + day + b - 1524.5

    t = (jd - 2451545.0) / 36525.0
    # 平黄经
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    # 平近点角
    m_anom = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    mr = math.radians(m_anom)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(mr)
         + (0.019993 - 0.000101 * t) * math.sin(2 * mr)
         + 0.000289 * math.sin(3 * mr))
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    lam = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    return lam % 360


def _jie_start(year: int, idx: int) -> datetime.datetime:
    """第 idx 个节气（0=立春）在 year 年的交节时刻（按北京时间的近似）。"""
    target = (315 + 15 * idx) % 360
    # 立春在 2 月初，往外推着找
    guess = datetime.datetime(year, 1, 1) + datetime.timedelta(days=idx * 15.2 + 30)
    lo = guess - datetime.timedelta(days=20)
    hi = guess + datetime.timedelta(days=20)

    def diff(dt):
        return ((_sun_longitude(dt) - target + 180) % 360) - 180

    for _ in range(40):
        mid = lo + (hi - lo) / 2
        if diff(mid) < 0:
            lo = mid
        else:
            hi = mid
    return lo + (hi - lo) / 2


def current_hou(now: datetime.datetime | None = None) -> dict:
    """现在落在第几候。返回 {idx, jie, part, name, plain, jie_start, next_in_days}。"""
    if now is None:
        now = datetime.datetime.now()
    year = now.year

    # 找最近一个已经过去的交节时刻
    best = None
    for y in (year - 1, year):
        for idx in range(24):
            start = _jie_start(y, idx)
            if start <= now and (best is None or start > best[0]):
                best = (start, idx)
    if best is None:
        return {}

    start, idx = best
    jie_start, jie_idx = start, idx

    # 候的编号：该节气里第 0/1/2 个五天
    part = int((now - jie_start).total_seconds() // (5 * 86400))
    part = max(0, min(2, part))
    # 七十二候数组按立春起算
    order = (jie_idx) % 24
    hou_idx = order * 3 + part + 1

    data = json.loads(open(HOU_PATH, encoding="utf-8").read())
    item = next((h for h in data if h["idx"] == hou_idx), None)
    if not item:
        return {}
    item = dict(item)
    item["jie_start"] = jie_start.strftime("%m-%d %H:%M")
    item["days_in"] = int((now - jie_start).total_seconds() // 86400)
    nxt = _jie_start(year if jie_idx < 23 else year + 1, (jie_idx + 1) % 24)
    item["next_jie"] = JIE_NAMES[(jie_idx + 1) % 24]
    item["next_in_days"] = (nxt - now).days
    return item


if __name__ == "__main__":
    import sys
    when = datetime.datetime.now()
    if len(sys.argv) > 1:
        when = datetime.datetime.fromisoformat(sys.argv[1])
    print(when, "→", current_hou(when))
