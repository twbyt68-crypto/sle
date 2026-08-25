#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Central Telegram control plane with real Telethon Session workers.

The Telegram Bot API process is a button-based administration panel. Every
operational account is an independent Telethon client. The panel never sends a
text command to a target group: it writes a scenario into SQLite, and the
corresponding worker reads it and performs the configured action.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import requests
from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient
from telethon.errors import (ChatWriteForbiddenError, FloodWaitError,
                             MessageNotModifiedError, SessionPasswordNeededError,
                             UserNotParticipantError)
from telethon.sessions import StringSession

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("telegram-control-plane")

MANAGER_BOT_TOKEN = os.getenv("MANAGER_BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
FERNET_KEY = os.getenv("FERNET_KEY", "").strip()
DB_PATH = os.getenv("DB_PATH", "manager.sqlite3")
POLL_INTERVAL = max(0.1, float(os.getenv("POLL_INTERVAL", "0.5")))
RECENT_MESSAGES = 50
DEFAULT_INTERVAL_MINUTES = 3.0
DEFAULT_TIMEOUT_SECONDS = 15.0
if not MANAGER_BOT_TOKEN or not ADMIN_IDS or not API_ID or not API_HASH or not FERNET_KEY:
    raise RuntimeError("MANAGER_BOT_TOKEN, ADMIN_IDS, API_ID, API_HASH and FERNET_KEY are required")
try:
    CRYPTO = Fernet(FERNET_KEY.encode())
except Exception as exc:
    raise RuntimeError("FERNET_KEY is not a valid Fernet key") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: Any) -> str:
    value = "" if value is None else str(value)
    value = value.replace("\x00", "")
    return re.sub(r"\s+", " ", value).strip()


def normalize(value: Any) -> str:
    return clean(value).casefold()


def button_matches(value: Any, target: Any) -> bool:
    left, right = normalize(value), normalize(target)
    return bool(left and right and (left == right or left in right or right in left))


def number(value: str, minimum: float = 0.1) -> float:
    parsed = float(clean(value).replace(",", "."))
    if parsed < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return parsed


def safe_html(value: Any) -> str:
    return html.escape(clean(value))


def secret_preview(value: str) -> str:
    if not value:
        return "خالی"
    return value[:3] + "…" + value[-3:]


class Store:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            session_blob TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_status TEXT NOT NULL DEFAULT 'new',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scenarios(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            button TEXT NOT NULL DEFAULT '',
            interval_minutes REAL NOT NULL DEFAULT 3,
            timeout_seconds REAL NOT NULL DEFAULT 15,
            enabled INTEGER NOT NULL DEFAULT 0,
            last_run TEXT NOT NULL DEFAULT '',
            last_result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scenarios_account ON scenarios(account_id);
        CREATE INDEX IF NOT EXISTS idx_scenarios_enabled ON scenarios(enabled);
        """)
        self.db.commit()

    def execute(self, sql: str, params: tuple = ()):
        with self.lock:
            cursor = self.db.execute(sql, params)
            self.db.commit()
            return cursor

    def one(self, sql: str, params: tuple = ()):
        with self.lock:
            return self.db.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()):
        with self.lock:
            return self.db.execute(sql, params).fetchall()

    def audit(self, admin: int, action: str, details: str = ""):
        self.execute("INSERT INTO audit(admin_id,action,details,created_at) VALUES(?,?,?,?)", (admin, action, details, utc_now()))

    def setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def accounts(self): return self.all("SELECT * FROM accounts ORDER BY id DESC")
    def scenarios(self): return self.all("SELECT s.*,a.name account_name FROM scenarios s JOIN accounts a ON a.id=s.account_id ORDER BY s.id DESC")
    def counts(self) -> dict[str, int]:
        return {"accounts": self.one("SELECT COUNT(*) FROM accounts")[0], "scenarios": self.one("SELECT COUNT(*) FROM scenarios")[0], "enabled": self.one("SELECT COUNT(*) FROM scenarios WHERE enabled=1")[0]}
    def account_names(self) -> list[str]:
        return [row["name"] for row in self.accounts()]
    def scenario_names(self) -> list[str]:
        return [row["name"] for row in self.scenarios()]
    def close(self):
        with self.lock: self.db.close()
    def account_count(self) -> int:
        return int(self.one("SELECT COUNT(*) FROM accounts")[0])
    def scenario_count(self) -> int:
        return int(self.one("SELECT COUNT(*) FROM scenarios")[0])
    def enabled_scenario_count(self) -> int:
        return int(self.one("SELECT COUNT(*) FROM scenarios WHERE enabled=1")[0])
    def recent_audit(self, limit: int = 20):
        return self.all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(100, limit)),))
    def account(self, ident: int): return self.one("SELECT * FROM accounts WHERE id=?", (ident,))
    def scenario(self, ident: int): return self.one("SELECT * FROM scenarios WHERE id=?", (ident,))

    def create_account(self, name: str, session: str) -> int:
        now = utc_now(); blob = CRYPTO.encrypt(session.encode()).decode()
        return self.execute("INSERT INTO accounts(name,session_blob,created_at,updated_at) VALUES(?,?,?,?,?)", (name, blob, now, now)).lastrowid

    def update_account(self, ident: int, name: str, session: str | None = None):
        if session:
            blob = CRYPTO.encrypt(session.encode()).decode()
            self.execute("UPDATE accounts SET name=?,session_blob=?,updated_at=? WHERE id=?", (name, blob, utc_now(), ident))
        else:
            self.execute("UPDATE accounts SET name=?,updated_at=? WHERE id=?", (name, utc_now(), ident))

    def set_account_enabled(self, ident: int, enabled: bool):
        self.execute("UPDATE accounts SET enabled=?,updated_at=? WHERE id=?", (int(enabled), utc_now(), ident))

    def set_account_status(self, ident: int, status: str, error: str = ""):
        self.execute("UPDATE accounts SET last_status=?,last_error=?,updated_at=? WHERE id=?", (status, error[:1000], utc_now(), ident))

    def delete_account(self, ident: int): self.execute("DELETE FROM accounts WHERE id=?", (ident,))

    def create_scenario(self, name: str, account_id: int, chat_id: int, keyword: str, button: str, minutes: float, timeout: float) -> int:
        now = utc_now()
        return self.execute("INSERT INTO scenarios(name,account_id,chat_id,keyword,button,interval_minutes,timeout_seconds,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (name, account_id, chat_id, keyword, button, minutes, timeout, now, now)).lastrowid

    def update_scenario(self, ident: int, name: str, account_id: int, chat_id: int, keyword: str, button: str, minutes: float, timeout: float):
        self.execute("UPDATE scenarios SET name=?,account_id=?,chat_id=?,keyword=?,button=?,interval_minutes=?,timeout_seconds=?,updated_at=? WHERE id=?", (name, account_id, chat_id, keyword, button, minutes, timeout, utc_now(), ident))

    def set_scenario_enabled(self, ident: int, enabled: bool): self.execute("UPDATE scenarios SET enabled=?,updated_at=? WHERE id=?", (int(enabled), utc_now(), ident))
    def delete_scenario(self, ident: int): self.execute("DELETE FROM scenarios WHERE id=?", (ident,))
    def mark_scenario(self, ident: int, result: str): self.execute("UPDATE scenarios SET last_run=?,last_result=?,updated_at=? WHERE id=?", (utc_now(), result[:500], utc_now(), ident))

    def export_json(self) -> str:
        data = {"version": 5, "exported_at": utc_now(), "accounts": [], "scenarios": [], "settings": {}}
        for row in self.accounts():
            data["accounts"].append({"id": row["id"], "name": row["name"], "enabled": bool(row["enabled"]), "last_status": row["last_status"], "created_at": row["created_at"]})
        for row in self.scenarios(): data["scenarios"].append(dict(row))
        for row in self.all("SELECT key,value FROM settings"): data["settings"][row["key"]] = row["value"]
        return json.dumps(data, ensure_ascii=False, indent=2)


class UserWorker:
    def __init__(self, account: sqlite3.Row, store: Store):
        self.account = account; self.store = store; self.client: TelegramClient | None = None
        self.tasks: dict[int, asyncio.Task] = {}; self.next_runs: dict[int, float] = {}
        self.stop_flag = False

    def session_string(self) -> str:
        try: return CRYPTO.decrypt(self.account["session_blob"].encode()).decode()
        except InvalidToken as exc: raise RuntimeError("encrypted Session cannot be decrypted") from exc

    async def connect(self):
        self.client = TelegramClient(StringSession(self.session_string()), API_ID, API_HASH, request_retries=5, connection_retries=5, auto_reconnect=True, sequential_updates=False)
        await self.client.connect()
        if not await self.client.is_user_authorized(): raise RuntimeError("Session is not authorized")

    async def run(self):
        try:
            await self.connect(); self.store.set_account_status(self.account["id"], "online")
            while not self.stop_flag:
                self.sync_tasks(); await asyncio.sleep(2)
        except asyncio.CancelledError: pass
        except Exception as exc:
            self.store.set_account_status(self.account["id"], "error", str(exc)); log.exception("worker %s stopped", self.account["name"])
        finally:
            if self.client: await self.client.disconnect()

    def sync_tasks(self):
        active = {row["id"]: row for row in self.store.all("SELECT * FROM scenarios WHERE account_id=? AND enabled=1", (self.account["id"],))}
        for ident, task in list(self.tasks.items()):
            if ident not in active: task.cancel(); self.tasks.pop(ident, None); self.next_runs.pop(ident, None)
        for ident, row in active.items():
            if ident not in self.tasks or self.tasks[ident].done(): self.tasks[ident] = asyncio.create_task(self.schedule(row))

    async def schedule(self, row: sqlite3.Row):
        ident = row["id"]; period = max(6.0, float(row["interval_minutes"]) * 60); next_run = time.monotonic()
        while not self.stop_flag:
            try:
                await self.execute_once(row)
            except asyncio.CancelledError: return
            except FloodWaitError as exc:
                self.store.mark_scenario(ident, f"FloodWait {exc.seconds}s"); await asyncio.sleep(exc.seconds + 1)
            except (ChatWriteForbiddenError, UserNotParticipantError) as exc:
                self.store.mark_scenario(ident, f"access error: {type(exc).__name__}"); return
            except Exception as exc:
                self.store.mark_scenario(ident, f"error: {type(exc).__name__}: {exc}")
            next_run += period
            while next_run <= time.monotonic(): next_run += period
            self.next_runs[ident] = next_run
            await asyncio.sleep(max(0.1, next_run - time.monotonic()))

    async def execute_once(self, row: sqlite3.Row) -> bool:
        assert self.client
        await self.client.send_message(int(row["chat_id"]), row["keyword"])
        if not row["button"]: self.store.mark_scenario(row["id"], "sent-no-button"); return True
        attempts = max(1, int(float(row["timeout_seconds"]) / POLL_INTERVAL))
        for _ in range(attempts):
            await asyncio.sleep(POLL_INTERVAL)
            if await self.find_and_click(int(row["chat_id"]), row["button"]): self.store.mark_scenario(row["id"], "clicked"); return True
        self.store.mark_scenario(row["id"], "button-not-found"); return False

    async def find_and_click(self, chat_id: int, target: str) -> bool:
        assert self.client
        async for message in self.client.iter_messages(chat_id, limit=RECENT_MESSAGES):
            markup = message.reply_markup
            if not markup or not hasattr(markup, "rows"): continue
            for ri, row in enumerate(markup.rows):
                for ci, button in enumerate(getattr(row, "buttons", [])):
                    if button_matches(getattr(button, "text", ""), target):
                        return await self.click(message, ri, ci, button, target)
        return False

    async def click(self, message: Any, ri: int, ci: int, button: Any, target: str) -> bool:
        data = getattr(button, "data", None)
        if data:
            try: await message.click(data=data); return True
            except Exception: pass
        for action in (lambda: message.click(ri, ci), lambda: message.click(text=target)):
            try: await action(); return True
            except Exception: pass
        return False

    async def test(self, chat_id: int, keyword: str, button: str) -> str:
        assert self.client
        await self.client.send_message(chat_id, keyword)
        if not button: return "sent-no-button"
        return "clicked" if await self.find_and_click(chat_id, button) else "button-not-found"


class WorkerHub:
    def __init__(self, store: Store): self.store=store; self.threads={}; self.workers={}; self.lock=threading.RLock()
    def start_all(self):
        for row in self.store.accounts():
            if row["enabled"]: self.start(row["id"])
    def start(self, account_id: int):
        with self.lock:
            if account_id in self.threads and self.threads[account_id].is_alive(): return
            row=self.store.account(account_id)
            if not row or not row["enabled"]: return
            def runner():
                worker=UserWorker(row,self.store); self.workers[account_id]=worker
                try: asyncio.run(worker.run())
                finally: self.workers.pop(account_id,None)
            thread=threading.Thread(target=runner, daemon=True, name=f"telethon-{account_id}"); self.threads[account_id]=thread; thread.start()
    def stop(self, account_id: int):
        worker=self.workers.get(account_id)
        if worker: worker.stop_flag=True
    def restart(self, account_id: int): self.stop(account_id); time.sleep(0.2); self.start(account_id)
    def online(self, account_id: int) -> bool: return account_id in self.workers
    def run_test(self, account_id: int, chat_id: int, keyword: str, button: str) -> str:
        worker=self.workers.get(account_id)
        if not worker or not worker.client: return "worker-offline"
        future=asyncio.run_coroutine_threadsafe(worker.test(chat_id,keyword,button), worker.client.loop)
        try: return future.result(timeout=60)
        except Exception as exc: return f"error:{type(exc).__name__}"


@dataclass
class Flow:
    kind: str
    prompts: list[str]
    values: list[str] = field(default_factory=list)
    index: int = 0
    edit_id: int | None = None


class ManagerBot:
    def __init__(self):
        self.api=f"https://api.telegram.org/bot{MANAGER_BOT_TOKEN}"; self.store=Store(); self.hub=WorkerHub(self.store); self.flows={}; self.offset=0

    def api_call(self, method: str, **payload):
        response=requests.post(f"{self.api}/{method}",json=payload,timeout=40); response.raise_for_status(); data=response.json()
        if not data.get("ok"): raise RuntimeError(data.get("description","Telegram API error"))
        return data["result"]
    def button(self,text,data): return {"text":text,"callback_data":data}
    def button_row(self,*items): return [self.button(text,data) for text,data in items]
    def safe_button(self,text,data): return self.button(clean(text)[:32], clean(data)[:64])
    def send(self,chat,text,keys=None,edit=None):
        payload={"chat_id":chat,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
        if keys is not None: payload["reply_markup"]={"inline_keyboard":keys}
        if edit: payload["message_id"]=edit; return self.api_call("editMessageText",**payload)
        return self.api_call("sendMessage",**payload)
    def answer(self,qid,text=""): self.api_call("answerCallbackQuery",callback_query_id=qid,text=text[:190])
    def back(self,data="home"): return [[self.button("↩️ بازگشت",data)]]

    def home(self,chat,edit=None):
        keys=[[self.button("👤 حساب‌ها","accounts"),self.button("⚙️ سناریوها","scenarios")],[self.button("🧪 تست اتصال","test_manager"),self.button("📊 وضعیت","status")],[self.button("🧰 ابزارها","tools"),self.button("🧾 گزارش","audit")],[self.button("❔ راهنما","help")]]
        self.send(chat,"<b>مرکز مدیریت Session</b>\n\nحساب‌های عملیاتی و سناریوهای زمان‌بندی‌شده را از همین پنل کنترل کنید.",keys,edit)
    def page(self,chat,name,edit=None):
        if name=="accounts":
            keys=[[self.button("➕ افزودن Session","add_account")],[self.button("📋 فهرست و وضعیت","list_accounts")],[self.button("✏️ ویرایش","edit_account")],[self.button("🔄 روشن/خاموش","toggle_account")],[self.button("🗑 حذف","delete_account")]]; text="<b>👤 حساب‌های عملیاتی</b>\nSession رمزنگاری‌شده نگهداری می‌شود."
        elif name=="scenarios":
            keys=[[self.button("➕ افزودن سناریو","add_scenario")],[self.button("📋 فهرست","list_scenarios")],[self.button("✏️ ویرایش","edit_scenario")],[self.button("▶️ روشن/خاموش","toggle_scenario")],[self.button("🗑 حذف","delete_scenario")]]; text="<b>⚙️ سناریوها</b>\nفاصلهٔ همهٔ سناریوها برحسب دقیقه است."
        elif name=="tools":
            keys=[[self.button("⏱ فاصلهٔ polling","set_poll")],[self.button("⏳ timeout پیش‌فرض","set_timeout")],[self.button("💾 پشتیبان متنی","backup")],[self.button("🧹 پاک‌سازی گزارش","clear_audit")]]; text="<b>🧰 ابزارها</b>"
        elif name=="status": return self.status(chat,edit)
        elif name=="audit": return self.audit(chat,edit)
        elif name=="help": return self.send(chat,self.help_text(),self.back(),edit)
        else: return self.home(chat,edit)
        self.send(chat,text,keys+[self.back()[0]],edit)
    def help_text(self):
        return """<b>راهنمای پنل مدیریت</b>\n\n<b>حساب عملیاتی</b>\nاز بخش حساب‌ها یک Session String کامل Telethon را در گفت‌وگوی خصوصی وارد کنید. این Session متعلق به حساب Telegram است و پس از رمزنگاری در دیتابیس ذخیره می‌شود.\n\n<b>سناریو</b> هر سناریو حساب، chat_id، کلمه، دکمهٔ اختیاری، فاصلهٔ دقیقه‌ای و timeout دارد. اگر متن دکمه خالی باشد فقط کلمه ارسال می‌شود. اگر دکمه تعیین شود، Worker بعد از ارسال کلمه هر نیم‌ثانیه پیام‌های اخیر را بررسی می‌کند.\n\n<b>زمان‌بندی</b> فاصلهٔ سناریوها دقیقه‌ای است؛ 1 یعنی یک دقیقه و 0.5 یعنی ۳۰ ثانیه. زمان‌بندی هر سناریو مستقل است و اجرای طولانی یک سناریو، سناریوی دیگر را متوقف نمی‌کند.\n\n<b>دسترسی</b> حساب عملیاتی باید واقعاً عضو گروه باشد و دسترسی ارسال/خواندن/کلیک داشته باشد. پنل مدیر نمی‌تواند عضویت یا دسترسی را جعل کند.\n\n<b>امنیت</b> توکن Bot مدیر، API Hash، FERNET_KEY و Session را در چت عمومی منتشر نکنید. فقط ADMIN_IDS اجازهٔ استفاده از این پنل را دارند."""
    def status_snapshot(self) -> dict[str, Any]:
        accounts=self.store.accounts(); scenarios=self.store.scenarios()
        return {"accounts":len(accounts),"online_workers":sum(self.hub.online(a["id"]) for a in accounts),"scenarios":len(scenarios),"active_scenarios":sum(int(r["enabled"]) for r in scenarios),"poll_interval":POLL_INTERVAL,"database":DB_PATH}
    def status(self,chat,edit=None):
        snap=self.status_snapshot()
        text=f"<b>📊 وضعیت سیستم</b>\nحساب‌ها: {snap['accounts']}\nWorker آنلاین: {snap['online_workers']}\nسناریوها: {snap['scenarios']}\nسناریوهای فعال: {snap['active_scenarios']}\nPolling: {snap['poll_interval']:g} ثانیه\nDatabase: {safe_html(snap['database'])}"
        self.send(chat,text,self.back(),edit)
    def audit_rows(self, limit: int = 20):
        return self.store.all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1,min(limit,100)),))
    def audit(self,chat,edit=None):
        rows=self.audit_rows(); lines=[f"• <code>{safe_html(r['created_at'])}</code> — {safe_html(r['action'])} {safe_html(r['details'])}" for r in rows]
        self.send(chat,"<b>🧾 گزارش فعالیت</b>\n"+("\n".join(lines) or "موردی ثبت نشده است."),self.back(),edit)
    def begin(self,user,chat,kind,prompts,edit=None):
        self.flows[user]=Flow(kind,prompts,edit_id=edit); self.send(chat,f"<b>مرحلهٔ ۱ از {len(prompts)}</b>\n{prompts[0]}\n\nلغو: /cancel",[[self.button("❌ لغو","cancel")]])
    def prompt_next(self,user,chat):
        flow=self.flows[user]; self.send(chat,f"<b>مرحلهٔ {flow.index+1} از {len(flow.prompts)}</b>\n{flow.prompts[flow.index]}\n\nلغو: /cancel",[[self.button("❌ لغو","cancel")]])
    def receive_flow(self,user,chat,text):
        flow=self.flows[user]
        if text=="/cancel": self.flows.pop(user,None); return self.home(chat)
        flow.values.append(text.strip()); flow.index+=1
        if flow.index<len(flow.prompts): return self.prompt_next(user,chat)
        values=flow.values; self.flows.pop(user,None)
        try: result=self.commit_flow(user,flow.kind,values)
        except Exception as exc: log.exception("form commit failed"); result=f"❌ خطا: {safe_html(exc)}"
        self.send(chat,result,[[self.button("↩️ منوی اصلی","home")]])
    def commit_flow(self,user,kind,v):
        if kind=="add_account":
            ident=self.store.create_account(clean(v[0]),clean(v[1])); self.store.audit(user,"account-add",str(ident)); self.hub.start(ident); return "✅ حساب و Session ثبت شد و Worker در حال اتصال است."
        if kind=="edit_account":
            ident=int(v[0]); self.store.update_account(ident,clean(v[1]),clean(v[2]) or None); self.store.audit(user,"account-edit",str(ident)); self.hub.restart(ident); return "✅ حساب ویرایش شد."
        if kind in ("add_scenario","edit_scenario"):
            if kind == "add_scenario":
                name,aid,cid,keyword,button,minutes,timeout=clean(v[0]),int(v[1]),int(v[2]),clean(v[3]),clean(v[4]),number(v[5]),number(v[6],1)
                ident=self.store.create_scenario(name,aid,cid,keyword,button,minutes,timeout); action="scenario-add"
            else:
                ident=int(v[0]); name,aid,cid,keyword,button,minutes,timeout=clean(v[1]),int(v[2]),int(v[3]),clean(v[4]),clean(v[5]),number(v[6]),number(v[7],1)
                self.store.update_scenario(ident,name,aid,cid,keyword,button,minutes,timeout); action="scenario-edit"
            self.store.audit(user,action,str(ident)); self.hub.start(aid); return "✅ سناریو ذخیره شد. برای اجرای خودکار آن را روشن کنید."
        if kind=="set_poll": self.store.set_setting("poll_seconds",str(number(v[0]))); self.store.audit(user,"poll-change",v[0]); return "✅ فاصلهٔ polling ذخیره شد؛ مقدار محیطی فعلی پس از Restart اعمال می‌شود."
        if kind=="set_timeout": self.store.set_setting("timeout_seconds",str(number(v[0],1))); self.store.audit(user,"timeout-change",v[0]); return "✅ timeout پیش‌فرض ذخیره شد."
        return "ℹ️ عملیات ثبت شد."
    def list_accounts(self,chat,edit=None):
        rows=self.store.accounts(); lines=[]
        for r in rows: lines.append(f"<b>#{r['id']} {safe_html(r['name'])}</b> — {'روشن' if r['enabled'] else 'خاموش'} — {safe_html(r['last_status'])} — Session: {secret_preview(r['session_blob'])}")
        self.send(chat,"<b>👤 حساب‌ها</b>\n"+("\n".join(lines) or "موردی نیست"),self.back("accounts"),edit)
    def list_scenarios(self,chat,edit=None):
        rows=self.store.scenarios(); lines=[]
        for r in rows: lines.append(f"<b>#{r['id']} {safe_html(r['name'])}</b> — حساب {r['account_id']} — <code>{r['chat_id']}</code> — {r['interval_minutes']:g} دقیقه — {'روشن' if r['enabled'] else 'خاموش'} — {safe_html(r['last_result'])}")
        self.send(chat,"<b>⚙️ سناریوها</b>\n"+("\n".join(lines) or "موردی نیست"),self.back("scenarios"),edit)
    def callback(self,user,chat,data,msg,qid):
        try: self.answer(qid)
        except Exception: pass
        if user not in ADMIN_IDS: return
        if data=="home": return self.home(chat,msg)
        if data in {"accounts","scenarios","tools","status","audit","help"}: return self.page(chat,data,msg)
        if data=="test_manager":
            try: me=self.api_call("getMe"); self.send(chat,f"✅ اتصال مدیر موفق است: @{safe_html(me.get('username',''))}",self.back(),msg)
            except Exception as exc: self.send(chat,f"❌ اتصال ناموفق: {safe_html(exc)}",self.back(),msg)
            return
        if data=="list_accounts": return self.list_accounts(chat,msg)
        if data=="list_scenarios": return self.list_scenarios(chat,msg)
        if data=="add_account": return self.begin(user,chat,data,["نام نمایشی حساب","Session String کامل Telethon"],msg)
        if data=="edit_account": return self.begin(user,chat,data,["شناسه حساب","نام جدید","Session جدید؛ برای حفظ قبلی خالی بفرستید"],msg)
        if data=="add_scenario": return self.begin(user,chat,data,["نام سناریو","شناسه حساب","chat_id گروه مثل -1001234567890","کلمه یا فرمان","متن دکمه؛ بدون دکمه خالی بفرستید","فاصله برحسب دقیقه؛ مثل 1 یا 0.5","timeout برحسب ثانیه؛ مثل 15"],msg)
        if data=="edit_scenario": return self.begin(user,chat,data,["شناسه سناریو","نام جدید","شناسه حساب","chat_id گروه","کلمه یا فرمان","متن دکمه؛ بدون دکمه خالی","فاصله برحسب دقیقه","timeout برحسب ثانیه"],msg)
        if data=="set_poll": return self.begin(user,chat,data,["فاصله polling برحسب ثانیه؛ پیشنهاد 0.5"],msg)
        if data=="set_timeout": return self.begin(user,chat,data,["timeout پیش‌فرض برحسب ثانیه"],msg)
        if data=="toggle_account": return self.begin(user,chat,data,["شناسه حساب","روشن یا خاموش"],msg)
        if data=="delete_account": return self.begin(user,chat,data,["شناسه حساب برای حذف","برای تأیید، کلمه حذف را بنویسید"],msg)
        if data=="toggle_scenario": return self.begin(user,chat,data,["شناسه سناریو","روشن یا خاموش"],msg)
        if data=="delete_scenario": return self.begin(user,chat,data,["شناسه سناریو برای حذف","برای تأیید، کلمه حذف را بنویسید"],msg)
        if data=="backup": return self.send(chat,"<pre>"+safe_html(self.store.export_json())+"</pre>",self.back("tools"),msg)
        if data=="clear_audit": self.store.execute("DELETE FROM audit"); self.send(chat,"✅ گزارش پاک شد.",self.back("tools"),msg); return
        if data=="cancel": self.flows.pop(user,None); return self.home(chat,msg)
        self.send(chat,"❌ گزینه ناشناخته است.",self.back(),msg)
    def special_commit(self,user,kind,v):
        if kind=="toggle_account":
            ident=int(v[0]); enabled=normalize(v[1]) in ("روشن","on","1","yes"); self.store.set_account_enabled(ident,enabled); self.store.audit(user,"account-toggle",f"{ident}:{enabled}"); (self.hub.start if enabled else self.hub.stop)(ident); return "✅ وضعیت حساب تغییر کرد."
        if kind=="delete_account":
            if normalize(v[1])!="حذف": return "❌ تأیید حذف نامعتبر بود."
            ident=int(v[0]); self.hub.stop(ident); self.store.delete_account(ident); self.store.audit(user,"account-delete",str(ident)); return "✅ حساب و سناریوهای وابسته حذف شدند."
        if kind=="toggle_scenario":
            ident=int(v[0]); enabled=normalize(v[1]) in ("روشن","on","1","yes"); row=self.store.scenario(ident); self.store.set_scenario_enabled(ident,enabled); self.store.audit(user,"scenario-toggle",f"{ident}:{enabled}"); self.hub.start(row["account_id"]); return "✅ وضعیت سناریو تغییر کرد."
        if kind=="delete_scenario":
            if normalize(v[1])!="حذف": return "❌ تأیید حذف نامعتبر بود."
            ident=int(v[0]); self.store.delete_scenario(ident); self.store.audit(user,"scenario-delete",str(ident)); return "✅ سناریو حذف شد."
        return None
    def process_message(self,msg):
        user=msg.get("from",{}).get("id"); chat=msg.get("chat",{}).get("id"); text=msg.get("text","")
        if user not in ADMIN_IDS:return
        if text=="/start": return self.home(chat)
        if user in self.flows:
            flow=self.flows[user]
            if flow.kind in {"toggle_account","delete_account","toggle_scenario","delete_scenario"}:
                flow.values.append(text.strip()); flow.index+=1
                if flow.index<len(flow.prompts): return self.prompt_next(user,chat)
                self.flows.pop(user,None); result=self.special_commit(user,flow.kind,flow.values); return self.send(chat,result,self.back())
            return self.receive_flow(user,chat,text)
        return self.home(chat)
    def run(self):
        self.hub.start_all()
        try: self.api_call("deleteWebhook",drop_pending_updates=False)
        except Exception: log.exception("could not delete webhook")
        log.info("manager online")
        while True:
            try:
                updates=self.api_call("getUpdates",offset=self.offset,timeout=25,allowed_updates=["message","callback_query"])
                for update in updates:
                    self.offset=update["update_id"]+1
                    if "callback_query" in update:
                        q=update["callback_query"]; message=q.get("message",{}); self.callback(q["from"]["id"],message.get("chat",{}).get("id"),q.get("data",""),message.get("message_id"),q["id"])
                    elif "message" in update: self.process_message(update["message"])
            except KeyboardInterrupt:return
            except Exception: log.exception("manager loop error"); time.sleep(3)


def main(): ManagerBot().run()
if __name__=="__main__": main()
