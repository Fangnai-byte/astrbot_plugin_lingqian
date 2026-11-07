# -*- coding: utf-8 -*-
"""灵签：摇一支签，出签条图，附签诗和一句白话。"""
import asyncio
import json
import os
import random
import time
from datetime import datetime

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .render import render_stick

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(PLUGIN_DIR, "data.json")

SHAKE_LINES = (
    "摇了三下，一支跳出来。",
    "签筒晃了晃，落出一支。",
    "摇到第三下，这支自己冒了头。",
    "匣子里一阵响，出来的就是它。",
)


def _load() -> list:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def items_line(item: dict) -> str:
    """分项一行：功名 · 财禄 · 婚姻 · 疾病 · 出行。"""
    items = item.get("items") or {}
    if not items:
        return ""
    return "　".join(f"{k} {v}" for k, v in items.items())


def grade_of(item: dict) -> str:
    return item.get("grade") or "中平"


class Store:
    """抽过的签记下来，避免连着重复。"""

    def __init__(self, path: str):
        self.path = path

    def read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception:
            pass


@register("astrbot_plugin_lingqian", "Fangnai-byte",
          "六十甲子灵签：摇一支签，出签条图，附签诗与一段白话", "0.1.0")
class LingQianPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.data = _load()
        self.store = Store(os.path.join(PLUGIN_DIR, "history.json"))
        self._last_draw: dict = {}

    # ---------- 配置 ----------
    def _cfg(self, key: str, default):
        return self.config.get(key, default)

    def _as_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _temp_dir(self) -> str:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            base = os.path.join(get_astrbot_data_path(), "temp")
        except Exception:
            base = os.path.join(PLUGIN_DIR, ".tmp")
        os.makedirs(base, exist_ok=True)
        return base

    # ---------- 抽签 ----------
    @staticmethod
    def _rec(entry) -> dict:
        """兼容老的「一串签号」写法。"""
        if isinstance(entry, list):
            return {"nums": [n for n in entry if isinstance(n, int)], "count": len(entry), "last": 0}
        if isinstance(entry, dict):
            nums = [n for n in (entry.get("nums") or []) if isinstance(n, int)]
            return {"nums": nums, "count": int(entry.get("count") or len(nums)), "last": entry.get("last") or 0}
        return {"nums": [], "count": 0, "last": 0}

    def draw(self, uid: str, gid: str = "") -> dict:
        hist = self.store.read()
        rec = self._rec(hist.get(uid))
        recent = rec["nums"][-12:]
        pool = [c for c in self.data if c["num"] not in recent]
        if not pool:
            pool = self.data
        item = random.choice(pool)
        rec["nums"] = (rec["nums"] + [item["num"]])[-60:]
        rec["count"] += 1
        rec["last"] = item["num"]
        hist[uid] = rec

        # 群统计：按群记每个人抽了几次
        if gid:
            groups = hist.get("_groups")
            if not isinstance(groups, dict):
                groups = {}
            bucket = groups.get(gid)
            if not isinstance(bucket, dict):
                bucket = {}
            bucket[uid] = int(bucket.get(uid) or 0) + 1
            groups[gid] = bucket
            hist["_groups"] = groups

        self.store.write(hist)
        return item

    @staticmethod
    def parse_num(text: str) -> int:
        """认「第26」「26」「第 26 签」这几种写法，其余当问题。"""
        t = (text or "").strip()
        if not t:
            return 0
        t = t.replace("第", "").replace("签", "").strip()
        if not t.isdigit():
            return 0
        n = int(t)
        return n if 1 <= n <= 60 else 0

    def view_text(self, item: dict) -> str:
        poem = "\n".join(item["poem"])
        parts = [f"第 {item['num']} 签 · {item['title']}（{grade_of(item)}）",
                 item.get("meta", ""), "", poem, "",
                 item.get("jie", ""), "", items_line(item)]
        if item.get("story"):
            parts.append("")
            parts.append("卦头：" + item["story"])
        return "\n".join(p for p in parts if p is not None)

    def cooldown_left(self, uid: str) -> int:
        """还要等几秒，0 表示可以抽。"""
        span = self._as_int("cooldown_seconds", 60)
        if span <= 0:
            return 0
        last = self._last_draw.get(uid) or 0
        left = int(span - (time.time() - last))
        return max(0, left)

    def group_stats_text(self, gid: str) -> str:
        groups = self.store.read().get("_groups")
        bucket = (groups or {}).get(gid) if isinstance(groups, dict) else None
        if not isinstance(bucket, dict) or not bucket:
            return "这个群还没人抽过。"
        total = sum(int(v) for v in bucket.values())
        top = sorted(bucket.items(), key=lambda kv: -int(kv[1]))[:3]
        lines = [f"本群一共抽了 {total} 次，{len(bucket)} 个人抽过。"]
        lines.append("抽得最多：" + "、".join(f"××{uid[-4:]} {n} 次" for uid, n in top))
        return "\n".join(lines)

    def stats_of(self, uid: str) -> dict:
        rec = self._rec(self.store.read().get(uid))
        nums = rec["nums"]
        by_num = {c["num"]: c for c in self.data}
        grades = {}
        for n in nums:
            g = grade_of(by_num[n]) if n in by_num else "中平"
            grades[g] = grades.get(g, 0) + 1
        return {
            "count": rec["count"],
            "distinct": len(set(nums)),
            "grades": grades,
            "last": by_num.get(rec["last"]) if rec["last"] else None,
        }

    @staticmethod
    def stats_text(st: dict) -> str:
        order = ["上上", "上吉", "中吉", "中平", "中下", "下下"]
        parts = [f"{g}{st['grades'][g]}" for g in order if st["grades"].get(g)]
        lines = [f"抽过 {st['count']} 次，碰到 {st['distinct']} 支不同的签"]
        if parts:
            lines.append("　".join(parts))
        if st["last"]:
            it = st["last"]
            lines.append(f"最近一支：第 {it['num']} 签 · {it['title']}（{grade_of(it)}）")
        return "\n".join(lines)

    @staticmethod
    def reading(item: dict) -> str:
        poem = "，".join(item["poem"][:2]) + "。"
        head = f"第 {item['num']} 签 · {item['title']}（{grade_of(item)}）"
        tail = items_line(item)
        return head + "\n" + poem + "\n" + item.get("jie", "") + (("\n\n" + tail) if tail else "")

    def plain(self, item: dict) -> str:
        """配置关掉图片时的纯文字版。"""
        poem = "\n".join(item["poem"])
        head = f"第 {item['num']} 签 · {item['title']}（{grade_of(item)}）"
        tail = items_line(item)
        return head + "\n" + poem + "\n\n" + item.get("jie", "") + (("\n\n" + tail) if tail else "")

    # ---------- 命令 ----------
    @filter.command("灵签", alias={"抽签", "求签"})
    async def cmd_lingqian(self, event: AstrMessageEvent, question: str = ""):
        uid = str(event.get_sender_id())
        gid = str(event.get_group_id() or "")
        if question.strip() in ("群统计", "本群统计"):
            yield event.plain_result(self.group_stats_text(gid))
            return

        want = self.parse_num(question)
        if want:
            item = next((c for c in self.data if c["num"] == want), None)
            if item is None:
                yield event.plain_result(f"没有第 {want} 签，一共 60 支。")
                return
            yield event.plain_result(self.view_text(item))
            return

        if question.strip() in ("统计", "记录", "抽过"):
            yield event.plain_result(self.stats_text(self.stats_of(uid)))
            return
        wait = self.cooldown_left(uid)
        if wait > 0:
            yield event.plain_result(f"刚抽过，{wait} 秒后再来。")
            return

        item = self.draw(uid, str(event.get_group_id() or ""))
        self._last_draw[uid] = time.time()
        question = (question or "").strip()[:24]

        if self._cfg("shake_enabled", True):
            yield event.plain_result(random.choice(SHAKE_LINES))
            await asyncio.sleep(float(self._as_int("shake_delay", 2)))

        if not self._cfg("image_enabled", True):
            yield event.plain_result(self.plain(item))
            return

        path = os.path.join(self._temp_dir(), f"lingqian_{uid}_{int(time.time())}.jpg")
        try:
            await asyncio.to_thread(
                render_stick, item, path,
                self._as_int("image_width", 760), question,
                datetime.now().strftime("%Y-%m-%d"),
            )
        except Exception:
            yield event.plain_result(self.plain(item))
            return

        try:
            yield event.image_result(path)
        finally:
            pass
        yield event.plain_result(self.reading(item))
