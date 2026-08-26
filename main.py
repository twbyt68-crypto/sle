#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Userbot — Multi-Bot Auto-Clicker
Any keyword → Any button → Any bot
Works on Local and Railway

    pip install telethon
    python main.py
"""

import asyncio
import json
import os
import re
import time
import unicodedata

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    UserNotParticipantError,
    MessageNotModifiedError,
    SessionPasswordNeededError,
)

try:
    from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
except ImportError:
    GetBotCallbackAnswerRequest = None


# ═══════════════════════════════════════════════════════════════
#  ENV
# ═══════════════════════════════════════════════════════════════

API_ID       = int(os.environ.get("API_ID", "0"))
API_HASH     = os.environ.get("API_HASH", "")
PHONE        = os.environ.get("PHONE", "")
SESSION_STR  = os.environ.get("SESSION_STRING", "")
SESSION_FILE = "mine_session.txt"


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════

TAG            = "⚙️ UB_CONFIG_V4"
SEARCH         = 50
RETRIES        = 15
WAIT           = 2
TIMEOUT        = 55


# ═══════════════════════════════════════════════════════════════
#  HELP
# ═══════════════════════════════════════════════════════════════

HELP = """
📖 راهنما

━━━ ساخت تسک ━━━
`.تسک اضافه نام|کلمه|دکمه|تاخیر`
مثال:
`.تسک اضافه ماین|ماین|بفروش بره|4`
`.تسک اضافه ماهی|ماهی|بگیرش|3`
`.تسک اضافه جمع‌آوری|جمع|دریافت|5`

تاخیر = ثانیه صبر بعد از ارسال کلمه
(پیش‌فرض ۴ اگر ننویسید)

━━━ مدیریت تسک ━━━
`.تسک حذف نام` — حذف تسک
`.تسک لیست` — نمایش همه تسک‌ها

━━━ اجرا در گروه ━━━
`.روشن` — اجرای همه تسک‌ها در گروه
`.خاموش` — توقف در گروه

━━━ زمان‌بندی ━━━
`.زمان 3:50M` — ۳ دقیقه ۵۰ ثانیه
`.زمان 2:30H` — ۲ ساعت ۳۰ دقیقه
`.زمان` — نمایش زمان فعلی

━━━ سایر ━━━
`.وضعیت` — تنظیمات فعلی
`.تست` — تست در گروه فعلی
`.لاگ روشن` / `.لاگ خاموش`
`.راهنما` — این پیام
""".strip()


# ═══════════════════════════════════════════════════════════════
#  TEXT MATCHING
# ═══════════════════════════════════════════════════════════════

_EMOJI = re.compile(
    "[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D\u3030]+",
    flags=re.UNICODE,
)


def norm(t):
    t = unicodedata.normalize("NFKC", t)
    t = _EMOJI.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def match_btn(text, targets):
    a = norm(text)
    for tgt in targets:
        b = norm(tgt)
        if not a or not b:
            continue
        if a == b or b in a or a in b:
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════════

class Task:
    def __init__(self, name, keyword, button, delay=4):
        self.name    = name
        self.keyword = keyword
        self.button  = button
        self.delay   = delay

    def to_dict(self):
        return {"n": self.name, "k": self.keyword, "b": self.button, "d": self.delay}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("n",""), d.get("k",""), d.get("b",""), d.get("d",4))


class Cfg:
    def __init__(self):
        self.interval = 180
        self.fmt      = "M"
        self.log      = False
        self.chats    = []
        self.tasks    = []
        self.msg_id   = None

    def to_dict(self):
        return {
            "i": self.interval,
            "f": self.fmt,
            "l": self.log,
            "c": self.chats,
            "t": [x.to_dict() for x in self.tasks],
        }

    @classmethod
    def from_dict(cls, d):
        c = cls()
        c.interval = d.get("i", 180)
        c.fmt      = d.get("f", "M")
        c.log      = d.get("l", False)
        c.chats    = d.get("c", [])
        c.tasks    = [Task.from_dict(x) for x in d.get("t", [])]
        return c

    def find_task(self, name):
        for t in self.tasks:
            if t.name == name:
                return t
        return None


# ═══════════════════════════════════════════════════════════════
#  BOT
# ═══════════════════════════════════════════════════════════════

class Bot:
    def __init__(self):
        self.cli   = None
        self.cfg   = Cfg()
        self.jobs  = {}
        self.owner = 0
        self.lock  = asyncio.Lock()

    # ── run ──
    async def run(self):
        await self._login()
        await self._load()
        self._bind()
        self._start_all()
        await self.cli.run_until_disconnected()

    # ── login ──
    async def _login(self):
        aid  = API_ID   or int(input("API ID: "))
        ahash = API_HASH or input("API Hash: ")

        if SESSION_STR:
            ses = StringSession(SESSION_STR)
        elif os.path.exists(SESSION_FILE):
            with open(SESSION_FILE) as f:
                s = f.read().strip()
            ses = StringSession(s) if s else StringSession()
        else:
            ses = StringSession()

        self.cli = TelegramClient(ses, aid, ahash)
        await self.cli.connect()

        if not await self.cli.is_user_authorized():
            ph = PHONE or input("Phone: ")
            await self.cli.send_code_request(ph)
            cd = input("Code: ")
            try:
                await self.cli.sign_in(ph, cd)
            except SessionPasswordNeededError:
                await self.cli.sign_in(password=input("2FA: "))
            try:
                with open(SESSION_FILE, "w") as f:
                    f.write(ses.save())
            except Exception:
                pass

        self.owner = (await self.cli.get_me()).id

    # ── storage ──
    async def _load(self):
        try:
            async for m in self.cli.iter_messages("me", limit=30):
                if m.text and TAG in m.text:
                    jm = re.search(r"```json\s*(\{.*?\})\s*```", m.text, re.S)
                    if jm:
                        self.cfg = Cfg.from_dict(json.loads(jm.group(1)))
                        self.cfg.msg_id = m.id
                        return
        except Exception:
            pass
        await self._save()

    async def _save(self):
        async with self.lock:
            txt = f"{TAG}\n\n```json\n{json.dumps(self.cfg.to_dict(), ensure_ascii=False, indent=2)}\n```"
            if self.cfg.msg_id:
                try:
                    await self.cli.edit_message("me", self.cfg.msg_id, txt)
                    return
                except MessageNotModifiedError:
                    return
                except Exception:
                    pass
            try:
                m = await self.cli.send_message("me", txt)
                self.cfg.msg_id = m.id
            except Exception:
                pass

    async def _say(self, t):
        try:
            await self.cli.send_message("me", t)
        except Exception:
            pass

    # ── commands ──
    def _bind(self):
        @self.cli.on(events.NewMessage(pattern=r"^\..+"))
        async def _(e):
            try:
                if e.sender_id != self.owner:
                    return
                t = re.sub(r"\s+", " ", e.text.strip())
                c = e.chat_id

                if t == ".راهنما":
                    await self._say(HELP)

                elif t == ".روشن":
                    await self._on(c)
                elif t == ".خاموش":
                    await self._off(c)
                elif t == ".تست":
                    await self._test(c)
                elif t == ".وضعیت":
                    await self._status()
                elif t == ".لاگ روشن":
                    self.cfg.log = True; await self._save()
                    await self._say("📝 لاگ روشن شد ✅")
                elif t == ".لاگ خاموش":
                    self.cfg.log = False; await self._save()
                    await self._say("📝 لاگ خاموش شد ✅")

                elif t.startswith(".زمان"):
                    rest = t[5:].strip()
                    if not rest:
                        await self._say(f"⏱ {self._fmt()}")
                    else:
                        m = re.match(r"^(\d+:\d+)([MH])$", rest)
                        if m:
                            await self._set_time(m.group(1), m.group(2))
                        else:
                            await self._say("❌ مثال: `.زمان 3:50M`")

                elif t.startswith(".تسک اضافه"):
                    await self._task_add(t[10:].strip())
                elif t.startswith(".تسک حذف"):
                    await self._task_del(t[8:].strip())
                elif t == ".تسک لیست":
                    await self._task_list()

            except Exception:
                pass

    # ── on/off ──
    async def _on(self, cid):
        if cid > 0:
            await self._say("❌ فقط در گروه")
            return
        if not self.cfg.tasks:
            await self._say("❌ اول تسک بساز:\n`.تسک اضافه ماین|ماین|بفروش بره|4`")
            return
        if cid not in self.cfg.chats:
            self.cfg.chats.append(cid)
            await self._save()
        self._spawn(cid)
        n = len(self.cfg.tasks)
        await self._say(f"✅ روشن شد در `{cid}`\n⏱ {self._fmt()}\n📋 {n} تسک فعال")

    async def _off(self, cid):
        if cid in self.cfg.chats:
            self.cfg.chats.remove(cid)
            await self._save()
        self._kill(cid)
        await self._say(f"🛑 خاموش شد در `{cid}`")

    # ── time ──
    async def _set_time(self, ts, u):
        s = self._parse(ts, u)
        if not s or s < 1:
            await self._say("❌ مثال: `.زمان 3:50M`")
            return
        self.cfg.interval = s
        self.cfg.fmt = u
        await self._save()
        self._restart_all()
        await self._say(f"⏱ {self._fmt()} ✅")

    # ── task management ──
    async def _task_add(self, arg):
        if not arg:
            await self._say("❌ فرمت:\n`.تسک اضافه نام|کلمه|دکمه|تاخیر`\nمثال:\n`.تسک اضافه ماین|ماین|بفروش بره|4`")
            return
        parts = arg.split("|")
        if len(parts) < 3:
            await self._say("❌ حداقل ۳ بخش لازم:\n`.تسک اضافه نام|کلمه|دکمه|تاخیر`")
            return

        name = parts[0].strip()
        kw   = parts[1].strip()
        btn  = parts[2].strip()
        dly  = 4

        if len(parts) >= 4:
            try:
                dly = int(parts[3].strip())
                if dly < 1:
                    dly = 4
            except ValueError:
                await self._say("❌ تاخیر باید عدد باشد")
                return

        if not name or not kw or not btn:
            await self._say("❌ نام و کلمه و دکمه نمی‌تواند خالی باشد")
            return
        if self.cfg.find_task(name):
            await self._say(f"❌ تسک «{name}» وجود دارد. اول حذفش کن.")
            return

        self.cfg.tasks.append(Task(name, kw, btn, dly))
        await self._save()
        await self._say(
            f"✅ تسک «{name}» ساخته شد\n"
            f"🔤 کلمه: {kw}\n"
            f"🔘 دکمه: {btn}\n"
            f"⏱ تاخیر: {dly}s"
        )

    async def _task_del(self, name):
        t = self.cfg.find_task(name)
        if not t:
            await self._say(f"❌ تسک «{name}» نیست")
            return
        self.cfg.tasks.remove(t)
        await self._save()
        await self._say(f"🗑 «{name}» حذف شد ✅")

    async def _task_list(self):
        if not self.cfg.tasks:
            await self._say("📋 هیچ تسکی نیست.\n`.تسک اضافه ماین|ماین|بفروش بره|4`")
            return
        lines = []
        for i, t in enumerate(self.cfg.tasks, 1):
            lines.append(f"{i}. **{t.name}**\n   کلمه: `{t.keyword}`\n   دکمه: `{t.button}`\n   تاخیر: {t.delay}s")
        await self._say("📋 تسک‌ها:\n\n" + "\n\n".join(lines))

    # ── status ──
    async def _status(self):
        cl = "\n".join(f"  • `{c}`" for c in self.cfg.chats) or "  (هیچ)"
        tl = len(self.cfg.tasks)
        lg = "✅" if self.cfg.log else "❌"
        await self._say(
            f"📊 وضعیت\n\n⏱ زمان: {self._fmt()}\n"
            f"📋 {tl} تسک\n📝 لاگ: {lg}\n"
            f"گروه‌ها:\n{cl}"
        )

    # ── test ──
    async def _test(self, cid):
        if cid > 0:
            await self._say("❌ فقط در گروه")
            return
        if not self.cfg.tasks:
            await self._say("❌ تسکی نیست")
            return

        results = []
        for task in self.cfg.tasks:
            r = await self._run_test(cid, task)
            results.append(r)
        await self._say("🧪 تست:\n\n" + "\n\n".join(results))

    async def _run_test(self, cid, task):
        try:
            await self.cli.send_message(cid, task.keyword)
        except Exception as ex:
            return f"[{task.name}] ❌ ارسال نشد: {ex}"

        await asyncio.sleep(task.delay)

        try:
            async for msg in self.cli.iter_messages(cid, limit=SEARCH):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue
                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        bt = getattr(btn, "text", "")
                        if match_btn(bt, [task.button]):
                            ok = await self._click(msg, cid, ri, ci, btn)
                            return f"[{task.name}] {'✅' if ok else '❌'} «{bt}» msg:{msg.id}"
        except Exception as ex:
            return f"[{task.name}] ❌ خطا: {ex}"

        return f"[{task.name}] ❌ دکمه «{task.button}» پیدا نشد"

    # ── time helpers ──
    @staticmethod
    def _parse(ts, u):
        try:
            a, b = int(ts.split(":")[0]), int(ts.split(":")[1])
            if a < 0 or b < 0:
                return None
            if u == "M" and b < 60:
                return a * 60 + b
            if u == "H" and b < 60:
                return a * 3600 + b * 60
        except (ValueError, IndexError):
            pass
        return None

    def _fmt(self):
        iv = self.cfg.interval
        if self.cfg.fmt == "M":
            m, s = divmod(iv, 60)
            return f"{m}:{s:02d} دقیقه"
        h, r = divmod(iv, 3600)
        return f"{h}:{r // 60:02d} ساعت"

    # ── job management ──
    def _spawn(self, cid):
        self._kill(cid)
        self.jobs[cid] = asyncio.create_task(self._loop(cid))

    def _kill(self, cid):
        j = self.jobs.pop(cid, None)
        if j and not j.done():
            j.cancel()

    def _start_all(self):
        for cid in list(self.cfg.chats):
            self._spawn(cid)

    def _restart_all(self):
        for cid in list(self.jobs.keys()):
            self._kill(cid)
        self._start_all()

    # ── scheduler ──
    async def _loop(self, cid):
        try:
            await self._run_all(cid)
            nxt = time.time() + self.cfg.interval
            while cid in self.cfg.chats:
                w = nxt - time.time()
                if w > 0:
                    await asyncio.sleep(w)
                if cid not in self.cfg.chats:
                    break
                await self._run_all(cid)
                nxt += self.cfg.interval
                if nxt < time.time():
                    nxt = time.time() + self.cfg.interval
        except asyncio.CancelledError:
            return
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 5)
            if cid in self.cfg.chats:
                self._spawn(cid)
        except (ChatWriteForbiddenError, UserNotParticipantError):
            if cid in self.cfg.chats:
                self.cfg.chats.remove(cid)
                await self._save()
            await self._say(f"⚠️ دسترسی `{cid}` از دست رفت")
        except Exception:
            await asyncio.sleep(30)
            if cid in self.cfg.chats:
                self._spawn(cid)
        finally:
            self.jobs.pop(cid, None)

    # ── run all tasks ──
    async def _run_all(self, cid):
        for task in self.cfg.tasks:
            try:
                await self._run_one(cid, task)
            except (FloodWaitError, ChatWriteForbiddenError, UserNotParticipantError):
                raise
            except Exception:
                if self.cfg.log:
                    await self._say(f"⚠️ [{task.name}] خطا در `{cid}`")
            await asyncio.sleep(1)

    # ── run one task ──
    async def _run_one(self, cid, task):
        try:
            await self.cli.send_message(cid, task.keyword)
        except (ChatWriteForbiddenError, UserNotParticipantError):
            raise
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return
        except Exception:
            return

        await asyncio.sleep(task.delay)

        for _ in range(RETRIES):
            try:
                if await self._find_click(cid, [task.button]):
                    if self.cfg.log:
                        await self._say(f"✅ [{task.name}] در `{cid}`")
                    return
            except FloodWaitError:
                raise
            except (ChatWriteForbiddenError, UserNotParticipantError):
                raise
            except Exception:
                pass
            await asyncio.sleep(WAIT)

        if self.cfg.log:
            await self._say(f"⚠️ [{task.name}] دکمه نشد در `{cid}`")

    # ── find and click ──
    async def _find_click(self, cid, btns):
        try:
            async for msg in self.cli.iter_messages(cid, limit=SEARCH):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue
                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        if match_btn(getattr(btn, "text", ""), btns):
                            return await self._click(msg, cid, ri, ci, btn)
        except FloodWaitError:
            raise
        except Exception:
            pass
        return False

    # ── click (4 methods) ──
    async def _click(self, msg, cid, ri, ci, btn):
        data = getattr(btn, "data", None)

        if data and GetBotCallbackAnswerRequest:
            try:
                peer = await self.cli.get_input_entity(cid)
                await self.cli(GetBotCallbackAnswerRequest(peer=peer, msg_id=msg.id, data=data))
                return True
            except Exception:
                pass

        if data:
            try:
                await msg.click(data=data)
                return True
            except Exception:
                pass

        try:
            await msg.click(ri, ci)
            return True
        except Exception:
            pass

        try:
            await msg.click(text=getattr(btn, "text", ""))
            return True
        except Exception:
            pass

        return False


# ═══════════════════════════════════════════════════════════════
#  SESSION GENERATOR
# ═══════════════════════════════════════════════════════════════

async def gen_session():
    aid  = int(input("API ID: "))
    ah   = input("API Hash: ")
    c    = TelegramClient(StringSession(), aid, ah)
    await c.connect()
    ph = input("Phone: ")
    await c.send_code_request(ph)
    cd = input("Code: ")
    try:
        await c.sign_in(ph, cd)
    except SessionPasswordNeededError:
        await c.sign_in(password=input("2FA: "))
    print(f"\nSESSION_STRING:\n{c.session.save()}")
    await c.disconnect()


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

async def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--gen-session":
        await gen_session()
    else:
        await Bot().run()

if __name__ == "__main__":
    asyncio.run(main())
