#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mine Telegram Userbot — production-oriented Railway worker.

A single-file Telethon userbot with:
- Real polling every 0.5 seconds for inline buttons.
- Independent scenarios per bot/chat/workflow.
- Backward-compatible Persian commands from the original version.
- Button and no-button modes.
- Persistent configuration in Telegram Saved Messages.
- Environment-only credentials; never hardcode secrets.

Run:
    python main.py
    python main.py --generate-session
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import (ChatWriteForbiddenError, FloodWaitError,
                             MessageNotModifiedError, SessionPasswordNeededError,
                             UserNotParticipantError)
from telethon.sessions import StringSession

try:
    from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
except ImportError:
    GetBotCallbackAnswerRequest = None

# ─────────────────────────────────────────────────────────────────────────────
# Runtime configuration
# ─────────────────────────────────────────────────────────────────────────────
STORAGE_TAG = "MINE_USERBOT_CONFIG_V3"
SESSION_FILE = "mine_session.txt"
MAX_RECENT_MESSAGES = 50
POLL_INTERVAL = 0.5
DEFAULT_INTERVAL_MINUTES = 3.0
DEFAULT_TIMEOUT = 15
DEFAULT_KEYWORD = "ماین"
DEFAULT_BUTTON = "بفروش بره"

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("mine-userbot")


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"متغیر محیطی {name} تنظیم نشده است: {name}")
    return value


API_ID = int(required_env("API_ID"))
API_HASH = required_env("API_HASH")
PHONE = os.getenv("PHONE", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()


def normalize(text: str | None) -> str:
    """Normalize Unicode, remove emoji noise and collapse whitespace."""
    value = unicodedata.normalize("NFKC", text or "")
    value = re.sub(r"[\U00010000-\U0010FFFF\U00002600-\U000027BF\u200d\ufe0f]", "", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def button_matches(button_text: str | None, target: str | None) -> bool:
    a, b = normalize(button_text), normalize(target)
    return bool(a and b and (a == b or a in b or b in a))


def parse_duration(value: str, unit: str) -> int | None:
    """Parse the original 3:50M / 2:30H syntax."""
    try:
        first, second = (int(x) for x in value.split(":", 1))
        if first < 0 or second < 0 or second >= 60:
            return None
        if unit.upper() == "M":
            return first * 60 + second
        if unit.upper() == "H":
            return first * 3600 + second * 60
    except (TypeError, ValueError):
        return None
    return None


@dataclass
class Scenario:
    name: str
    chats: list[int] = field(default_factory=list)
    keyword: str = DEFAULT_KEYWORD
    button: str = DEFAULT_BUTTON
    interval_minutes: float = DEFAULT_INTERVAL_MINUTES
    timeout: int = DEFAULT_TIMEOUT
    enabled: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Scenario":
        return cls(
            name=str(raw.get("name", "default")),
            chats=[int(x) for x in raw.get("chats", [])],
            keyword=str(raw.get("keyword", DEFAULT_KEYWORD)),
            button=str(raw.get("button", "")),
            # Backward compatibility: old configs stored interval in seconds.
            interval_minutes=max(0.1, float(raw.get("interval_minutes", float(raw.get("interval", DEFAULT_INTERVAL_MINUTES * 60)) / 60))),
            timeout=max(1, int(raw.get("timeout", DEFAULT_TIMEOUT))),
            enabled=bool(raw.get("enabled", False)),
        )


class Config:
    def __init__(self) -> None:
        self.scenarios: dict[str, Scenario] = {}
        self.storage_msg_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"version": 4, "scenarios": {name: asdict(s) for name, s in self.scenarios.items()}}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        config = cls()
        for name, value in raw.get("scenarios", {}).items():
            scenario = Scenario.from_dict(value)
            scenario.name = name
            config.scenarios[name] = scenario
        return config


class MineUserbot:
    def __init__(self) -> None:
        self.client: TelegramClient | None = None
        self.owner_id = 0
        self.config = Config()
        self.tasks: dict[tuple[str, int], asyncio.Task] = {}
        self.locks: dict[tuple[str, int], asyncio.Lock] = {}
        self.next_runs: dict[tuple[str, int], float] = {}
        self.config_lock = asyncio.Lock()

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle and persistence
    # ─────────────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        await self.init_client()
        await self.load_config()
        self.register_handlers()
        self.start_enabled()
        log.info("Userbot online; %d scenario(s) loaded", len(self.config.scenarios))
        await self.client.run_until_disconnected()  # type: ignore[union-attr]

    async def init_client(self) -> None:
        if SESSION_STRING:
            session = StringSession(SESSION_STRING)
        elif os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, encoding="utf-8") as file:
                session = StringSession(file.read().strip())
        else:
            session = StringSession()
        self.client = TelegramClient(session, API_ID, API_HASH)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            if not PHONE:
                raise RuntimeError("برای ورود اول، PHONE لازم است")
            await self.client.send_code_request(PHONE)
            code = input("کد تلگرام: ").strip()
            try:
                await self.client.sign_in(PHONE, code)
            except SessionPasswordNeededError:
                await self.client.sign_in(password=input("رمز دومرحله‌ای: "))
            with open(SESSION_FILE, "w", encoding="utf-8") as file:
                file.write(self.client.session.save())
        self.owner_id = (await self.client.get_me()).id

    async def load_config(self) -> None:
        assert self.client
        async for message in self.client.iter_messages("me", limit=50):
            if not message.text or STORAGE_TAG not in message.text:
                continue
            found = re.search(r"```json\s*(\{.*?\})\s*```", message.text, re.S)
            if found:
                self.config = Config.from_dict(json.loads(found.group(1)))
                self.config.storage_msg_id = message.id
                return
        await self.save_config()

    async def save_config(self) -> None:
        assert self.client
        text = f"{STORAGE_TAG}\n\n```json\n{json.dumps(self.config.to_dict(), ensure_ascii=False, indent=2)}\n```"
        async with self.config_lock:
            try:
                if self.config.storage_msg_id:
                    await self.client.edit_message("me", self.config.storage_msg_id, text)
                else:
                    message = await self.client.send_message("me", text)
                    self.config.storage_msg_id = message.id
            except MessageNotModifiedError:
                pass

    async def report(self, text: str) -> None:
        assert self.client
        await self.client.send_message("me", text)

    # ─────────────────────────────────────────────────────────────────────────
    # Commands: original + new scenario management
    # ─────────────────────────────────────────────────────────────────────────
    def register_handlers(self) -> None:
        assert self.client

        @self.client.on(events.NewMessage(pattern=r"^\."))
        async def command_handler(event: events.NewMessage.Event) -> None:
            if event.sender_id != self.owner_id:
                return
            try:
                response = await self.handle_command(event.text.strip(), event.chat_id)
                if response:
                    await self.report(response)
            except Exception as exc:
                log.exception("Command failed")
                await self.report(f"❌ خطا: {type(exc).__name__}: {exc}")

    def help_text(self) -> str:
        return """راهنمای Userbot Mine

فرمان‌های نسخهٔ اصلی:
• .ماین روشن       فعال‌سازی در گروه فعلی
• .ماین خاموش      توقف در گروه فعلی
• .ماین وضعیت      نمایش وضعیت
• .ماین تست        تست دکمه در گروه فعلی
• .ماین زمان 3:50M   تنظیم سازگار با نسخهٔ قدیمی؛ ۳ دقیقه و ۵۰ ثانیه
• .ماین زمان 2:30H   تنظیم سازگار با نسخهٔ قدیمی؛ ۲ ساعت و ۳۰ دقیقه
• .کلمه متن        تغییر کلمهٔ سناریوی پیش‌فرض
• .دکمه متن        تغییر دکمهٔ سناریوی پیش‌فرض

مدیریت سناریوهای مستقل:
• .سناریو افزودن نام
• .سناریو تنظیم نام | کلمه | دکمه | فاصله‌دقیقه | chat_id
• .سناریو روشن نام
• .سناریو خاموش نام
• .سناریو حذف نام
• .سناریوها

فاصلهٔ همهٔ سناریوها برحسب دقیقه است؛ اعشار هم مجاز است، مثل `0.5` دقیقه. برای حالت بدون دکمه، ستون دکمه را خالی بگذارید:
.سناریو تنظیم فقط-ارسال | /start |  | 1 | -1001234567890

تنظیمات در Saved Messages ذخیره می‌شود."""

    def default_scenario(self) -> Scenario:
        if "default" not in self.config.scenarios:
            self.config.scenarios["default"] = Scenario("default")
        return self.config.scenarios["default"]

    async def handle_command(self, text: str, chat_id: int) -> str:
        if text in (".راهنما", ".help", ".start"):
            return self.help_text()
        if text == ".سناریوها":
            return self.list_scenarios()

        match = re.fullmatch(r"\.سناریو افزودن\s+(.+)", text)
        if match:
            name = match.group(1).strip()
            if name in self.config.scenarios:
                return "❌ این سناریو از قبل وجود دارد."
            self.config.scenarios[name] = Scenario(name)
            await self.save_config()
            return f"✅ سناریو ساخته شد: {name}\nحالا آن را تنظیم و روشن کنید."

        match = re.fullmatch(r"\.سناریو حذف\s+(.+)", text)
        if match:
            name = match.group(1).strip()
            if name not in self.config.scenarios:
                return "❌ سناریو پیدا نشد."
            self.stop_scenario(name)
            del self.config.scenarios[name]
            await self.save_config()
            return f"✅ سناریو حذف شد: {name}"

        match = re.fullmatch(r"\.سناریو تنظیم\s+(.+?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\d+(?:[.,]\d+)?)\s*\|\s*(-?\d+)", text)
        if match:
            name, keyword, button, interval, target_chat = match.groups()
            if name not in self.config.scenarios:
                return "❌ ابتدا سناریو را بسازید: .سناریو افزودن نام"
            scenario = self.config.scenarios[name]
            scenario.keyword = keyword or DEFAULT_KEYWORD
            scenario.button = button
            scenario.interval_minutes = max(0.1, float(interval.replace(",", ".")))
            if int(target_chat) not in scenario.chats:
                scenario.chats.append(int(target_chat))
            await self.save_config()
            return f"✅ تنظیم شد: {name}\nکلمه: {scenario.keyword}\nدکمه: {scenario.button or 'بدون دکمه'}\nفاصله: {scenario.interval_minutes:g} دقیقه\nگروه: {target_chat}"

        match = re.fullmatch(r"\.سناریو (روشن|خاموش)\s+(.+)", text)
        if match:
            action, name = match.group(1), match.group(2).strip()
            if name not in self.config.scenarios:
                return "❌ سناریو پیدا نشد."
            scenario = self.config.scenarios[name]
            scenario.enabled = action == "روشن"
            if scenario.enabled:
                self.start_scenario(scenario)
            else:
                self.stop_scenario(name)
            await self.save_config()
            return f"✅ سناریو «{name}» {action} شد."

        # Backward-compatible original commands operate on the default scenario.
        default = self.default_scenario()
        if text == ".ماین روشن":
            if chat_id > 0:
                return "❌ این فرمان را داخل گروه اجرا کنید."
            if chat_id not in default.chats:
                default.chats.append(chat_id)
            default.enabled = True
            self.start_scenario(default)
            await self.save_config()
            return "✅ Mine در این گروه روشن شد."
        if text == ".ماین خاموش":
            default.chats = [x for x in default.chats if x != chat_id]
            self.stop_pair("default", chat_id)
            await self.save_config()
            return "✅ Mine در این گروه خاموش شد."
        if text == ".ماین وضعیت":
            return self.status_text()
        if text == ".ماین تست":
            return await self.test_default(chat_id)
        match = re.fullmatch(r"\.ماین زمان\s+(\d+:\d+)([MH])", text)
        if match:
            seconds = parse_duration(match.group(1), match.group(2))
            if not seconds:
                return "❌ زمان نامعتبر است. نمونه: .ماین زمان 3:50M"
            default.interval_minutes = seconds / 60
            await self.save_config()
            self.restart_enabled()
            return f"✅ فاصلهٔ Mine روی {default.interval_minutes:g} دقیقه تنظیم شد."
        match = re.fullmatch(r"\.کلمه\s+(.+)", text)
        if match:
            default.keyword = match.group(1).strip()
            await self.save_config()
            return f"✅ کلمهٔ پیش‌فرض: {default.keyword}"
        match = re.fullmatch(r"\.دکمه\s+(.+)", text)
        if match:
            default.button = match.group(1).strip()
            await self.save_config()
            return f"✅ دکمهٔ پیش‌فرض: {default.button}"
        return "❌ فرمان ناشناخته است. برای راهنما: .راهنما"

    def list_scenarios(self) -> str:
        if not self.config.scenarios:
            return "هیچ سناریویی ثبت نشده است."
        lines = ["📋 سناریوها:"]
        for s in self.config.scenarios.values():
            lines.append(f"• {s.name} | {'روشن' if s.enabled else 'خاموش'} | کلمه={s.keyword} | دکمه={s.button or 'ندارد'} | فاصله={s.interval_minutes:g} دقیقه | chats={s.chats}")
        return "\n".join(lines)

    def status_text(self) -> str:
        default = self.default_scenario()
        return f"📊 وضعیت Mine\nکلمه: {default.keyword}\nدکمه: {default.button or 'بدون دکمه'}\nفاصله: {default.interval_minutes:g} دقیقه\nگروه‌ها: {default.chats or 'هیچ'}\nفعال: {'بله' if default.enabled else 'خیر'}"

    async def test_default(self, chat_id: int) -> str:
        scenario = self.default_scenario()
        if chat_id > 0:
            return "❌ تست فقط داخل گروه انجام می‌شود."
        if not scenario.button:
            return "ℹ️ این سناریو بدون دکمه است؛ ارسال کلمه تست نمی‌شود."
        ok = await self.find_and_click(chat_id, scenario.button)
        return "✅ تست دکمه موفق بود." if ok else "❌ دکمه در ۵۰ پیام اخیر پیدا نشد."

    # ─────────────────────────────────────────────────────────────────────────
    # Independent task management and polling
    # ─────────────────────────────────────────────────────────────────────────
    def start_enabled(self) -> None:
        for scenario in self.config.scenarios.values():
            if scenario.enabled:
                self.start_scenario(scenario)

    def start_scenario(self, scenario: Scenario) -> None:
        for chat_id in scenario.chats:
            key = (scenario.name, chat_id)
            if key not in self.tasks or self.tasks[key].done():
                self.tasks[key] = asyncio.create_task(self.scheduler(scenario, chat_id))

    def stop_pair(self, name: str, chat_id: int) -> None:
        key = (name, chat_id)
        task = self.tasks.pop(key, None)
        self.next_runs.pop(key, None)
        if task and not task.done():
            task.cancel()

    def stop_scenario(self, name: str) -> None:
        for scenario_name, chat_id in list(self.tasks):
            if scenario_name == name:
                self.stop_pair(scenario_name, chat_id)

    def restart_enabled(self) -> None:
        for name, chat_id in list(self.tasks):
            self.stop_pair(name, chat_id)
        self.start_enabled()

    async def scheduler(self, scenario: Scenario, chat_id: int) -> None:
        while scenario.enabled and chat_id in scenario.chats:
            started = time.monotonic()
            try:
                await self.execute_once(scenario, chat_id)
            except asyncio.CancelledError:
                return
            except FloodWaitError as exc:
                log.warning("FloodWait %ss for %s/%s", exc.seconds, scenario.name, chat_id)
                await asyncio.sleep(exc.seconds + 1)
            except (ChatWriteForbiddenError, UserNotParticipantError):
                log.error("No access to %s/%s", scenario.name, chat_id)
                scenario.chats = [x for x in scenario.chats if x != chat_id]
                await self.save_config()
                break
            except Exception:
                log.exception("Scenario failed: %s/%s", scenario.name, chat_id)
            # Keep a stable monotonic timetable per task. If polling takes time,
            # skip missed slots instead of firing several late executions together.
            period = scenario.interval_minutes * 60.0
            key = (scenario.name, chat_id)
            next_run = self.next_runs.get(key, started + period)
            while next_run <= time.monotonic():
                next_run += period
            self.next_runs[key] = next_run
            await asyncio.sleep(max(0.1, next_run - time.monotonic()))

    async def execute_once(self, scenario: Scenario, chat_id: int) -> None:
        assert self.client
        key = (scenario.name, chat_id)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self.client.send_message(chat_id, scenario.keyword)
            if not scenario.button:
                return
            # Real polling path: 0.5s cadence, newest 50 messages each pass.
            for _ in range(max(1, int(scenario.timeout / POLL_INTERVAL))):
                await asyncio.sleep(POLL_INTERVAL)
                if await self.find_and_click(chat_id, scenario.button):
                    return

    async def find_and_click(self, chat_id: int, target: str) -> bool:
        assert self.client
        async for message in self.client.iter_messages(chat_id, limit=MAX_RECENT_MESSAGES):
            markup = message.reply_markup
            if not markup or not hasattr(markup, "rows"):
                continue
            for row_index, row in enumerate(markup.rows):
                for column_index, button in enumerate(getattr(row, "buttons", [])):
                    if button_matches(getattr(button, "text", ""), target):
                        return await self.try_click(message, row_index, column_index, button, target)
        return False

    async def try_click(self, message: Any, row: int, column: int, button: Any, target: str) -> bool:
        assert self.client
        data = getattr(button, "data", None)
        methods: list[tuple[str, Any]] = []
        if data and GetBotCallbackAnswerRequest:
            methods.append(("raw", lambda: self.client(GetBotCallbackAnswerRequest(peer=self.client.get_input_entity(message.chat_id), msg_id=message.id, data=data))))
        if data:
            methods.append(("data", lambda: message.click(data=data)))
        methods.extend([("position", lambda: message.click(row, column)), ("text", lambda: message.click(text=target))])
        for name, method in methods:
            try:
                await method()
                log.debug("Button clicked using %s", name)
                return True
            except Exception:
                continue
        return False


async def generate_session() -> None:
    api_id = int(input("API ID: ").strip())
    api_hash = input("API Hash: ").strip()
    phone = input("Phone: ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)
    print("\nSESSION_STRING:\n" + client.session.save())
    await client.disconnect()


async def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--generate-session":
        await generate_session()
    else:
        await MineUserbot().run()


if __name__ == "__main__":
    asyncio.run(main())
