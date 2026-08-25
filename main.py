#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Userbot — Mine Auto-Clicker
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


# ═══════════════════════════════════════════════════════════════════════════
#  ENVIRONMENT / CREDENTIALS
# ═══════════════════════════════════════════════════════════════════════════

API_ID       = int(os.environ.get("API_ID", "0"))
API_HASH     = os.environ.get("API_HASH", "")
PHONE        = os.environ.get("PHONE", "")
SESSION_STR  = os.environ.get("SESSION_STRING", "")
SESSION_FILE = "mine_session.txt"


# ═══════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

STORAGE_TAG      = "⚙️ MINE_USERBOT_CONFIG_V1"
DEFAULT_BUTTON   = "بفروش بره"
SEARCH_LIMIT     = 50
DEFAULT_KEYWORD  = "ماین"
DEFAULT_INTERVAL = 180
CLICK_RETRIES    = 15
CLICK_WAIT       = 2


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class Config:

    def __init__(self):
        self.interval       = DEFAULT_INTERVAL
        self.time_format    = "M"
        self.keyword        = DEFAULT_KEYWORD
        self.button_text    = DEFAULT_BUTTON
        self.active_chats   = []
        self.storage_msg_id = None

    def to_dict(self):
        return {
            "interval":     self.interval,
            "time_format":  self.time_format,
            "keyword":      self.keyword,
            "button_text":  self.button_text,
            "active_chats": self.active_chats,
        }

    @classmethod
    def from_dict(cls, d):
        c = cls()
        c.interval     = d.get("interval", DEFAULT_INTERVAL)
        c.time_format  = d.get("time_format", "M")
        c.keyword      = d.get("keyword", DEFAULT_KEYWORD)
        c.button_text  = d.get("button_text", DEFAULT_BUTTON)
        c.active_chats = d.get("active_chats", [])
        return c


# ═══════════════════════════════════════════════════════════════════════════
#  TEXT MATCHING
# ═══════════════════════════════════════════════════════════════════════════

def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    cleaned = re.sub(
        r'[\U0001F000-\U0001FFFF\U00002700-\U000027BF'
        r'\U0000FE00-\U0000FE0F\U0000200D\U00002600-\U000026FF'
        r'\U0000231A-\U0000231B\U00002328\U000023CF'
        r'\U000023E9-\U000023F3\U000023F8-\U000023FA'
        r'\U00002934-\U00002935\U000025AA-\U000025AB'
        r'\U000025B6\U000025C0\U000025FB-\U000025FE'
        r'\U00002B05-\U00002B07\U00002B1B-\U00002B1C'
        r'\U00002B50\U00002B55\U00003030\U0000303D'
        r'\U00003297\U00003299\U0000FE0F\U0000200D]+',
        '', text
    )
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def button_matches(btn_text, target):
    a = normalize(btn_text)
    b = normalize(target)
    return a == b or b in a or a in b


# ═══════════════════════════════════════════════════════════════════════════
#  USERBOT
# ═══════════════════════════════════════════════════════════════════════════

class MineUserbot:

    def __init__(self):
        self.client    = None
        self.config    = Config()
        self.tasks     = {}
        self.owner     = 0
        self._cfg_lock = asyncio.Lock()

    # ── entry ────────────────────────────────────────────────

    async def run(self):
        await self._init_client()
        await self._load_config()
        self._register_handlers()
        self._start_all()
        await self.client.run_until_disconnected()

    # ── client init ──────────────────────────────────────────

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

    # ── storage (Saved Messages) ─────────────────────────────

    async def _load_config(self):
        try:
            async for m in self.client.iter_messages("me", limit=50):
                if m.text and STORAGE_TAG in m.text:
                    match = re.search(
                        r"```json\s*(\{.*?\})\s*```", m.text, re.S
                    )
                    if match:
                        self.config = Config.from_dict(
                            json.loads(match.group(1))
                        )
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
                    await self.client.edit_message(
                        "me", self.config.storage_msg_id, txt
                    )
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

    async def _report(self, text):
        try:
            await self.client.send_message("me", text)
        except Exception:
            pass

    # ── command handlers ─────────────────────────────────────

    def _register_handlers(self):
        @self.client.on(events.NewMessage(pattern=r"^\..+"))
        async def _handler(event):
            try:
                if event.sender_id != self.owner:
                    return
                t = event.text.strip()
                c = event.chat_id

                # ── فعال/غیرفعال ──
                if t == ".ماین روشن":
                    await self._cmd_on(c)
                elif t == ".ماین خاموش":
                    await self._cmd_off(c)

                # ── زمان ──
                elif t.startswith(".ماین زمان"):
                    m = re.match(r"^\.ماین زمان\s+(\d+:\d+)([MH])$", t)
                    if m:
                        await self._cmd_time(m.group(1), m.group(2))
                    else:
                        await self._report(self._help_time())

                # ── کلمه (ارسال در گروه) ──
                elif t == ".کلمه":
                    await self._cmd_keyword_show()
                elif t.startswith(".کلمه ") and len(t) > 6:
                    kw = t[6:].strip()
                    if kw:
                        await self._cmd_keyword_set(kw)

                # ── دکمه (کلیک بعد از ماین) ──
                elif t == ".دکمه":
                    await self._cmd_button_show()
                elif t.startswith(".دکمه ") and len(t) > 6:
                    bt = t[6:].strip()
                    if bt:
                        await self._cmd_button_set(bt)

                # ── وضعیت و تست و راهنما ──
                elif t == ".ماین وضعیت":
                    await self._cmd_status()
                elif t == ".ماین تست":
                    await self._cmd_test(c)
                elif t == ".ماین راهنما":
                    await self._cmd_help()

            except Exception:
                pass

    # ── فعال/غیرفعال ────────────────────────────────────────

    async def _cmd_on(self, cid):
        if cid > 0:
            await self._report("❌ این دستور فقط در گروه کار می‌کند.")
            return
        if cid not in self.config.active_chats:
            self.config.active_chats.append(cid)
            await self._save_config()
        self._spawn(cid)

    async def _cmd_off(self, cid):
        if cid in self.config.active_chats:
            self.config.active_chats.remove(cid)
            await self._save_config()
        self._kill(cid)

    # ── زمان ────────────────────────────────────────────────

    async def _cmd_time(self, ts, unit):
        secs = self._parse_time(ts, unit)
        if not secs or secs <= 0:
            await self._report(self._help_time())
            return
        self.config.interval    = secs
        self.config.time_format = unit
        await self._save_config()
        self._restart_all()

    # ── کلمه (ارسال در گروه) ────────────────────────────────

    async def _cmd_keyword_show(self):
        await self._report(
            f"🔤 کلمه فعلی: `{self.config.keyword}`\n\n"
            f"این کلمه هر {self._format_interval()} در گروه‌های فعال ارسال می‌شود.\n\n"
            f"برای تغییر:\n"
            f"`.کلمه میو`\n"
            f"`.کلمه ماین`\n"
            f"`.کلمه هرچی`"
        )

    async def _cmd_keyword_set(self, kw):
        old = self.config.keyword
        self.config.keyword = kw
        await self._save_config()
        await self._report(
            f"🔤 کلمه تغییر کرد: `{old}` → `{kw}`\n"
            f"از این پس در گروه‌های فعال، کلمه `{kw}` ارسال می‌شود."
        )

    # ── دکمه (کلیک بعد از ماین) ────────────────────────────

    async def _cmd_button_show(self):
        await self._report(
            f"🔘 دکمه فعلی: `{self.config.button_text}`\n\n"
            f"بعد از ارسال کلمه `{self.config.keyword}` در گروه،\n"
            f"ربات پاسخ می‌دهد. Userbot دنبال دکمه‌ای با این متن می‌گردد\n"
            f"و روی آن کلیک می‌کند.\n\n"
            f"برای تغییر:\n"
            f"`.دکمه بفروش بره`\n"
            f"`.دکمه بذارش توی کوله‌پشتی`"
        )

    async def _cmd_button_set(self, bt):
        old = self.config.button_text
        self.config.button_text = bt
        await self._save_config()
        await self._report(
            f"🔘 دکمه تغییر کرد: `{old}` → `{bt}`\n"
            f"از این پس Userbot دنبال دکمه `{bt}` می‌گردد و کلیک می‌کند."
        )

    # ── وضعیت ───────────────────────────────────────────────

    async def _cmd_status(self):
        iv = self.config.interval
        if self.config.time_format == "M":
            m, s = divmod(iv, 60)
            td = f"{m}:{s:02d} دقیقه"
        else:
            h, r = divmod(iv, 3600)
            td = f"{h}:{r // 60:02d} ساعت"
        cl = (
            "\n".join(f"  • `{c}`" for c in self.config.active_chats)
            or "  (هیچ)"
        )
        await self._report(
            f"📊 **وضعیت Userbot**\n\n"
            f"⏱ فاصله زمانی: {td}\n"
            f"🔤 کلمه ارسالی: `{self.config.keyword}`\n"
            f"🔘 دکمه کلیک: `{self.config.button_text}`\n"
            f"📋 گروه‌های فعال:\n{cl}"
        )

    # ── راهنما ──────────────────────────────────────────────

    async def _cmd_help(self):
        await self._report(
            "📖 **راهنمای کامل Userbot**\n\n"

            "━━━ فعال/غیرفعال ━━━\n"
            "`.ماین روشن` — فعال‌سازی ماین در گروه فعلی\n"
            "`.ماین خاموش` — غیرفعال‌سازی ماین در گروه فعلی\n\n"

            "━━━ کلمه (ارسال در گروه) ━━━\n"
            "`.کلمه` — نمایش کلمه فعلی\n"
            "`.کلمه ماین` — تنظیم کلمه روی «ماین»\n"
            "`.کلمه میو` — تنظیم کلمه روی «میو»\n\n"
            "کلمه‌ای که اینجا تنظیم می‌شه، هر بار در گروه‌های\n"
            "فعال ارسال می‌شه تا ربات ماین جواب بده.\n\n"

            "━━━ دکمه (کلیک بعد از ماین) ━━━\n"
            "`.دکمه` — نمایش دکمه فعلی\n"
            "`.دکمه بفروش بره` — تنظیم دکمه روی «بفروش بره»\n"
            "`.دکمه بذارش توی کوله‌پشتی` — تنظیم روی «بذارش توی کوله‌پشتی»\n\n"
            "بعد از ارسال کلمه، ربات پاسخ می‌ده.\n"
            "Userbot دنبال دکمه‌ای با این متن می‌گرده و کلیک می‌کنه.\n\n"

            "━━━ زمان ━━━\n"
            "`.ماین زمان 3:50M` — هر ۳ دقیقه ۵۰ ثانیه\n"
            "`.ماین زمان 2:30H` — هر ۲ ساعت ۳۰ دقیقه\n\n"

            "━━━ ابزار ━━━\n"
            "`.ماین وضعیت` — نمایش تمام تنظیمات\n"
            "`.ماین تست` — تست دکمه در گروه فعلی\n"
            "`.ماین راهنما` — همین پیام\n\n"

            "━━━ مثال کامل ━━━\n"
            "۱. `.کلمه ماین` ← کلمه ارسالی\n"
            "۲. `.دکمه بفروش بره` ← دکمه‌ای که کلیک می‌شه\n"
            "۳. `.ماین زمان 3:00M` ← هر ۳ دقیقه\n"
            "۴. برو توی گروه ← `.ماین روشن`\n"
            "۵. تمام! خودکار کار می‌کنه."
        )

    # ── TEST ─────────────────────────────────────────────────

    async def _cmd_test(self, cid):
        if cid > 0:
            await self._report("❌ فقط در گروه.")
            return

        target = self.config.button_text
        found_any = False
        log = []

        try:
            async for msg in self.client.iter_messages(cid, limit=SEARCH_LIMIT):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue

                found_any = True

                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        btn_text = getattr(btn, "text", "")
                        btn_data = getattr(btn, "data", None)

                        if button_matches(btn_text, target):
                            ok = await self._try_click(msg, cid, ri, ci, btn)
                            await self._report(
                                f"🔍 تست دکمه:\n\n"
                                f"📨 پیام: {msg.id}\n"
                                f"🔘 دکمه: «{btn_text}»\n"
                                f"📦 Data: {btn_data}\n"
                                f"🎯 نتیجه: {'✅ موفق — کلیک انجام شد' if ok else '❌ ناموفق'}\n\n"
                                f"کلمه فعلی: `{self.config.keyword}`\n"
                                f"دکمه فعلی: `{self.config.button_text}`"
                            )
                            return

                        log.append(f"  msg {msg.id} [{ri},{ci}]: «{btn_text}»")

        except Exception as e:
            await self._report(f"❌ خطا: {e}")
            return

        if not found_any:
            await self._report(
                "❌ هیچ پیامی با دکمه در ۵۰ پیام آخر نبود.\n\n"
                f"کلمه فعلی: `{self.config.keyword}`\n"
                f"دکمه فعلی: `{self.config.button_text}`\n\n"
                "احتمالاً ربات هنوز پاسخ نداده."
            )
        else:
            detail = "\n".join(log[:20]) or "(خالی)"
            await self._report(
                f"❌ دکمه «{target}» پیدا نشد.\n\n"
                f"دکمه‌های موجود:\n{detail}\n\n"
                f"اگه متن دکمه فرق داره، با `.دکمه متن‌جدید` تغییرش بده."
            )

    # ── time parser ──────────────────────────────────────────

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

    def _format_interval(self):
        iv = self.config.interval
        if self.config.time_format == "M":
            m, s = divmod(iv, 60)
            return f"{m}:{s:02d} دقیقه"
        else:
            h, r = divmod(iv, 3600)
            return f"{h}:{r // 60:02d} ساعت"

    @staticmethod
    def _help_time():
        return (
            "❌ فرمت زمان نامعتبر.\n\n"
            "فرمت‌های درست:\n"
            "`.ماین زمان 3:50M` → ۳ دقیقه ۵۰ ثانیه\n"
            "`.ماین زمان 10:00M` → ۱۰ دقیقه\n"
            "`.ماین زمان 2:30H` → ۲ ساعت ۳۰ دقیقه\n"
            "`.ماین زمان 1:00H` → ۱ ساعت"
        )

    # ── task management ──────────────────────────────────────

    def _spawn(self, cid):
        self._kill(cid)
        self.tasks[cid] = asyncio.create_task(self._scheduler(cid))

    def _kill(self, cid):
        t = self.tasks.pop(cid, None)
        if t and not t.done():
            t.cancel()

    def _start_all(self):
        for cid in list(self.config.active_chats):
            self._spawn(cid)

    def _restart_all(self):
        for cid in list(self.tasks.keys()):
            self._kill(cid)
        self._start_all()

    # ── scheduler (timestamp-based, drift-free) ──────────────

    async def _scheduler(self, cid):
        try:
            await self._execute_mine(cid)
            nxt = time.time() + self.config.interval

            while cid in self.config.active_chats:
                wait = nxt - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                if cid not in self.config.active_chats:
                    break
                await self._execute_mine(cid)
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
            await self._report(f"⚠️ عدم دسترسی به گروه `{cid}`")
        except Exception:
            await asyncio.sleep(30)
            if cid in self.config.active_chats:
                self._spawn(cid)
        finally:
            self.tasks.pop(cid, None)

    # ── mine execution ───────────────────────────────────────

    async def _execute_mine(self, cid):
        try:
            await self.client.send_message(cid, self.config.keyword)
        except (ChatWriteForbiddenError, UserNotParticipantError):
            raise
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return
        except Exception:
            return

        for _ in range(CLICK_RETRIES):
            await asyncio.sleep(CLICK_WAIT)
            try:
                if await self._find_and_click(cid):
                    return
            except FloodWaitError:
                raise
            except (ChatWriteForbiddenError, UserNotParticipantError):
                raise
            except Exception:
                continue

    # ── find button ──────────────────────────────────────────

    async def _find_and_click(self, cid):
        target = self.config.button_text
        try:
            async for msg in self.client.iter_messages(
                cid, limit=SEARCH_LIMIT
            ):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue

                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        btn_text = getattr(btn, "text", "")
                        if button_matches(btn_text, target):
                            return await self._try_click(
                                msg, cid, ri, ci, btn
                            )

        except FloodWaitError:
            raise
        except Exception:
            pass
        return False

    # ── click (4 fallback methods) ───────────────────────────

    async def _try_click(self, msg, cid, ri, ci, btn):
        data = getattr(btn, "data", None)

        if data and GetBotCallbackAnswerRequest:
            try:
                peer = await self.client.get_input_entity(cid)
                await self.client(
                    GetBotCallbackAnswerRequest(
                        peer=peer, msg_id=msg.id, data=data,
                    )
                )
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
            await msg.click(text=self.config.button_text)
            return True
        except Exception:
            pass

        return False


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION STRING GENERATOR (for Railway)
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
