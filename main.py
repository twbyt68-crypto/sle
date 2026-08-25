#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Userbot - Mine Auto-Clicker (Full Edition)
22 commands - Multiple keywords and buttons - Saved Messages storage
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


# ENVIRONMENT

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
PHONE = os.environ.get("PHONE", "")
SESSION_STR = os.environ.get("SESSION_STRING", "")
SESSION_FILE = "mine_session.txt"


# CONSTANTS

STORAGE_TAG = "MINE_USERBOT_CONFIG_V2"
SEARCH_LIMIT = 50
DEFAULT_INTERVAL = 180
CLICK_RETRIES = 15
CLICK_WAIT = 2
MINE_TIMEOUT = 50


# HELP TEXT

HELP_TEXT = """
راهنمای کامل دستورات

--- مدیریت ماین (در گروه) ---
.ماین روشن - فعال‌سازی در گروه فعلی
.ماین خاموش - غیرفعال‌سازی در گروه فعلی
.ماین تست - تست پیدا کردن و کلیک دکمه

--- تنظیم زمان ---
.ماین زمان 3:50M - 3 دقیقه 50 ثانیه
.ماین زمان 2:30H - 2 ساعت 30 دقیقه
.ماین زمان - نمایش زمان فعلی

--- مدیریت کلمات (ارسالی) ---
.کلمه اضافه ماین - افزودن کلمه
.کلمه حذف ماین - حذف کلمه
.کلمه ویرایش قدیم|جدید - ویرایش
.کلمه لیست - نمایش همه کلمات

--- مدیریت دکمه‌ها (کلیکی) ---
.دکمه اضافه بفروش بره - افزودن دکمه
.دکمه حذف بفروش بره - حذف دکمه
.دکمه ویرایش قدیم|جدید - ویرایش
.دکمه لیست - نمایش همه دکمه‌ها

--- تنظیمات عمومی ---
.ماین وضعیت - نمایش تمام تنظیمات
.لاگ روشن - فعال‌سازی لاگ در Saved Messages
.لاگ خاموش - غیرفعال‌سازی لاگ
.گروه لیست - نمایش گروه‌های فعال
.ماین ریستارت - ریستارت schedulerها
.پاکسازی - ریست کامل تمام تنظیمات
.پشتیبان - خروجی JSON تنظیمات
.بارگذاری {json} - بازیابی تنظیمات
.راهنما - نمایش این راهنما
""".strip()


# TEXT MATCHING

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001f926-\U0001f937"
    "\U00010000-\U0010ffff"
    "\u2640-\u2642"
    "\u2600-\u2B55"
    "\u200d"
    "\u23cf"
    "\u23e9"
    "\u231a"
    "\ufe0f"
    "\u3030"
    "]+",
    flags=re.UNICODE,
)


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def button_matches(btn_text, target):
    a = normalize(btn_text)
    b = normalize(target)
    if not a or not b:
        return False
    return a == b or b in a or a in b


# CONFIG

class Config:

    def __init__(self):
        self.interval = DEFAULT_INTERVAL
        self.time_format = "M"
        self.keywords = ["ماین"]
        self.buttons = ["بفروش بره"]
        self.active_chats = []
        self.logging = False
        self.storage_msg_id = None

    def to_dict(self):
        return {
            "interval": self.interval,
            "time_format": self.time_format,
            "keywords": self.keywords,
            "buttons": self.buttons,
            "active_chats": self.active_chats,
            "logging": self.logging,
        }

    @classmethod
    def from_dict(cls, d):
        c = cls()
        c.interval = d.get("interval", DEFAULT_INTERVAL)
        c.time_format = d.get("time_format", "M")
        c.logging = d.get("logging", False)
        c.active_chats = d.get("active_chats", [])
        if "keywords" in d:
            c.keywords = d["keywords"]
        elif "keyword" in d:
            c.keywords = [d["keyword"]]
        if "buttons" in d:
            c.buttons = d["buttons"]
        elif "button_text" in d:
            c.buttons = [d["button_text"]]
        return c


# USERBOT

class MineUserbot:

    def __init__(self):
        self.client = None
        self.config = Config()
        self.tasks = {}
        self.owner = 0
        self._cfg_lock = asyncio.Lock()

    async def run(self):
        await self._init_client()
        await self._load_config()
        self._register_handlers()
        self._start_all()
        await self.client.run_until_disconnected()

    async def _init_client(self):
        api_id = API_ID or int(input input("API Hash: ")

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
            data = json.dumps(
                self.config.to_dict(),
                ensure_ascii=False,
                indent=2
            )
            txt = STORAGE_TAG + "\n\n```json\n" + data + "\n```"
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

    async def _tell(self, text):
        try:
            await self.client.send_message("me", text)
        except Exception:
            pass

    def _register_handlers(self):
        @self.client.on(events.NewMessage(pattern=r"^\..+"))
        async def _handler(event):
            try:
                if event.sender_id != self.owner:
                    return
                t = re.sub(r"\s+", " ", event.text.strip())
                c = event.chat_id

                if t == ".راهنما":
                    await self._tell(HELP_TEXT)

                elif t == ".ماین روشن":
                    await self._cmd_on(c)
                elif t == ".ماین خاموش":
                    await self._cmd_off(c)

                elif t.startswith(".ماین زمان"):
                    rest = t[len(".ماین زمان"):].strip()
                    if not rest:
                        await self._tell(
                            "زمان فعلی: " + self._fmt_interval()
                        )
                    else:
                        m = re.match(r"^(\d+:\d+)([MH])$", rest)
                        if m:
                            await self._cmd_time(m.group(1), m.group(2))
                        else:
                            await self._tell(
                                "فرمت نامعتبر.\n"
                                "مثال: .ماین زمان 3:50M\n"
                                "مثال: .ماین زمان 2:30H"
                            )

                elif t == ".ماین وضعیت":
                    await self._cmd_status()
                elif t == ".ماین تست":
                    await self._cmd_test(c)
                elif t == ".ماین ریستارت":
                    self._restart_all()
                    n = len(self.config.active_chats)
                    await self._tell(
                        "ریستارت شد. " + str(n) + " scheduler فعال."
                    )

                elif t.startswith(".کلمه اضافه "):
                    kw = t[len(".کلمه اضافه "):].strip()
                    if kw:
                        await self._cmd_kw_add(kw)
                    else:
                        await self._tell("مثال: .کلمه اضافه ماین")
                elif t.startswith(".کلمه حذف "):
                    kw = t[len(".کلمه حذف "):].strip()
                    if kw:
                        await self._cmd_kw_del(kw)
                    else:
                        await self._tell("API ID: "))
        api_hash = API_HASH or("مثال: ..id, data=data
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
            await msg.click(text=getattr(btn, "text", ""))
            return True
        except Exception:
            pass

        return False


# SESSION STRING GENERATOR

async def generate_session():
    api_id = int(input("API ID: "))
    api_hash = input("API Hash: ")
    client = TelegramClient(StringSession(), api_id, api_hash)
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
    print("\nSESSION_STRING:\n" + ss)
    await client.disconnect()


# ENTRY POINT

async def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--generate-session":
        await generate_session()
    else:
        await MineUserbot().run()

if __name__ == "__main__":
    asyncio.run(main())
