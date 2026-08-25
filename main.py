
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Userbot — Mine Auto-Clicker (Multi-Task Edition)
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


# ═══════════════════════════════════════════════════════════════════════════
#  ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════════

API_ID       = int(os.environ.get("API_ID", "0"))
API_HASH     = os.environ.get("API_HASH", "")
PHONE        = os.environ.get("PHONE", "")
SESSION_STR  = os.environ.get("SESSION_STRING", "")
SESSION_FILE = "mine_session.txt"


# ═══════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

STORAGE_TAG      = "⚙️ MINE_USERBOT_CONFIG_V3"
SEARCH_LIMIT     = 50
DEFAULT_INTERVAL = 180
CLICK_RETRIES    = 15
CLICK_WAIT       = 2
TASK_TIMEOUT     = 50


# ═══════════════════════════════════════════════════════════════════════════
#  HELP TEXT
# ═══════════════════════════════════════════════════════════════════════════

HELP_TEXT = """
📖 راهنمای کامل دستورات

━━━ 🎮 مدیریت ماین (در گروه) ━━━
`.ماین روشن` — فعال‌سازی در گروه فعلی
`.ماین خاموش` — غیرفعال‌سازی در گروه فعلی
`.ماین تست` — تست تمام تسک‌ها

━━━ ⏱ تنظیم زمان ━━━
`.ماین زمان 3:50M` — ۳ دقیقه۵۰ ثانیه
`.ماین زمان 2:30H` — ۲ ساعت ۳۰ دقیقه
`.ماین زمان` — نمایش زمان فعلی

━━━ 📋 مدیریت تسک‌ها ━━━
`.تسک اضافه نام|کلمه|دکمه|تاخیر`
`.تسک حذف نام`
`.تسک لیست`
`.تسک ویرایش نام|کلمه|مقدار`
`.تسک ویرایش نام|دکمه|مقدار`
`.تسک ویرایش نام|تاخیر|مقدار`
`.تسک ویرایش نام|نام|مقدار`
`.تسک تست نام`

━━━ ⚙️ تنظیمات عمومی ━━━
`.ماین وضعیت` — نمایش تنظیمات
`.لاگ روشن` / `.لاگ خاموش`
`.گروه لیست` — گروه‌های فعال
`.ماین ریستارت` — ریستارت
`.پاکسازی` — ریست کامل
`.پشتیبان` — خروجی JSON
`.بارگذاری {json}` — بازیابی
`.راهنما` — این راهنما

━━━ 📝 مثال ━━━
`.تسک اضافه ماین|ماین|بفروش بره|4`
`.تسک اضافه ماهی|ماهی|بگیرش|3`
`.ماین زمان 5:00M`
`.ماین روشن`
""".strip()


# ═══════════════════════════════════════════════════════════════════════════
#  TEXT MATCHING
# ═══════════════════════════════════════════════════════════════════════════

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0\U000024C2-\U0001F251"
    "\U0001f926-\U0001f937\U00010000-\U0010ffff"
    "\u2640-\u2642\u2600-\u2B55\u200d\u23cf"
    "\u23e9\u231a\ufe0f\u3030"
    "]+",
    flags=re.UNICODE,
)


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def button_matches(btn_text, targets):
    a = normalize(btn_text)
    for target in targets:
        b = normalize(target)
        if not a or not b:
            continue
        if a == b or b in a or a in b:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  TASK MODEL
# ═══════════════════════════════════════════════════════════════════════════

class MineTask:
    def __init__(self, name="ماین", keywords=None, buttons=None, delay=4):
        self.name     = name
        self.keywords = keywords or ["ماین"]
        self.buttons  = buttons or ["بفروش بره"]
        self.delay    = delay

    def to_dict(self):
        return {
            "name":     self.name,
            "keywords": self.keywords,
            "buttons":  self.buttons,
            "delay":    self.delay,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            name     = d.get("name", "ماین"),
            keywords = d.get("keywords", ["ماین"]),
            buttons  = d.get("buttons", ["بفروش بره"]),
            delay    = d.get("delay", 4),
        )


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class Config:
    def __init__(self):
        self.interval       = DEFAULT_INTERVAL
        self.time_format    = "M"
        self.logging        = False
        self.active_chats   = []
        self.mine_tasks     = []
        self.storage_msg_id = None

    def to_dict(self):
        return {
            "interval":     self.interval,
            "time_format":  self.time_format,
            "logging":      self.logging,
            "active_chats": self.active_chats,
            "tasks":        [t.to_dict() for t in self.mine_tasks],
        }

    @classmethod
    def from_dict(cls, d):
        c = cls()
        c.interval     = d.get("interval", DEFAULT_INTERVAL)
        c.time_format  = d.get("time_format", "M")
        c.logging      = d.get("logging", False)
        c.active_chats = d.get("active_chats", [])

        if "tasks" in d and d["tasks"]:
            c.mine_tasks = [MineTask.from_dict(t) for t in d["tasks"]]
        elif "keywords" in d or "buttons" in d:
            kws = d.get("keywords", [d.get("keyword", "ماین")])
            btns = d.get("buttons", [d.get("button_text", "بفروش بره")])
            if isinstance(kws, str):
                kws = [kws]
            if isinstance(btns, str):
                btns = [btns]
            c.mine_tasks = [MineTask(name="ماین", keywords=kws, buttons=btns)]
        else:
            c.mine_tasks = [MineTask()]

        return c

    def get_task(self, name):
        for t in self.mine_tasks:
            if t.name == name:
                return t
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  USERBOT
# ═══════════════════════════════════════════════════════════════════════════

class MineUserbot:
    def __init__(self):
        self.client    = None
        self.config    = Config()
        self._tasks    = {}  # asyncio tasks
        self.owner     = 0
        self._cfg_lock = asyncio.Lock()

    # ── entry ──
    async def run(self):
        await self._init_client()
        await self._load_config()
        self._register_handlers()
        self._start_all()
        await self.client.run_until_disconnected()

    # ── client init ──
    async def _init_client(self):
        api_id   = API_ID   or int(input("API ID: "))
        api_hash = API_HASH or input("API Hash: ")

        if SESSION_STR:
            session = StringSession(SESSION_STR)
        elif os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r") as f:
                saved = f.read().strip()
            session = StringSession(saved) if saved else StringSession()
        else:
            session = StringSession()

        self.client = TelegramClient(session, api_id, api_hash)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            phone = PHONE or input("Phone (e.g. +989...): ")
            await self.client.send_code_request(phone)
            code = input("Verification code: ")
            try:
                await self.client.sign_in(phone, code)
            except SessionPasswordNeededError:
                pwd = input("2FA Password: ")
                await self.client.sign_in(password=pwd)
            try:
                ss = session.save()
                with open(SESSION_FILE, "w") as f:
                    f.write(ss)
            except Exception:
                pass

        self.owner = (await self.client.get_me()).id

    # ── storage ──
    async def _load_config(self):
        try:
            async for m in self.client.iter_messages("me", limit=50):
                if m.text and STORAGE_TAG in m.text:
                    match = re.search(r"```json\s*(\{.*?\})\s*```", m.text, re.S)
                    if match:
                        self.config = Config.from_dict(json.loads(match.group(1)))
                        self.config.storage_msg_id = m.id
                        return
        except Exception:
            pass
        await self._save_config()

    async def _save_config(self):
        async with self._cfg_lock:
            txt = (
                f"{STORAGE_TAG}\n\n"
                f"```json\n"
                f"{json.dumps(self.config.to_dict(), ensure_ascii=False, indent=2)}\n"
                f"```"
            )
            if self.config.storage_msg_id:
                try:
                    await self.client.edit_message("me", self.config.storage_msg_id, txt)
                    return
                except MessageNotModifiedError:
                    return
                except Exception:
                    pass
            try:
                msg = await self.client.send_message("me", txt)
                self.config.storage_msg_id = msg.id
            except Exception:
                pass

    async def _tell(self, text):
        try:
            await self.client.send_message("me", text)
        except Exception:
            pass

    # ── command handlers ──
    def _register_handlers(self):
        @self.client.on(events.NewMessage(pattern=r"^\..+"))
        async def _handler(event):
            try:
                if event.sender_id != self.owner:
                    return
                t = re.sub(r"\s+", " ", event.text.strip())
                c = event.chat_id

                # Help
                if t == ".راهنما":
                    await self._tell(HELP_TEXT)

                # Mine on/off
                elif t == ".ماین روشن":
                    await self._cmd_on(c)
                elif t == ".ماین خاموش":
                    await self._cmd_off(c)

                # Time
                elif t.startswith(".ماین زمان"):
                    rest = t[len(".ماین زمان"):].strip()
                    if not rest:
                        await self._tell(f"⏱ زمان فعلی: {self._fmt_interval()}")
                    else:
                        m = re.match(r"^(\d+:\d+)([MH])$", rest)
                        if m:
                            await self._cmd_time(m.group(1), m.group(2))
                        else:
                            await self._tell("❌ فرمت نامعتبر.\nمثال: `.ماین زمان 3:50M`")

                # Status / test / restart
                elif t == ".ماین وضعیت":
                    await self._cmd_status()
                elif t == ".ماین تست":
                    await self._cmd_test_all(c)
                elif t == ".ماین ریستارت":
                    self._restart_all()
                    await self._tell(f"🔄 ریستارت شد. {len(self.config.active_chats)} scheduler فعال.")

                # Task management
                elif t.startswith(".تسک اضافه "):
                    await self._cmd_task_add(t[len(".تسک اضافه "):].strip())
                elif t.startswith(".تسک حذف "):
                    await self._cmd_task_del(t[len(".تسک حذف "):].strip())
                elif t == ".تسک لیست":
                    await self._cmd_task_list()
                elif t.startswith(".تسک ویرایش "):
                    await self._cmd_task_edit(t[len(".تسک ویرایش "):].strip())
                elif t.startswith(".تسک تست "):
                    await self._cmd_task_test(c, t[len(".تسک تست "):].strip())

                # Logging
                elif t == ".لاگ روشن":
                    await self._cmd_log(True)
                elif t == ".لاگ خاموش":
                    await self._cmd_log(False)

                # Groups
                elif t == ".گروه لیست":
                    await self._cmd_groups()

                # Reset / backup / restore
                elif t == ".پاکسازی":
                    await self._cmd_reset()
                elif t == ".پشتیبان":
                    await self._cmd_backup()
                elif t.startswith(".بارگذاری "):
                    await self._cmd_restore(t[len(".بارگذاری "):].strip())

            except Exception:
                pass

    # ── mine on/off ──
    async def _cmd_on(self, cid):
        if cid > 0:
            await self._tell("❌ فقط در گروه.")
            return
        if not self.config.mine_tasks:
            await self._tell("❌ هیچ تسکی تعریف نشده. اول `.تسک اضافه` بزنید.")
            return
        if cid not in self.config.active_chats:
            self.config.active_chats.append(cid)
            await self._save_config()
        self._spawn(cid)
        n = len(self.config.mine_tasks)
        await self._tell(f"✅ ماین در گروه `{cid}` فعال شد.\n⏱ هر {self._fmt_interval()}\n📋 {n} تسک فعال")

    async def _cmd_off(self, cid):
        if cid in self.config.active_chats:
            self.config.active_chats.remove(cid)
            await self._save_config()
        self._kill(cid)
        await self._tell(f"🛑 ماین در گروه `{cid}` غیرفعال شد.")

    # ── time ──
    async def _cmd_time(self, ts, unit):
        secs = self._parse_time(ts, unit)
        if not secs or secs <= 0:
            await self._tell("❌ فرمت نامعتبر.\nمثال: `.ماین زمان 3:50M`")
            return
        self.config.interval    = secs
        self.config.time_format = unit
        await self._save_config()
        self._restart_all()
        await self._tell(f"⏱ زمان به {self._fmt_interval()} تغییر کرد. ✅ ذخیره شد.")

    # ── status ──
    async def _cmd_status(self):
        log = "✅ روشن" if self.config.logging else "❌ خاموش"
        cl = "\n".join(f"  • `{c}`" for c in self.config.active_chats) or "  (هیچ)"
        tasks = ""
        for i, t in enumerate(self.config.mine_tasks):
            kws = ", ".join(t.keywords)
            btns = ", ".join(t.buttons)
            tasks += f"\n  {i+1}. {t.name} | کلمات: {kws} | دکمه‌ها: {btns} | تاخیر: {t.delay}s"
        if not tasks:
            tasks = "\n  (هیچ تسکی)"
        await self._tell(
            f"📊 وضعیت\n\n⏱ زمان: {self._fmt_interval()}\n📝 لاگ: {log}\n📋 گروه‌ها:\n{cl}\n📋 تسک‌ها:{tasks}"
        )

    # ── test all ──
    async def _cmd_test_all(self, cid):
        if cid > 0:
            await self._tell("❌ فقط در گروه.")
            return
        if not self.config.mine_tasks:
            await self._tell("❌ هیچ تسکی تعریف نشده.")
            return

        results = []
        for task in self.config.mine_tasks:
            result = await self._test_task(cid, task)
            results.append(result)

        report = "\n\n".join(results)
        await self._tell(f"🧪 نتیجه تست:\n\n{report}")

    async def _cmd_task_test(self, cid, name):
        if cid > 0:
            await self._tell("❌ فقط در گروه.")
            return
        task = self.config.get_task(name)
        if not task:
            await self._tell(f"❌ تسک «{name}» پیدا نشد.")
            return
        result = await self._test_task(cid, task)
        await self._tell(result)

    async def _test_task(self, cid, task):
        # Send keywords
        for kw in task.keywords:
            try:
                await self.client.send_message(cid, kw)
            except Exception:
                pass

        await asyncio.sleep(task.delay)

        # Look for buttons
        found = False
        log = []
        try:
            async for msg in self.client.iter_messages(cid, limit=SEARCH_LIMIT):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue
                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        btn_text = getattr(btn, "text", "")
                        if button_matches(btn_text, task.buttons):
                            ok = await self._try_click(msg, cid, ri, ci, btn)
                            return (
                                f"📋 [{task.name}]\n"
                                f"  پیام: {msg.id}\n"
                                f"  دکمه: «{btn_text}»\n"
                                f"  نتیجه: {'✅ موفق' if ok else '❌ ناموفق'}"
                            )
                        log.append(f"  msg {msg.id}: «{btn_text}»")
        except Exception as e:
            return f"📋 [{task.name}]\n  ❌ خطا: {e}"

        targets = ", ".join(task.buttons)
        detail = "\n".join(log[:10]) or "  (بدون دکمه)"
        return f"📋 [{task.name}]\n  ❌ دکمه [{targets}] پیدا نشد.\n  دکمه‌های موجود:\n{detail}"

    # ── task management ──
    async def _cmd_task_add(self, arg):
        parts = arg.split("|")
        if len(parts) < 3:
            await self._tell(
                "❌ فرمت: `.تسک اضافه نام|کلمه|دکمه|تاخیر`\n"
                "مثال: `.تسک اضافه ماین|ماین|بفروش بره|4`\n"
                "تاخیر اختیاری (پیش‌فرض: 4 ثانیه)"
            )
            return

        name    = parts[0].strip()
        kws     = [k.strip() for k in parts[1].split(",") if k.strip()]
        btns    = [b.strip() for b in parts[2].split(",") if b.strip()]
        delay   = 4

        if len(parts) >= 4:
            try:
                delay = int(parts[3].strip())
                if delay < 1:
                    delay = 4
            except ValueError:
                await self._tell("❌ تاخیر باید عدد باشد. مثال: `4`")
                return

        if not name:
            await self._tell("❌ نام تسک نمی‌تواند خالی باشد.")
            return
        if not kws:
            await self._tell("❌ حداقل یک کلمه لازم است.")
            return
        if not btns:
            await self._tell("❌ حداقل یک دکمه لازم است.")
            return
        if self.config.get_task(name):
            await self._tell(f"❌ تسک «{name}» از قبل وجود دارد. از `.تسک ویرایش` استفاده کنید.")
            return

        task = MineTask(name=name, keywords=kws, buttons=btns, delay=delay)
        self.config.mine_tasks.append(task)
        await self._save_config()
        await self._tell(
            f"✅ تسک «{name}» اضافه شد.\n"
            f" 🔤 کلمات: {', '.join(kws)}\n"
            f"  🔘 دکمه‌ها: {', '.join(btns)}\n"
            f"  ⏱ تاخیر: {delay}s\n"
            f"  ✅ ذخیره شد."
        )

    async def _cmd_task_del(self, name):
        task = self.config.get_task(name)
        if not task:
            await self._tell(f"❌ تسک «{name}» پیدا نشد.")
            return
        self.config.mine_tasks.remove(task)
        await self._save_config()
        await self._tell(f"🗑 تسک «{name}» حذف شد. ✅ ذخیره شد.")

    async def _cmd_task_list(self):
        if not self.config.mine_tasks:
            await self._tell("📋 هیچ تسکی تعریف نشده.")
            return
        items = []
        for i, t in enumerate(self.config.mine_tasks):
            kws = ", ".join(t.keywords)
            btns = ", ".join(t.buttons)
            items.append(f"  {i+1}. **{t.name}**\n     کلمات: {kws}\n     دکمه‌ها: {btns}\n     تاخیر: {t.delay}s")
        await self._tell(f"📋 **لیست تسک‌ها:**\n\n" + "\n\n".join(items))

    async def _cmd_task_edit(self, arg):
        parts = arg.split("|")
        if len(parts) < 3:
            await self._tell(
                "❌ فرمت: `.تسک ویرایش نام|فیلد|مقدار`\n"
                "فیلدها: کلمه, دکمه, تاخیر, نام\n"
                "مثال: `.تسک ویرایش ماین|کلمه|ماین,میو`"
            )
            return

        name  = parts[0].strip()
        field = parts[1].strip()
        value = parts[2].strip()

        task = self.config.get_task(name)
        if not task:
            await self._tell(f"❌ تسک «{name}» پیدا نشد.")
            return

        if field == "کلمه":
            kws = [k.strip() for k in value.split(",") if k.strip()]
            if not kws:
                await self._tell("❌ حداقل یک کلمه لازم است.")
                return
            task.keywords = kws
 await self._save_config()
            await self._tell(f"✏️ کلمات تسک «{name}» به [{', '.join(kws)}] تغییر کرد. ✅ ذخیره شد.")

        elif field == "دکمه":
            btns = [b.strip() for b in value.split(",") if b.strip()]
            if not btns:
                await self._tell("❌ حداقل یک دکمه لازم است.")
                return
            task.buttons = btns
            await self._save_config()
            await self._tell(f"✏️ دکمه‌های تسک «{name}» به [{', '.join(btns)}] تغییر کرد. ✅ ذخیره شد.")

        elif field == "تاخیر":
            try:
                delay = int(value)
                if delay < 1:
                    await self._tell("❌ تاخیر باید حداقل 1 ثانیه باشد.")
                    return
                task.delay = delay
                await self._save_config()
                await self._tell(f"✏️ تاخیر تسک «{name}» به {delay}s تغییر کرد. ✅ ذخیره شد.")
            except ValueError:
                await self._tell("❌ تاخیر باید عدد باشد.")

        elif field == "نام":
            if not value:
                await self._tell("❌ نام نمی‌تواند خالی باشد.")
                return
            if self.config.get_task(value):
                await self._tell(f"❌ تسک «{value}» از قبل وجود دارد.")
                return
            old_name = task.name
            task.name = value
            await self._save_config()
            await self._tell(f"✏️ نام تسک «{old_name}» به «{value}» تغییر کرد. ✅ ذخیره شد.")

        else:
            await self._tell("❌ فیلد نامعتبر. فیلدها: کلمه, دکمه, تاخیر, نام")

    # ── logging ──
    async def _cmd_log(self, state):
        self.config.logging = state
        await self._save_config()
        await self._tell(f"📝 لاگ {'فعال' if state else 'غیرفعال'} شد. ✅ ذخیره شد.")

    # ── groups ──
    async def _cmd_groups(self):
        if not self.config.active_chats:
            await self._tell("📋 هیچ گروه فعالی وجود ندارد.")
            return
        items = "\n".join(f"  • `{c}`" for c in self.config.active_chats)
        await self._tell(f"📋 گروه‌های فعال:\n{items}")

    # ── reset / backup / restore ──
    async def _cmd_reset(self):
        for cid in list(self._tasks.keys()):
            self._kill(cid)
        self.config = Config()
        await self._save_config()
        await self._tell("♻️ تمام تنظیمات ریست شد. ✅ ذخیره شد.")

    async def _cmd_backup(self):
        data = json.dumps(self.config.to_dict(), ensure_ascii=False, indent=2)
        await self._tell(f"📦 پشتیبان:\n```json\n{data}\n```")

    async def _cmd_restore(self, js):
        try:
            d = json.loads(js)
            self.config = Config.from_dict(d)
            await self._save_config()
            self._restart_all()
            n = len(self.config.mine_tasks)
            await self._tell(f"✅ بازیابی شد. {n} تسک، {len(self.config.active_chats)} گروه.")
        except json.JSONDecodeError:
            await self._tell("❌ JSON نامعتبر.")
        except Exception as e:
            await self._tell(f"❌ خطا: {type(e).__name__}")

    # ── time parser / formatter ──
    @staticmethod
    def _parse_time(ts, unit):
        try:
            parts = ts.split(":")
            a, b = int(parts[0]), int(parts[1])
            if a < 0 or b < 0:
                return None
            if unit == "M" and b < 60:
                return a * 60 + b
            if unit == "H" and b < 60:
                return a * 3600 + b * 60
        except (ValueError, IndexError):
            pass
        return None

    def _fmt_interval(self):
        iv = self.config.interval
        if self.config.time_format == "M":
            m, s = divmod(iv, 60)
            return f"{m}:{s:02d} دقیقه"
        h, r = divmod(iv, 3600)
        return f"{h}:{r // 60:02d} ساعت"

    # ── task management (asyncio) ──
    def _spawn(self, cid):
        self._kill(cid)
        self._tasks[cid] = asyncio.create_task(self._scheduler(cid))

    def _kill(self, cid):
        t = self._tasks.pop(cid, None)
        if t and not t.done():
            t.cancel()

    def _start_all(self):
        for cid in list(self.config.active_chats):
            self._spawn(cid)

    def _restart_all(self):
        for cid in list(self._tasks.keys()):
            self._kill(cid)
        self._start_all()

    # ── scheduler ──
    async def _scheduler(self, cid):
        try:
            await self._execute_all_tasks(cid)
            nxt = time.time() + self.config.interval

            while cid in self.config.active_chats:
                wait = nxt - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                if cid not in self.config.active_chats:
                    break
                await self._execute_all_tasks(cid)
                nxt += self.config.interval
                if nxt < time.time():
                    nxt = time.time() + self.config.interval

        except asyncio.CancelledError:
            return
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 5)
            if cid in self.config.active_chats:
                self._spawn(cid)
        except (ChatWriteForbiddenError, UserNotParticipantError):
            if cid in self.config.active_chats:
                self.config.active_chats.remove(cid)
                await self._save_config()
            await self._tell(f"⚠️ عدم دسترسی به گروه `{cid}`")
        except Exception:
            await asyncio.sleep(30)
            if cid in self.config.active_chats:
                self._spawn(cid)
        finally:
            self._tasks.pop(cid, None)

    # ── execute all tasks ──
    async def _execute_all_tasks(self, cid):
        for task in self.config.mine_tasks:
            try:
                await self._execute_task(cid, task)
            except (FloodWaitError, ChatWriteForbiddenError, UserNotParticipantError):
                raise
            except Exception:
                if self.config.logging:
                    await self._tell(f"⚠️ [{task.name}] خطا در `{cid}`")
            await asyncio.sleep(1)

    # ── execute single task ──
    async def _execute_task(self, cid, task):
        start = time.time()

        for kw in task.keywords:
            if time.time() - start > TASK_TIMEOUT:
                break

            try:
                await self.client.send_message(cid, kw)
            except (ChatWriteForbiddenError, UserNotParticipantError):
                raise
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except Exception:
                continue

            await asyncio.sleep(task.delay)

            remaining = TASK_TIMEOUT - (time.time() - start)
            retries = min(CLICK_RETRIES, max(1, int(remaining / CLICK_WAIT)))

            clicked = False
            for _ in range(retries):
                try:
                    if await self._find_and_click(cid, task.buttons):
                        clicked = True
                        if self.config.logging:
                            await self._tell(f"✅ [{task.name}] کلیک موفق در `{cid}`")
                        break
                except FloodWaitError:
                    raise
                except (ChatWriteForbiddenError, UserNotParticipantError):
                    raise
                except Exception:
                    pass
                await asyncio.sleep(CLICK_WAIT)

            if not clicked and self.config.logging:
                await self._tell(f"⚠️ [{task.name}] دکمه پیدا نشد در `{cid}`")

    # ── find and click ──
    async def _find_and_click(self, cid, button_targets):
        try:
            async for msg in self.client.iter_messages(cid, limit=SEARCH_LIMIT):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue
                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        btn_text = getattr(btn, "text", "")
                        if button_matches(btn_text, button_targets):
                            return await self._try_click(msg, cid, ri, ci, btn)
        except FloodWaitError:
            raise
        except Exception:
            pass
        return False

    # ── click (4 methods) ──
    async def _try_click(self, msg, cid, ri, ci, btn):
        data = getattr(btn, "data", None)

        if data and GetBotCallbackAnswerRequest:
            try:
                peer = await self.client.get_input_entity(cid)
                await self.client(GetBotCallbackAnswerRequest(peer=peer, msg_id=msg.id, data=data))
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


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION STRING GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

async def generate_session():
    api_id   = int(input("API ID: "))
    api_hash = input("API Hash: ")
    client   = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    phone = input("Phone: ")
    await client.send_code_request(phone)
    code = input("Code: ")
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        pwd = input("2FA: ")
        await client.sign_in(password=pwd)
    ss = client.session.save()
    print(f"\nSESSION_STRING:\n{ss}")
    await client.disconnect()


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--generate-session":
        await generate_session()
    else:
        await MineUserbot().run()

if __name__ == "__main__":
    asyncio.run(main())
