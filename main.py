# -*- coding: utf-8 -*-
"""灵签：摇一支签，出签条图，附签诗和一句白话。"""
import asyncio
import json
import os
import random
import re
import time
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.components import Image
from astrbot.api.star import Context, Star, register

from .hou import current_hou
from .render import render_stick

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(PLUGIN_DIR, "data.json")

SHAKE_LINES = (
    "摇了三下，一支跳出来。",
    "签筒晃了晃，落出一支。",
    "摇到第三下，这支自己冒了头。",
    "筒里沙沙一阵，一支竹签顶到最上头。",
    "手一停，它就歪在筒口，不往下掉了。",
    "摇了半天，最后落在一支上。",
    "筒口一亮，半截签身探在外头。",
    "摇到手臂发酸，它才慢慢浮上来。",
    "哗啦一阵，一支卡在筒沿上。",
    "刚收手，一支顺势倒进掌心。",
)


def _load() -> list:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


KIND_WORDS = (
    ("功名", ("工作", "事业", "上班", "面试", "考试", "升职", "跳槽", "辞职", "学业", "考研", "老板", "公司", "offer")),
    ("财禄", ("钱", "财运", "收入", "生意", "投资", "股票", "买卖", "回本", "赚", "亏", "欠", "债")),
    ("婚姻", ("感情", "喜欢", "复合", "结婚", "对象", "恋爱", "分手", "告白", "追", "他", "她", "缘")),
    ("疾病", ("身体", "病", "健康", "难受", "失眠", "医", "恢复")),
    ("出行", ("出行", "搬家", "出差", "旅游", "回老家", "出门", "远行", "去不去", "路上")),
)

KIND_HINT = {
    "功名": "事业和学业",
    "财禄": "钱",
    "婚姻": "感情",
    "疾病": "身体",
    "出行": "走动",
}


def question_kind(question: str) -> str:
    """按关键词猜问的是哪一类，猜不到返回空串。"""
    q = question or ""
    for kind, words in KIND_WORDS:
        if any(w in q for w in words):
            return kind
    return ""


def answer_line(item: dict, question: str) -> str:
    """直接把签里跟问题对应的那项说出来，别让人自己去分项里找。"""
    if not question:
        return ""
    kind = question_kind(question)
    items = item.get("items") or {}
    if kind and kind in items:
        return f"你问的是{question}，{KIND_HINT.get(kind, kind)}上，给的是{items[kind]}"
    grade = grade_of(item)
    if grade in ("上上", "上吉"):
        return f"你问的这件事，签是{grade}，可以往前推"
    if grade == "中吉":
        return f"你问的这件事，签是{grade}，能成但要费点工夫"
    if grade == "中平":
        return f"你问的这件事，签是{grade}，眼下还看不出准头"
    return f"你问的这件事，签是{grade}，先别急着动"


SYSTEM_PROMPT = (
    "你在一座小庙里替人解签。用户抽了一支六十甲子灵签，你要对着他问的那件事说清楚。"
    "先说结论，用什么态度做、行不行、往哪边走；再补一句签里的依据，别让人自己去对。"
    "不许编签里没有的具体时间、数字、人名：签文写「月中」就写月中，不要写成「中午前后」；"
    "签里没提日子就别给日子；也别写上午、中午、晚上这种时段，除非签文里明写。不用「首先其次」「总之」这类词，不重复签诗原文，"
    "不写「这支签的意思是」这种开场。两三句，结尾不加句号。"
)


def build_prompt(item: dict, question: str, hou: dict | None = None) -> str:
    poem = "，".join(item.get("poem") or [])
    items = "，".join(f"{k}{v}" for k, v in (item.get("items") or {}).items())
    vague = "" if question_kind(question) else (
        "他问得笼统，就往这几天该留意什么上说，别讲放之四海皆准的空话\n"
    )
    return (
        f"签号：第 {item['num']} 签 · {item.get('title', '')}（{grade_of(item)}）\n"
        f"签诗：{poem}\n"
        f"白话：{item.get('jie', '')}\n"
        f"分项：{items}\n"
        f"卦头故事：{item.get('story', '')}\n"
        + (f"时令：{hou['jie']}{'一二三'[max(0, hou['part'] - 1)]}候「{hou['name']}」，"
           f"{hou['plain']}\n" if hou else "")
        + f"用户问：{question}\n"
        + vague
        + "先说他问的这件事该怎么看，再用签里的东西作依据，两三句"
    )

TIME_WORDS = ("凌晨", "清早", "早上", "上午", "中午", "晌午", "下午", "傍晚", "晚上", "夜里", "半夜")


def drop_invented_time(answer: str, item: dict) -> str:
    """签文里没有的时段词，换成「这会儿」，免得凭空给个钟点。"""
    source = " ".join([
        "".join(item.get("poem") or []),
        item.get("jie", ""),
        "".join(f"{k}{v}" for k, v in (item.get("items") or {}).items()),
        item.get("story", ""),
        item.get("meta", ""),
    ])
    out = answer or ""
    for w in TIME_WORDS:
        if w in out and w not in source:
            out = out.replace(w, "这会儿")
    return out


def trim_text(text: str, limit: int) -> str:
    """模型爱写长句：按句号、分号、逗号截到 limit 字以内。"""
    t = (text or "").strip().strip('"“”')
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for mark in ("。", "；", "！", "？", "，"):
        pos = cut.rfind(mark)
        if pos > limit * 0.5:
            return cut[:pos]
    return cut


def no_period(text: str) -> str:
    """拟人路线不留句号。

    句尾的句号直接去掉；句中的句号，如果这句里已经有分号就换逗号，
    没有分号才换分号，免得一句里连着两个分号。
    """
    t = (text or "").strip()
    if not t:
        return ""
    while t.endswith(("。", "．", ".")):
        t = t[:-1].rstrip()
    mark = "，" if "；" in t else "；"
    t = t.replace("。", mark).replace("．", mark)
    t = t.replace("，，", "，").replace("；；", "；")
    return t.rstrip("，；")


def items_line(item: dict) -> str:
    """分项一行：功名…，财禄…，婚姻…，疾病…，出行…。"""
    items = item.get("items") or {}
    if not items:
        return ""
    return "，".join(f"{k}{v}" for k, v in items.items())


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
        self.scheduler: AsyncIOScheduler | None = None
        self._setup_daily()

    async def terminate(self) -> None:
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass

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
                 no_period(item.get("jie", "")), "", items_line(item)]
        if item.get("story"):
            parts.append("")
            parts.append("卦头：" + item["story"])
        return "\n".join(p for p in parts if p is not None)

    async def llm_answer(self, item: dict, question: str, hou: dict | None = None) -> str:
        """让模型对着问题解签，失败返回空串由规则兜底。"""
        if not self._cfg("llm_enabled", True) or not question:
            return ""
        try:
            provider = self.context.get_using_provider() if self.context else None
        except Exception:
            provider = None
        if provider is None:
            return ""
        limit = self._as_int("llm_max_chars", 80)
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=build_prompt(item, question, hou),
                                   system_prompt=SYSTEM_PROMPT),
                timeout=float(self._as_int("llm_timeout_seconds", 20)),
            )
            text = (getattr(resp, "completion_text", "") or "").strip()
        except Exception:
            return ""
        if not text:
            return ""
        return trim_text(drop_invented_time(text, item), limit)

    # ---------- 每日一签 ----------
    @property
    def _subs_path(self) -> str:
        return os.path.join(PLUGIN_DIR, "daily_subs.json")

    def load_subs(self) -> list:
        try:
            with open(self._subs_path, encoding="utf-8") as f:
                data = json.load(f)
            return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:
            return []

    def save_subs(self, subs: list) -> None:
        try:
            with open(self._subs_path, "w", encoding="utf-8") as f:
                json.dump(sorted(set(subs)), f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def push_times(self) -> list:
        times = [str(t).strip() for t in (self._cfg("daily_push_times", []) or []) if str(t).strip()]
        return times or ["08:00"]

    def subscribe(self, umo: str) -> str:
        if not umo:
            return "订不上，拿不到这个会话的标识。"
        subs = self.load_subs()
        if umo in subs:
            return f"已经订过了，每天 {'、'.join(self.push_times())} 推一支。"
        subs.append(umo)
        self.save_subs(subs)
        self._setup_daily()
        return (f"订好了，每天 {'、'.join(self.push_times())} 往这里推一支\n"
                "不想收就回两个字：退订")

    def unsubscribe(self, umo: str) -> str:
        subs = self.load_subs()
        if umo not in subs:
            return "这里没订过。"
        subs = [x for x in subs if x != umo]
        self.save_subs(subs)
        return "退了，不再推。"

    def daily_item(self, when=None) -> dict:
        """同一天推同一支：按日期取签，不看是谁。"""
        when = when or datetime.now()
        seed = int(when.strftime("%Y%m%d"))
        idx = seed % len(self.data)
        return self.data[idx]

    def _setup_daily(self) -> None:
        configured = [str(t).strip() for t in (self._cfg("daily_push_targets", []) or []) if str(t).strip()]
        targets = configured + [t for t in self.load_subs() if t not in configured]
        if not targets:
            return
        times = self.push_times()
        sched = self.scheduler or AsyncIOScheduler(timezone="Asia/Shanghai")
        try:
            sched.remove_all_jobs()
        except Exception:
            pass
        for slot in times:
            parts = slot.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                continue
            cron = f"{int(parts[1])} {int(parts[0])} * * *"
            sched.add_job(self._push_daily,
                          CronTrigger.from_crontab(cron, timezone="Asia/Shanghai"),
                          id="lingqian_daily_" + slot, replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=600)
        self.scheduler = sched
        try:
            if not sched.running:
                sched.start()
        except Exception:
            # 插件加载阶段没有事件循环，等收到第一条消息时再启
            pass

    async def _push_daily(self, targets: list | None = None) -> None:
        configured = [str(t).strip() for t in (self._cfg("daily_push_targets", []) or []) if str(t).strip()]
        targets = targets or (configured + [t for t in self.load_subs() if t not in configured])
        if not targets:
            return
        item = self.daily_item()
        head = f"今日一签 · 第 {item['num']} 签 · {item['title']}（{grade_of(item)}）"
        # 推送也带一句解语：没解出就退回签诗加白话
        # 推送没有上下文，把日期塞进问题里，模型才好谈“今天”
        today = f"今天是 {datetime.now().month} 月 {datetime.now().day} 日，今天该留意什么"
        answer = await self.llm_answer(item, today, current_hou())
        if answer:
            text = chr(10).join([head, no_period(answer), "", items_line(item)])
        else:
            poem_text = chr(10).join(item.get("poem") or [])
            text = chr(10).join([head, poem_text, "", no_period(item.get("jie", "")), "", items_line(item)])
        img = None
        if self._cfg("image_enabled", True):
            path = os.path.join(self._temp_dir(), f"lingqian_daily_{datetime.now():%Y%m%d}.jpg")
            try:
                await asyncio.to_thread(render_stick, item, path,
                                        self._as_int("image_width", 760), "", f"今日一签 {datetime.now():%m-%d}")
                img = path
            except Exception:
                img = None
        for umo in targets:
            try:
                parts = [Image.fromFileSystem(img)] if img else []
                parts.append(Plain(text))
                await self.context.send_message(umo, MessageChain(parts))
            except Exception:
                continue

    @staticmethod
    def hou_text() -> str:
        """今天落在第几候。"""
        h = current_hou()
        if not h:
            return "算不出现在的候。"
        part = "一二三"[max(0, h["part"] - 1)]
        return (f"第 {h['idx']} 候 · {h['jie']}{part}候「{h['name']}」\n"
                f"{h['plain']}\n"
                                f"{h['jie']}已经 {h['days_in']} 天，还有 {h['next_in_days']} 天到{h['next_jie']}"
                                "\n（七十二候按黄河流域记，南边走的是另一套冷暖）")

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
    def reading(item: dict, question: str = "", answer: str = "") -> str:
        """跟着签条发的那段字。有模型解语就短，没有才铺开。"""
        head = f"第 {item['num']} 签 · {item['title']}（{grade_of(item)}）"
        tail = items_line(item)
        ans = no_period(answer)
        if ans:
            body = head + "\n" + ans
            return body + (("\n\n" + tail) if tail else "")
        poem = "，".join(item["poem"][:2])
        body = head + "\n" + poem + "\n" + no_period(item.get("jie", ""))
        rule = no_period(answer_line(item, question))
        if rule:
            body += "\n\n" + rule
        return body + (("\n\n" + tail) if tail else "")

    def plain(self, item: dict, question: str = "", answer: str = "") -> str:
        """配置关掉图片时的纯文字版。"""
        poem = "\n".join(item["poem"])
        head = f"第 {item['num']} 签 · {item['title']}（{grade_of(item)}）"
        tail = items_line(item)
        ans = no_period(answer or answer_line(item, question))
        body = head + "\n" + poem + "\n\n" + no_period(item.get("jie", ""))
        if ans:
            body += "\n\n" + ans
        return body + (("\n\n" + tail) if tail else "")

    # ---------- 命令 ----------
    @filter.command("灵签", alias={"抽签", "求签"})
    async def cmd_lingqian(self, event: AstrMessageEvent, question: str = ""):
        if self.scheduler is not None and not self.scheduler.running:
            try:
                self.scheduler.start()
            except Exception:
                pass
        uid = str(event.get_sender_id())
        gid = str(event.get_group_id() or "")
        if question.strip() in ("订阅", "订", "subscribe"):
            yield event.plain_result(self.subscribe(str(event.unified_msg_origin or "")))
            return

        if question.strip() in ("退订", "取消订阅", "unsubscribe"):
            yield event.plain_result(self.unsubscribe(str(event.unified_msg_origin or "")))
            return

        if question.strip() in ("今候", "今日候", "候"):
            yield event.plain_result(self.hou_text())
            return

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

        answer = await self.llm_answer(item, question, current_hou()) if question else ""

        if self._cfg("shake_enabled", True):
            yield event.plain_result(random.choice(SHAKE_LINES))
            await asyncio.sleep(float(self._as_int("shake_delay", 2)))

        if not self._cfg("image_enabled", True):
            yield event.plain_result(self.plain(item, question, answer))
            return

        path = os.path.join(self._temp_dir(), f"lingqian_{uid}_{int(time.time())}.jpg")
        try:
            await asyncio.to_thread(
                render_stick, item, path,
                self._as_int("image_width", 760), question,
                datetime.now().strftime("%Y-%m-%d"),
            )
        except Exception:
            yield event.plain_result(self.plain(item, question, answer))
            return

        try:
            yield event.image_result(path)
        finally:
            pass
        yield event.plain_result(self.reading(item, question, answer))

    @filter.regex(r"(抽|求|摇|掷|看|来|给|帮|整)[^，。！？]{0,4}(签)")
    async def cmd_lingqian_loose(self, event: AstrMessageEvent):
        """没写命令词也能抽：「抽个签」「帮我求支签」。

        只认 At 或唤醒之后的话，普通闲聊不接。消息本来就以命令词开头的让路，
        免得同一条走两遍。
        """
        if not getattr(event, "is_at_or_wake_command", False):
            return
        raw = str(getattr(event, "message_str", "") or "").strip().lstrip("/").strip()
        if raw.startswith(("灵签", "抽签", "求签")):
            return
        # 把触发词从问句里剥掉，剩下的当问题
        question = re.sub(r"^(帮我|给我|麻烦)?\s*(抽|求|摇|掷|看|来|给|帮|整)[^，。！？]{0,4}签[，。！？\s]*", "", raw)
        if not question or len(question) > 24:
            question = ""
        async for item in self.cmd_lingqian(event, question):
            yield item

