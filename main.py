#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import json
import re
import os
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

# حذف تمام لاگ‌های Telethon
logging.getLogger("telethon").setLevel(logging.CRITICAL)

# ============================================================
# ⚙️ تنظیمات
# ============================================================
API_ID = 21052750
API_HASH = "857cc5ccbb70a2e67294d038b1000805"
PHONE_NUMBER = "+989211390758"

# ============================================================
# کلاس تنظیمات
# ============================================================
class MineConfig:
    def __init__(self):
        self.interval: int = 230
        self.time_format: str = "M"
        self.keyword: str = "ماین"
        self.active_chats: Set[int] = set()
        self.owner_id: Optional[int] = None
        self.storage_message_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "interval": self.interval,
            "time_format": self.time_format,
            "keyword": self.keyword,
            "active_chats": list(self.active_chats)
        }

    def from_dict(self, data: dict):
        self.interval = data.get("interval", 230)
        self.time_format = data.get("time_format", "M")
        self.keyword = data.get("keyword", "میو")
        self.active_chats = set(data.get("active_chats", []))

# ============================================================
# کلاس اصلی
# ============================================================
class UserBot:
    def __init__(self, api_id: int, api_hash: str):
        self.client = TelegramClient('userbot_session', api_id, api_hash)
        self.config = MineConfig()
        self.schedulers: Dict[int, asyncio.Task] = {}
        self.last_execution: Dict[int, datetime] = {}

    async def start(self):
        await self.client.start()
        self.config.owner_id = (await self.client.get_me()).id
        await self.load_config_from_saved_messages()
        await self.setup_handlers()
        await self.start_all_schedulers()
        await self.client.run_until_disconnected()

    # ====================== ذخیره‌سازی ======================
    async def load_config_from_saved_messages(self):
        try:
            async for message in self.client.iter_messages("me", limit=100):
                if message.text and message.text.startswith("MINE_CONFIG:"):
                    try:
                        data = json.loads(message.text.replace("MINE_CONFIG:", ""))
                        self.config.from_dict(data)
                        self.config.storage_message_id = message.id
                        return
                    except:
                        continue
            await self.save_config_to_saved_messages()
        except Exception as e:
            await self.report_error(f"خطا در بارگذاری: {str(e)}")

    async def save_config_to_saved_messages(self):
        try:
            config_json = json.dumps(self.config.to_dict(), ensure_ascii=False)
            text = f"MINE_CONFIG:{config_json}"
            if self.config.storage_message_id:
                await self.client.edit_message("me", self.config.storage_message_id, text)
            else:
                msg = await self.client.send_message("me", text)
                self.config.storage_message_id = msg.id
        except Exception as e:
            await self.report_error(f"خطا در ذخیره: {str(e)}")

    async def report_error(self, message: str):
        try:
            await self.client.send_message("me", f"⚠️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{message}")
        except:
            pass

    # ====================== هندلر ======================
    async def setup_handlers(self):
        @self.client.on(events.NewMessage(outgoing=True))
        async def handler(event):
            if not event.is_private:
                if event.sender_id != self.config.owner_id:
                    return
                chat_id = event.chat_id
                text = event.message.text.strip()

                if text == ".ماین روشن":
                    await event.delete()
                    self.config.active_chats.add(chat_id)
                    await self.save_config_to_saved_messages()
                    if chat_id not in self.schedulers or self.schedulers[chat_id].done():
                        self.schedulers[chat_id] = asyncio.create_task(self.mine_scheduler(chat_id))

                elif text == ".ماین خاموش":
                    await event.delete()
                    self.config.active_chats.discard(chat_id)
                    await self.save_config_to_saved_messages()
                    if chat_id in self.schedulers:
                        self.schedulers[chat_id].cancel()
                        del self.schedulers[chat_id]

                elif text.startswith(".ماین زمان "):
                    await event.delete()
                    secs = self.parse_time_format(text.replace(".ماین زمان ", ""))
                    if secs is None or secs <= 0:
                        await self.client.send_message("me", "❌ فرمت زمان اشتباه است.")
                    else:
                        self.config.interval = secs
                        await self.save_config_to_saved_messages()
                        await self.client.send_message("me", f"✅ زمان تنظیم شد: {self.format_interval_display(secs)}")

                elif text.startswith(".کلمه "):
                    await event.delete()
                    new_k = text.replace(".کلمه ", "").strip()
                    if len(new_k) > 50 or not new_k:
                        await self.client.send_message("me", "❌ کلمه نامعتبر است.")
                    else:
                        self.config.keyword = new_k
                        await self.save_config_to_saved_messages()
                        await self.client.send_message("me", f"✅ کلمه ماین حالا: {new_k}")

                elif text == ".ماین وضعیت":
                    await event.delete()
                    await self.client.send_message("me", self.get_status())

    def parse_time_format(self, time_str: str) -> Optional[int]:
        m = re.match(r'^(\d+):(\d{2})([MH])$', time_str)
        if not m: return None
        v1, v2, u = m.groups()
        v1 = int(v1)
        v2 = int(v2)
        if v2 > 59: return None
        if u == "M": return v1 * 60 + v2
        if u == "H": return v1 * 3600 + v2 * 60
        return None

    @staticmethod
    def format_interval_display(seconds: int) -> str:
        if seconds < 3600:
            return f"{seconds//60}:{seconds%60:02d}M"
        return f"{seconds//3600}:{(seconds%3600)//60:02d}H"

    def get_status(self) -> str:
        active = len(self.config.active_chats)
        return f"""📊 وضعیت:
⏱️ زمان: {self.format_interval_display(self.config.interval)}
🔑 کلمه: {self.config.keyword}
👥 گروه‌ها: {active}"""

    # ====================== scheduler ======================
    async def start_all_schedulers(self):
        for c in self.config.active_chats:
            if c not in self.schedulers:
                self.schedulers[c] = asyncio.create_task(self.mine_scheduler(c))

    async def mine_scheduler(self, chat_id: int):
        try:
            while chat_id in self.config.active_chats:
                now = datetime.now()
                next_t = self.last_execution.get(chat_id, now) + timedelta(seconds=self.config.interval)
                wait = (next_t - now).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)

                if chat_id not in self.config.active_chats:
                    break

                await self.send_keyword(chat_id)
                await asyncio.sleep(4)
                await self.execute_mine(chat_id)

                self.last_execution[chat_id] = datetime.now()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self.report_error(f"خطا در {chat_id}: {str(e)}")

    async def send_keyword(self, chat_id: int):
        try:
            await self.client.send_message(chat_id, self.config.keyword)
            await asyncio.sleep(1)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            await self.report_error(f"خطا ارسال کلمه: {str(e)}")

    # ====================== کلیک دکمه (همه پیام‌های ریپلای) ======================
    async def execute_mine(self, chat_id: int):
        try:
            target_message = None
            async for msg in self.client.iter_messages(chat_id, limit=100):
                if msg.sender_id != 6850835844:
                    continue
                if msg.is_reply() and msg.reply_to:
                    if "⛏️ درحال ماین کردن سنگ از معدن..." in msg.text:
                        target_message = msg
                        break
            if not target_message:
                return

            await asyncio.sleep(6)  # منتظر پیام بعدی بمان

            target_msg2 = None
            async for msg in self.client.iter_messages(chat_id, limit=100):
                if msg.sender_id != 6850835844:
                    continue
                if msg.reply_to and msg.reply_to.reply_to_msg_id == target_message.id:
                    if "✔️ شما با موفقیت یک عدد 🖤 بازالت را ماین کردید!" in msg.text:
                        target_msg2 = msg
                        break

            if target_msg2 and target_msg2.reply_markup and hasattr(target_msg2.reply_markup, 'rows'):
                for row in target_msg2.reply_markup.rows:
                    for btn in row.buttons:
                        try:
                            await target_msg2.click(data=getattr(btn, 'data', None))
                            return
                        except:
                            pass
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except RPCError as e:
            if "CHANNEL_PRIVATE" not in str(e):
                await self.report_error(f"خطا کلیک در {chat_id}: {str(e)}")
        except Exception:
            pass

    async def handle_reconnect(self):
        try:
            while True:
                if not self.client.is_connected():
                    await self.client.connect()
                    await self.load_config_from_saved_messages()
                    await self.start_all_schedulers()
                await asyncio.sleep(30)
        except:
            pass

# ============================================================
# Main
# ============================================================
async def main():
    print("🚀 Userbot Mine شروع شد...")
    api_id, api_hash, phone = API_ID, API_HASH, PHONE_NUMBER
    bot = UserBot(api_id, api_hash)
    reconnect = asyncio.create_task(bot.handle_reconnect())
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
