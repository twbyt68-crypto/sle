#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
import time
import unicodedata
import sys

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

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
PHONE = os.environ.get("PHONE", "")
SESSION_STR = os.environ.get("SESSION_STRING", "")
SESSION_FILE = "mine_session.txt"

STORAGE_TAG = "MINE_USERBOT_CONFIG_V2"
SEARCH_LIMIT = 50
DEFAULT_INTERVAL = 180
CLICK_RETRIES = 15
CLICK_WAIT = 2
MINE_TIMEOUT = 50

HELP_TEXT = (
    "راهنمای دستورات\n\n"
    "--- ماین (در گروه) ---\n"
    ".ماین روشن\n"
    ".ماین خاموش\n"
    ".ماین تست\n\n"
    "--- زمان ---\n"
    ".ماین زمان 3:50M\n"
    ".ماین زمان 2:30H\n"
    ".ماین زمان\n\n"
    "--- کلمات ---\n"
    ".کلمه اضافه ماین\n"
    ".کلمه حذف ماین\n"
    ".کلمه ویرایش قدیم|جدید\n"
    ".کلمه لیست\n\n"
    "--- دکمه‌ها ---\n"
    ".دکمه اضافه بفروش بره\n"
    ".دکمه حذف بفروش بره\n"
    ".دکمه ویرایش قدیم|جدید\n"
    ".دکمه لیست\n\n"
    "--- تنظیمات ---\n"
    ".ماین وضعیت\n"
    ".لاگ روشن\n"
    ".لاگ خاموش\n"
    ".گروه لیست\n"
    ".ماین ریستارت\n"
    ".پاکسازی\n"
    ".پشتیبان\n"
    ".بارگذاری {json}\n"
    ".راهنما"
)

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
        api_id = API_ID or int(input("API ID: "))
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
            phone = PHONE or input("Phone: ")
            await self.client.send_code_request(phone)
            code = input("Code: ")
            try:
                await self.client.sign_in(phone, code)
            except SessionPasswordNeededError:
                pwd = input("2FA: ")
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
                self.config.to_dict(), ensure_ascii=False, indent=2
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
        bot = self

        @self.client.on(events.NewMessage(pattern=r"^\..+"))
        async def _handler(event):
            try:
                if event.sender_id != bot.owner:
                    return
                t = re.sub(r"\s+", " ", event.text.strip())
                c = event.chat_id

                if t == ".راهنما":
                    await bot._tell(HELP_TEXT)

                elif t == ".ماین روشن":
                    await bot._cmd_on(c)

                elif t == ".ماین خاموش":
                    await bot._cmd_off(c)

                elif t.startswith(".ماین زمان"):
                    rest = t[len(".ماین زمان"):].strip()
                    if not rest:
                        await bot._tell(
                            "زمان فعلی: " + bot._fmt_interval()
                        )
                    else:
                        m = re.match(r"^(\d+:\d+)([MH])$", rest)
                        if m:
                            await bot._cmd_time(m.group(1), m.group(2))
                        else:
                            await bot._tell(
                                "فرمت نامعتبر. مثال: .ماین زمان 3:50M"
                            )

                elif t == ".ماین وضعیت":
                    await bot._cmd_status()

                elif t == ".ماین تست":
                    await bot._cmd_test(c)

                elif t == ".ماین ریستارت":
                    bot._restart_all()
                    n = len(bot.config.active_chats)
                    await bot._tell(
                        "ریستارت شد. " + str(n) + " scheduler فعال."
                    )

                elif t.startswith(".کلمه اضافه "):
                    kw = t[len(".کلمه اضافه "):].strip()
                    if kw:
                        await bot._cmd_kw_add(kw)

                elif t.startswith(".کلمه حذف "):
                    kw = t[len(".کلمه حذف "):].strip()
                    if kw:
                        await bot._cmd_kw_del(kw)

                elif t.startswith(".کلمه ویرایش "):
                    rest = t[len(".کلمه ویرایش "):].strip()
                    if "|" in rest:
                        old, new = rest.split("|", 1)
                        await bot._cmd_kw_edit(old.strip(), new.strip())
                    else:
                        await bot._tell("فرمت: .کلمه ویرایش قدیم|جدید")

                elif t == ".کلمه لیست":
                    await bot._cmd_kw_list()

                elif t.startswith(".دکمه اضافه "):
                    bt = t[len(".دکمه اضافه "):].strip()
                    if bt:
                        await bot._cmd_btn_add(bt)

                elif t.startswith(".دکمه حذف "):
                    bt = t[len(".دکمه حذف "):].strip()
                    if bt:
                        await bot._cmd_btn_del(bt)

                elif t.startswith(".دکمه ویرایش "):
                    rest = t[len(".دکمه ویرایش "):].strip()
                    if "|" in rest:
                        old, new = rest.split("|", 1)
                        await bot._cmd_btn_edit(old.strip(), new.strip())
                    else:
                        await bot._tell("فرمت: .دکمه ویرایش قدیم|جدید")

                elif t == ".دکمه لیست":
                    await bot._cmd_btn_list()

                elif t == ".لاگ روشن":
                    await bot._cmd_log(True)

                elif t == ".لاگ خاموش":
                    await bot._cmd_log(False)

                elif t == ".گروه لیست":
                    await bot._cmd_groups()

                elif t == ".پاکسازی":
                    await bot._cmd_reset()

                elif t == ".پشتیبان":
                    await bot._cmd_backup()

                elif t.startswith(".بارگذاری "):
                    js = t[len(".بارگذاری "):].strip()
                    if js:
                        await bot._cmd_restore(js)

            except Exception:
                pass

    async def _cmd_on(self, cid):
        if cid > 0:
            await self._tell("فقط در گروه قابل اجراست.")
            return
        if cid not in self.config.active_chats:
            self.config.active_chats.append(cid)
            await self._save_config()
        self._spawn(cid)
        kws = ", ".join(self.config.keywords)
        btns = ", ".join(self.config.buttons)
        await self._tell(
            "ماین فعال شد در " + str(cid) + "\n"
            "زمان: " + self._fmt_interval() + "\n"
            "کلمات: " + kws + "\n"
            "دکمه‌ها: " + btns
        )

    async def _cmd_off(self, cid):
        if cid in self.config.active_chats:
            self.config.active_chats.remove(cid)
            await self._save_config()
        self._kill(cid)
        await self._tell("ماین غیرفعال شد در " + str(cid))

    async def _cmd_time(self, ts, unit):
        secs = self._parse_time(ts, unit)
        if not secs or secs <= 0:
            await self._tell("فرمت نامعتبر.")
            return
        self.config.interval = secs
        self.config.time_format = unit
        await self._save_config()
        self._restart_all()
        await self._tell("زمان تغییر کرد: " + self._fmt_interval())

    async def _cmd_status(self):
        kws = ", ".join(self.config.keywords) or "(هیچ)"
        btns = ", ".join(self.config.buttons) or "(هیچ)"
        cl = "\n".join(str(c) for c in self.config.active_chats) or "(هیچ)"
        log = "روشن" if self.config.logging else "خاموش"
        await self._tell(
            "وضعیت:\n"
            "زمان: " + self._fmt_interval() + "\n"
            "کلمات: " + kws + "\n"
            "دکمه‌ها: " + btns + "\n"
            "لاگ: " + log + "\n"
            "گروه‌ها:\n" + cl
        )

    async def _cmd_test(self, cid):
        if cid > 0:
            await self._tell("فقط در گروه.")
            return
        found_any = False
        log = []
        try:
            async for msg in self.client.iter_messages(
                cid, limit=SEARCH_LIMIT
            ):
                rm = msg.reply_markup
                if not rm or not hasattr(rm, "rows"):
                    continue
                found_any = True
                for ri, row in enumerate(rm.rows):
                    if not hasattr(row, "buttons"):
                        continue
                    for ci, btn in enumerate(row.buttons):
                        btn_text = getattr(btn, "text", "")
                        for target in self.config.buttons:
                            if button_matches(btn_text, target):
                                ok = await self._try_click(
                                    msg, cid, ri, ci, btn
                                )
                                await self._tell(
                                    "تست:\n"
                                    "پیام: " + str(msg.id) + "\n"
                                    "دکمه: " + btn_text + "\n"
                                    "نتیجه: " + ("موفق" if ok else "ناموفق")
                                )
                                return
                        log.append(
                            "msg " + str(msg.id) + ": " + btn_text
                        )
        except Exception as e:
            await self._tell("خطا: " + str(e))
            return
        if not found_any:
            await self._tell("هیچ Keyboard نبود.")
        else:
            detail = "\n".join(log[:20]) or "(خالی)"
            await self._tell("دکمه پیدا نشد.\nموجود:\n" + detail)

    async def _cmd_kw_add(self, kw):
        if kw in self.config.keywords:
            await self._tell("از قبل وجود دارد: " + kw)
            return
        self.config.keywords.append(kw)
        await self._save_config()
        await self._tell("اضافه شد: " + kw)

    async def _cmd_kw_del(self, kw):
        if kw not in self.config.keywords:
            await self._tell("پیدا نشد: " + kw)
            return
        if len(self.config.keywords) <= 1:
            await self._tell("حداقل یک کلمه لازم است.")
            return
        self.config.keywords.remove(kw)
        await self._save_config()
        await self._tell("حذف شد: " + kw)

    async def _cmd_kw_edit(self, old, new):
        if not old or not new:
            await self._tell("فرمت: .کلمه ویرایش قدیم|جدید")
            return
        if old not in self.config.keywords:
            await self._tell("پیدا نشد: " + old)
            return
        idx = self.config.keywords.index(old)
        self.config.keywords[idx] = new
        await self._save_config()
        await self._tell(old + " -> " + new + " ذخیره شد.")

    async def _cmd_kw_list(self):
        if not self.config.keywords:
            await self._tell("کلمه‌ای نیست.")
            return
        items = "\n".join(
            str(i + 1) + ". " + k
            for i, k in enumerate(self.config.keywords)
        )
        await self._tell("کلمات:\n" + items)

    async def _cmd_btn_add(self, bt):
        if bt in self.config.buttons:
            await self._tell("از قبل وجود دارد: " + bt)
            return
        self.config.buttons.append(bt)
        await self._save_config()
        await self._tell("اضافه شد: " + bt)

    async def _cmd_btn_del(self, bt):
        if bt not in self.config.buttons:
            await self._tell("پیدا نشد: " + bt)
            return
        if len(self.config.buttons) <= 1:
            await self._tell("حداقل یک دکمه لازم است.")
            return
        self.config.buttons.remove(bt)
        await self._save_config()
        await self._tell("حذف شد: " + bt)

    async def _cmd_btn_edit(self, old, new):
        if not old or not new:
            await self._tell("فرمت: .دکمه ویرایش قدیم|جدید")
            return
        if old not in self.config.buttons:
            await self._tell("پیدا نشد: " + old)
            return
        idx = self.config.buttons.index(old)
        self.config.buttons[idx] = new
        await self._save_config()
        await self._tell(old + " -> " + new + " ذخیره شد.")

    async def _cmd_btn_list(self):
        if not self.config.buttons:
            await self._tell("دکمه‌ای نیست.")
            return
        items = "\n".join(
            str(i + 1) + ". " + b
            for i, b in enumerate(self.config.buttons)
        )
        await self._tell("دکمه‌ها:\n" + items)

    async def _cmd_log(self, state):
        self.config.logging = state
        await self._save_config()
        if state:
            await self._tell("لاگ روشن شد.")
        else:
            await self._tell("لاگ خاموش شد.")

    async def _cmd_groups(self):
        if not self.config.active_chats:
            await self._tell("گروه فعالی نیست.")
            return
        items = "\n".join(str(c) for c in self.config.active_chats)
        await self._tell("گروه‌های فعال:\n" + items)

    async def _cmd_reset(self):
        for cid in list(self.tasks.keys()):
            self._kill(cid)
        self.config = Config()
        await self._save_config()
        await self._tell("ریست شد.")

    async def _cmd_backup(self):
        data = json.dumps(
            self.config.to_dict(), ensure_ascii=False, indent=2
        )
        await self._tell("پشتیبان:\n```json\n" + data + "\n```")

    async def _cmd_restore(self, js):
        try:
            d = json.loads(js)
            self.config = Config.from_dict(d)
            await self._save_config()
            self._restart_all()
            await self._tell(
                "بازیابی شد.\n"
                "زمان: " + self._fmt_interval() + "\n"
                "کلمات: " + str(len(self.config.keywords)) + "\n"
                "دکمه‌ها: " + str(len(self.config.buttons)) + "\n"
                "گروه‌ها: " + str(len(self.config.active_chats))
            )
        except json.JSONDecodeError:
            await self._tell("JSON نامعتبر.")
        except Exception as e:
            await self._tell("خطا: " + str(e))

    @staticmethod
    def _parse_time(ts, unit):
        try:
            parts = ts.split(":")
            a = int(parts[0])
            b = int(parts[1])
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
            return str(m) + ":" + str(s).zfill(2) + " دقیقه"
        h, r = divmod(iv, 3600)
        return str(h) + ":" + str(r // 60).zfill(2) + " ساعت"

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
            await self._tell("عدم دسترسی: " + str(cid))
        except Exception:
            await asyncio.sleep(30)
            if cid in self.config.active_chats:
                self._spawn(cid)
        finally:
            self.tasks.pop(cid, None)

    async def _execute_mine(self, cid):
        start = time.time()
        for kw in self.config.keywords:
            if time.time() - start > MINE_TIMEOUT:
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
            remaining = MINE_TIMEOUT - (time.time() - start)
            retries = min(CLICK_RETRIES, max(1, int(remaining / CLICK_WAIT)))
            clicked = False
            for _ in range(retries):
                await asyncio.sleep(CLICK_WAIT)
                if time.time() - start > MINE_TIMEOUT:
                    break
                try:
                    if await self._find_and_click(cid):
                        clicked = True
                        if self.config.logging:
                            await self._tell("کلیک موفق: " + str(cid))
                        break
                except FloodWaitError:
                    raise
                except (ChatWriteForbiddenError, UserNotParticipantError):
                    raise
                except Exception:
                    continue
            if not clicked and self.config.logging:
                await self._tell("دکمه پیدا نشد: " + str(cid))
            await asyncio.sleep(1)

    async def _find_and_click(self, cid):
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
                        for target in self.config.buttons:
                            if button_matches(btn_text, target):
                                return await self._try_click(
                                    msg, cid, ri, ci, btn
                                )
        except FloodWaitError:
            raise
        except Exception:
            pass
        return False

    async def _try_click(self, msg, cid, ri, ci, btn):
        data = getattr(btn, "data", None)
        if data and GetBotCallbackAnswerRequest:
            try:
                peer = await self.client.get_input_entity(cid)
                await self.client(
                    GetBotCallbackAnswerRequest(
                        peer=peer, msg_id=msg.id, data=data
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
    print("SESSION_STRING:")
    print(ss)
    await client.disconnect()


async def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--generate-session":
        await generate_session()
    else:
        await MineUserbot().run()

if __name__ == "__main__":
    asyncio.run(main())
