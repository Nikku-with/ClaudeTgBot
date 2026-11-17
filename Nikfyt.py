# ==============================================================================
#  NIKFYT ENGINE · V5 ULTIMATE  (10-BOT AI CLUSTER + NON-STOP 24/7 NC PIPELINE)
#  CREDITS : @Nikkkwa (Nik)   ·   aiogram 3.7+   ·   single-file multi-bot bot
# ------------------------------------------------------------------------------
#  CONFIG (sab kuch file ke andar — koi real token/ID hardcode nahi):
#    1) OWNER_ID    : apni numeric Telegram ID  (@userinfobot)
#    2) MAIN_TOKEN  : MAIN / hosting bot token  (@BotFather) = slot #1
#    3) EXTRA_TOKENS: 9 alag slots (#2..#10) — file me bharo YA bot se
#                     !addtoken <token> / !remtoken <no>
#    4) PREFIX      : default "!" — ~ aur / HAMESHA chalte hain.
#                     Badalne ke liye:  !prefix ~      (naya prefix ~)
# ------------------------------------------------------------------------------
#  Run          : python Nikfyt.py
#  Config check : python Nikfyt.py --check
# ==============================================================================
import asyncio
import gc
import io
import json
import os
import random
import resource
import sys
import time
import traceback
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Set, Tuple
import re
from html import escape as _hesc

# Linux VPS / Panels par uvloop auto-enable (low RAM + high speed)
with suppress(ImportError):
    import uvloop
    uvloop.install()

gc.set_threshold(400, 5, 5)

from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import (
    BufferedInputFile,
    ChatPermissions,
    Message,
    ReactionTypeEmoji,
)

try:  # aiogram 3.7+ (botpfp fix)
    from aiogram.types import InputProfilePhotoStatic
except ImportError:
    InputProfilePhotoStatic = None

# ==============================================================================
# NIK CONFIG
# ==============================================================================
CONFIG: Dict[str, Any] = {
    "OWNER_ID":   0,     # ← apni numeric ID
    "MAIN_TOKEN": "",    # ← MAIN/hosting token (#1)
}

EXTRA_TOKENS: List[str] = [""] * 9   # slots #2 .. #10

MAX_TOKENS = 10
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_FILE   = os.path.join(BASE_DIR, "nikfyt_runtime.json")
SUDO_FILE      = os.path.join(BASE_DIR, "nikfyt_sudo.json")
STATE_FILE     = os.path.join(BASE_DIR, "nikfyt_state.json")
TEMPLATE_FILE  = os.path.join(BASE_DIR, "templates.json")
CACHE_DIR      = os.path.join(BASE_DIR, "nikfyt_cache")
GAMEOVER_FILE  = os.path.join(CACHE_DIR, "gameover.mp4")
os.makedirs(CACHE_DIR, exist_ok=True)


def _clean_tok(t) -> str:
    return ("" if t is None else str(t)).strip()


def _default_rt() -> Dict[str, Any]:
    return {"prefix": "!", "overrides": [None] * 9}


def _load_rt() -> Dict[str, Any]:
    d = _default_rt()
    try:
        with open(RUNTIME_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            d["prefix"] = str(raw.get("prefix") or "!")[:4] or "!"
            ov = raw.get("overrides")
            if isinstance(ov, list):
                d["overrides"] = [x if isinstance(x, str) else None
                                  for x in (list(ov) + [None] * 9)[:9]]
    except Exception:
        pass
    return d


_RT = _load_rt()


def _save_rt() -> None:
    with suppress(Exception):
        with open(RUNTIME_FILE, "w", encoding="utf-8") as f:
            json.dump(_RT, f, indent=2)


PREFIX: str = _RT["prefix"]          # runtime-editable working prefix


def build_tokens() -> List[str]:
    """10 slots: [MAIN, #2..#10].  override: None=file / ''=cleared / 'tok'=set"""
    slots = [_clean_tok(CONFIG["MAIN_TOKEN"] or os.environ.get("BOT_TOKEN", ""))]
    for i in range(9):
        ov = _RT["overrides"][i]
        slots.append(_clean_tok(EXTRA_TOKENS[i] if ov is None else ov))
    seen, out = set(), []
    for t in slots:
        if t and t in seen:
            out.append("")
        else:
            if t:
                seen.add(t)
            out.append(t)
    return out[:MAX_TOKENS]


TOKENS: List[str] = build_tokens()
MAIN_TOKEN: str = TOKENS[0] if TOKENS else ""
OWNER_ID: int = int(CONFIG["OWNER_ID"] or os.environ.get("OWNER_ID", 0) or 0)

# ==============================================================================
# DYNAMIC COMMAND FILTER  —  "!" working · "~" equal · "/" telegram-style
# ==============================================================================
def _cmd_prefixes(extra: str = "") -> set:
    return {PREFIX, "!", "~", "/"} | set(extra or "")


class DCommand(Filter):
    """aiogram Command filter ka drop-in; prefix runtime me resolve hota hai."""

    def __init__(self, *commands: str, prefix: str = "", ignore_case: bool = True):
        self.names = {c.lower() for c in commands if c}
        self.extra = prefix or ""
        self.ignore_case = ignore_case

    async def __call__(self, message: Message):
        text = message.text or message.caption or ""
        parts = text.split(maxsplit=1)
        head = parts[0] if parts else ""
        if not head:
            return False
        for p in sorted(_cmd_prefixes(self.extra), key=len, reverse=True):
            if not p or not head.startswith(p):
                continue
            body = head[len(p):]
            if not body:
                continue
            name = body.split("@", 1)[0]
            if self.ignore_case:
                name = name.lower()
            if name in self.names:
                mention = body.split("@", 1)[1] if "@" in body else None
                rest = text[len(head):]
                if rest[:1].isspace():
                    rest = rest[1:]
                args = rest or None
                return {"command": CommandObject(prefix=p, command=name,
                                                 mention=mention, args=args)}
        return False


PORT: int = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", 7860)))
IST_TZ = timezone(timedelta(hours=5, minutes=30))

MAX_LOG_ENTRIES = 200
MAX_TRACKED_USERS_PER_CHAT = 150
MAX_PFP_CACHE_PER_CHAT = 4
MAX_MEDIA_PER_CHAT = 15
MAX_FWD_QUEUE_PER_CHAT = 10

DEFAULT_GAMEOVER_GIF_URL = "https://media.giphy.com/media/eJ4j2VnYOZU8qJU3Py/giphy.gif"
START_BANNER_TEXT = "Nik Engine V5 · Advanced Bot By @Nikkkwa (Nik)"

LOG_BUFFER: Deque[str] = deque(maxlen=MAX_LOG_ENTRIES)


# ==============================================================================
# RAM / IST / LOG HELPERS
# ==============================================================================
def get_ist_now() -> datetime:
    return datetime.now(IST_TZ)


def get_ist_str() -> str:
    return get_ist_now().strftime("%d-%m-%Y | %I:%M:%S %p IST")


def get_ram_mb() -> float:
    with suppress(Exception):
        if os.path.exists("/proc/self/status"):
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024.0
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return usage / (1024.0 * 1024.0) if sys.platform == "darwin" else usage / 1024.0
    return 0.0


def log_event(level: str, tag: str, message: str):
    timestamp = get_ist_now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] [{level.upper()}] [{tag}] {message}"
    LOG_BUFFER.append(entry)
    print(entry, flush=True)


# ==============================================================================
# DATA ARRAYS · 15 NC WALL+FONT DESIGNS · RAID TEXTS · SPECIAL POOLS
# ==============================================================================
RAID_TEXTS = (
    "Chup pagal slide mt kr 😂🥀",
    "Bas kar pagal bahut ho gaya",
    "Dekh tera game abhi khatam hone wala ⏰",
    "Chal chal TMKC me aag laga du 😂🔥",
    "Ny ny try ab band kar 🗽",
    "Oy haste haste thak gaya? 😂😂😂",
    "Chup rndc jaake kaam kar 🤢🤢🤢",
    "Sorry sir, no drama allowed ✋🏿🚫",
    "Dhek tera time aa gaya 🫡🫡🫡",
    "𝘾𝙃𝙐𝙋 𝙏𝙀𝙍𝙄 𝙈𝘼 𝙍𝘼𝙉𝘿𝙄👻",
    "☞. 𝘾𝙃𝘼𝙇 ☮️ 𝙈𝘼𝘼 𝙍𝘼𝙉𝘿𝙄 ❤",
    "कमजोर रण्डी तेरी मां 🩶᭪",
    "𝙈𝘼𝘼 𝙍𝘼𝙉𝘿𝙄 𝙏𝙀𝙍𝙄: ̗̀➛🩶₊⊹",
    "Game over ho gaya tera 💀",
    "Tu toh gaya bhai final 💀🔥",
    "Ab bas bhi kar de 💀",
    "Tera score ab zero hai 😂",
    "Aaj teri bari hai ⏰🥀",
    "Roya mat kar aage badh 😭👉",
    "Fights me tu hamesha last 🥉",
    "Bhai tu kyu exist kr rha 😂",
    "Ek aur bol dunga gir jayega 💀",
    "Teri team already quit kar chuki 😂",
    "Screen shot le le abhi ka 📸",
    "Winner winner chicken dinner 🍗",
    "Tera bot bhi tere jaisa slow 🐌",
    "Maan ja bhai haar gaya 🏳️",
    "Ab silence mode on kar 🔇",
    "RUK JA pagal ruk ja 😂",
    "Bahut badi papad beli hai 😂",
    "Teri toh lobby hi quit kar gayi 😂",
    "Aankh dikhake darr gaya kya 🫣",
    "Ek aur round? Tu already bahar 💀",
    "Scoreboard dekh le bhai 📊",
    "Tera comeback possible nahi 😭",
    "Hum toh bas warmup kar rahe the 🔥",
    "Teri strategy zero thi 😂",
    "Ab ghar jaake so ja 💤",
    "Bhai ne bhi tere against khela tha 😂",
    "Last position tera fixed hai 🥉",
    "Tera bot bhi give up kar gaya 🤖",
    "Screen record kar liya? 📱",
    "Jeet ke bhi haar gaya tu 😂",
    "Tera confidence dekhke hasi aayi 😂",
    "Dobara try mat karna, rehne de 😂",
    "Tu toh practice lobby me bhi last tha 💀",
    "Game ka naam yaad rakhna — tera 💀",
    "Aaj teri team ne sab chhod diya 😭",
)

EMOJIS = (
    "❄️", "🌀", "🍁", "🍢", "✉️", "🍬",
    "🍀", "🚀", "😂", "😭", "😡", "😈",
    "💧", "🫧", "☄️", "🦑", "🥀", "🧊",
)

PREFIX_EMOJIS = (
    "🐉", "🤩", "🌊", "🪼", "✨", "😹", "❄️", "🌀", "🍁", "⚡",
    "🔥", "🦋", "👑", "💎", "🌸", "🫧", "🦈", "🦁", "👻", "😈",
    "🎯", "🚀", "🌙", "⭐", "🍀",
)

WALL_CHARS = ("𒐫", "𒐫𒐫", "𒈙", "𒁏", "𒀰", "꫞", "ᯓ", "꧅", "⦗", "⩐")
GAP_100 = "\n" * 100

# 15 unique wall + font designs  (style_key → n1 .. n15)
DYNAMIC_NC_MAP: Dict[str, Dict[str, Any]] = {
    "n1":  {"prefixes": ("🐙", "🫧", "🦈", "🐬", "🐋", "🐳", "🦑", "🪼", "🦀", "🦞"),
            "fmt": "〚 {base} 𝙈𝘼𝘼 𝙍𝘼𝙉𝘿𝙄 𝙏𝙀𝙍𝙄 〛", "wall": "𒐫🍣"},
    "n2":  {"prefixes": ("🌸", "🎀", "🎐", "✨", "🌷", "🌺", "🌻", "🌼", "🍀", "💮"),
            "fmt": "< {base}  𝘉𝘌𝘛𝘈 𝘉𝘖𝘓𝘖  Nik 𝘗𝘈𝘗𝘼?? >", "wall": "𒈙🐲"},
    "n3":  {"prefixes": ("⚔️", "🔥", "🎸", "🎭", "🎯", "🚀", "⚡", "💣", "🧨", "🛡️"),
            "fmt": " {base} 𝐶ℎ𝑢𝑝 𝑅𝑛𝑑𝑦𝑐", "wall": "⸻🍜"},
    "n4":  {"prefixes": ("🩵", "🧡", "🤍", "💛", "🩷", "💜", "🖤", "💙", "💚", "🤎"),
            "fmt": "{base} Cʜᴀʟ Tᴍᴋʙ", "wall": "𒁏"},
    "n5":  {"prefixes": ("💔", "🖤", "😈", "👿", "👹", "👺", "💀", "👻", "👽", "👾"),
            "fmt": " {base} 𝘚𝘭𝘢𝘷𝘦", "wall": "꫞🍥"},
    "n6":  {"prefixes": ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌙", "🪐"),
            "fmt": "【 {base} 𝑪𝒉𝒖𝒅𝒂𝒊 𝑲𝒉𝒂 】", "wall": "𒀰𒐫🍙"},
    "n7":  {"prefixes": ("🐉", "🦣", "🦕", "🦖", "🐊", "🦁", "🐯", "🦅", "🦉", "🐺"),
            "fmt": " {base} 𝘙𝘢𝘯𝘥𝘪 𝘊𝘩𝘪𝘭𝘥", "wall": "🩷🩵💙۞"},
    "n8":  {"prefixes": ("🍧", "🍯", "🍭", "🌮", "🍣", "🍱", "🍜", "🍔", "🍥", "🍡"),
            "fmt": " {base} 𝙏𝙐𝙈 𝙈𝘼𝘿𝘼𝙍𝘾𝙃⭕𝘿", "wall": "ᯓ★🤍"},
    "n9":  {"prefixes": ("🍁", "🌿", "🪷", "❄️", "🌀", "🌊", "🔥", "🌟", "💥", "☄️"),
            "fmt": "《  {base} 𝑇𝑒𝑟𝑖 𝑀𝑎𝑎 𝑇𝑎𝑘𝑙𝑖 》", "wall": "--{🍬}"},
    "n10": {"prefixes": ("👑", "💎", "🏆", "🥇", "⚜️", "🔱", "💍", "🌟", "✨", "⚡"),
            "fmt": "〘  {base}  𝐾𝐼 𝑀𝐴𝐴 𝐶𝐻𝑂𝐷𝑁𝐸 𝐴𝑌𝐸??〙", "wall": "꧅🍭"},
    "n11": {"prefixes": ("⛓️", "🗡️", "🩸", "⚰️", "🪦", "🕸️", "🕷️", "🦇", "🐦‍⬛", "🏴‍☠️"),
            "fmt": "⦗  {base} Lᴜɴᴅ Cʜᴜs ⦘", "wall": "𒈙⸻🐬"},
    "n12": {"prefixes": ("🧬", "☢️", "☣️", "⚠️", "⚡", "🔋", "💻", "🛰️", "🛸", "🧪"),
            "fmt": "⧼  {base} Sʟᴀᴠᴇ ⧽", "wall": "ဪဪဪ🩶"},
    "n13": {"prefixes": ("❄️", "🧊", "🌨️", "⛄", "🌬️", "🏔️", "💎", "🩵", "🫧", "🌊"),
            "fmt": "⟬ ❄️ {base} Cʜᴜᴘ ʀɴᴅʏᴄᴇ ʟᴀᴅᴋᴇ ❄️ ⟭", "wall": "﷽🍡"},
    "n14": {"prefixes": ("🌺", "🦋", "🦩", "🦚", "🦜", "🦢", "🌷", "🌹", "🥀", "💐"),
            "fmt": "⦑  {base} TMKC ⦒", "wall": "ﷺ🍵"},
    "n15": {"prefixes": ("👹", "🔥", "💢", "💥", "🩸", "☠️", "⚡", "🔱", "⚔️", "🛡️"),
            "fmt": "【 {base} Lᴏᴅᴇ ᴘᴇ Cʜᴅ 】", "wall": "𒀱🍢"},
    "n16": {"prefixes": ("🌸", "💮", "🏵️", "🪷", "🥀", "🌷", "🌹", "💐", "🌺", "🌻"),
            "fmt": "❀  {base} ꜱᴏꜰᴛ ʟᴏꜱᴇʀ ❀", "wall": "🩷🎀"},
    "n17": {"prefixes": ("⚡", "🔋", "🔌", "💾", "🖥️", "⌨️", "🖱️", "📡", "🛰️", "🤖"),
            "fmt": "⟫ {base} ɴᴇᴏɴ ʀᴜɴɴɪɴɢ⟪", "wall": "▞▚▞"},
    "n18": {"prefixes": ("👑", "👑", "🏰", "🗡️", "🛡️", "⚜️", "🎖️", "🏆", "💎", "🔮"),
            "fmt": "♛ {base} ᴋɪɴɢ ᴏꜰ ᴡᴀʀs ♛", "wall": "𒈙👑"},
    "n19": {"prefixes": ("👻", "💀", "☠️", "🖤", "🌙", "🌑", "🪦", "⚰️", "🕸️", "🦇"),
            "fmt": "☾ {base} ᴘʜᴀɴᴛᴏᴍ ☾", "wall": "⸻🖤"},
    "n20": {"prefixes": ("🌊", "💧", "🫧", "🐳", "🐬", "🦈", "🏝️", "🌊", "❄️", "🌧️"),
            "fmt": "≈  {base} ᴛꜱᴜɴᴀᴍɪ ≈", "wall": "ᯓ🌊"},
    "n21": {"prefixes": ("🔥", "♨️", "🌋", "☄️", "💥", "🧨", "💣", "☀️", "🔥", "🔥"),
            "fmt": "✶ {base} ʙᴜʀɴɪɴɢ ✶", "wall": "𒀱🔥"},
    "n22": {"prefixes": ("🌌", "🌠", "⭐", "🌟", "✨", "☄️", "🪐", "🔭", "🛸", "🌙"),
            "fmt": "✦ {base} ɢᴀʟᴀxʏ ✦", "wall": "✧🌌"},
    "n23": {"prefixes": ("☢️", "☣️", "⚠️", "🧪", "☠️", "💀", "🦠", "☣️", "⚠️", "☢️"),
            "fmt": "☠ {base} ᴛᴏxɪᴄ ☠", "wall": "☣️☢️"},
    "n24": {"prefixes": ("⚔️", "🗡️", "🪓", "🏹", "🛡️", "🩸", "💀", "🔥", "⚡", "🔱"),
            "fmt": "⚔ {base} ʙᴀᴛᴛʟᴇ ⚔", "wall": "⸻⚔️"},
    "n25": {"prefixes": ("🏅", "🥇", "🏆", "👑", "💎", "🔱", "⚜️", "🎖️", "🏆", "👑"),
            "fmt": "❖ {base} ᴜɴᴅᴇꜰᴇᴀᴛᴇᴅ ❖", "wall": "𒐫🏅"},
}
STYLE_KEYS_TUPLE: Tuple[str, ...] = tuple(DYNAMIC_NC_MAP.keys())

# Template formats (built-in — templates.json se override bhi ho sakta hai)
BUILTIN_TEMPLATES: Tuple[str, ...] = (
    "〘 {base} ✠ NIK ENGINE ✠ 〙",
    "◈ {base} ◈ 𝐕𝟓 𝐔𝐋𝐓𝐈𝐌𝐀𝐓Ε ◈",
    "【 {base} · Ɓ𝒪𝒯 𝒲𝒜𝑅 】",
    "⟦ {base} ⟧ ⚡ NIKFYT ⚡",
    "⊰ {base} ⊰ 𝘕𝘶𝘮𝘱𝘦 𝘚𝘩𝘰𝘸 ⊰",
    "〘 {base} 👑 K I N G 👑 〙",
    "★彡 {base} 彡★",
    "ㄣ {base} ㄥ · R U N N I N G",
)

# Time NC patterns ({t} = live clock, {d} = date)
TIMENC_PATTERNS: Tuple[str, ...] = (
    "🕐 {base} · {t}",
    "⏰ {base} {t} ⏰",
    "⌚ {base} » {t} ({d})",
    "🌙 {base} — {t} IST",
    "⏱️ {base} | {d} {t}",
    "🕑 {base} ka time: {t}",
    "📅 {d} 🕐 {t} · {base}",
)

# Crash / glitch / special generators ke alphabet pools
CRASH_SET_A = "⣿⣽⣻⢿⡿⣟⣯⣷⠀⠁⠂⠄⡀⠈⠐⠠⠐⠨⠸⠸⠘⠰⠠⣿"
CRASH_SET_B = "҉҈҉҉҈҉҈҉҉҈҉҈҉҉҈҉҈҉҉҈҉҈҉҉҈҉҈҉҉҈"
GLITCH_SET = "̷̢̧̨̛̖̗̘̙̜̝̞̟̠̣̤̥̦̩̪̫̬̭̮̯̰̲̳̹̺̻̼͇͈͉͍͎͔͖͚͛͜͝ͅ"
ZERO_SET = "0oO○●◉◌◍◎⊕⊗"

ANINC_TEMPLATES: Tuple[str, ...] = (
    "ᴀɴɪ {base} ꜱʜᴏᴡ",
    "ᴀɴɪᴍᴇ {base} ⚡",
    "ᴀɴɪ {base} · ʀᴜɴɴɪɴɢ",
    "{base} · ᴀɴɪ ᴍᴏᴅᴇ",
)
HNC_TEMPLATES: Tuple[str, ...] = (
    "ʜ {base} · ʜᴜɴᴛ",
    "ʜᴜɴᴛɪɴɢ {base} 💀",
    "{base} ʜᴜɴᴛᴇᴅ ☠️",
    "ʜ · {base} · ʜᴏᴏᴋ",
)
INC_TEMPLATES: Tuple[str, ...] = (
    "ɪɴꜱᴀɴᴇ {base} 🔥",
    "ɪ {base} · ɪɴꜱᴀɴᴇ",
    "{base} · ɪɴꜱᴀɴᴇ ᴍᴏᴅᴇ",
    "ɪɴꜱᴀɴᴇ · {base} ⚡",
)
JNC_TEMPLATES: Tuple[str, ...] = (
    "ᴊ {base} · ᴊᴜɴɢʟᴇ",
    "ᴊᴜɴɢʟᴇ {base} 🌿",
    "{base} · ᴊᴜɴɢʟᴇ ᴡᴀʀ",
    "ᴊ · {base} · ᴊᴜɴɢʟᴇ",
)
FUCKNC_TEMPLATES: Tuple[str, ...] = (
    "ꜰᴜᴄᴋ {base} 💀",
    "ꜰ {base} · ꜰᴜᴄᴋᴇᴅ",
    "{base} · ᴜɴᴅᴇꜰᴇᴀᴛᴇᴅ? ɴᴏᴘᴇ",
    "ꜰᴜᴄᴋɪɴɢ {base} 🔥",
)
FTNC_TEMPLATES: Tuple[str, ...] = (
    "ꜰᴛ {base} · ꜰɪɴᴀʟ",
    "ꜰɪɴᴀʟ {base} ᴛʜʀᴏɴᴇ 👑",
    "{base} · ꜰᴛ ᴡᴀʀ",
    "ꜰᴛ · {base} · ɢɢ",
)
LONG_WALL = "𒐫" * 24
# ==============================================================================
# ULTRA-FAST LRU + ZERO-DOMINO PIPELINED FLOOD BYPASS CONTROLLER
# ==============================================================================
class FloodBypassEngine:
    __slots__ = (
        "cooldowns", "bypass_active", "throttle_mode", "flood_history",
        "last_nc_call", "in_flight", "per_bot_nc_gap", "cluster_nc_tick",
        "total_nc_ok", "recent_ticks", "missing_until",
    )

    def __init__(self):
        self.cooldowns: Dict[int, float] = {}
        self.bypass_active: bool = True
        self.throttle_mode: str = "max"
        self.flood_history: Dict[int, int] = {}
        self.last_nc_call: Dict[int, float] = {}
        self.in_flight: Set[int] = set()
        self.missing_until: Dict[tuple, float] = {}

        # 0.92s per-bot gap + dynamic cluster tick = zero-flood pipeline
        self.per_bot_nc_gap: float = 0.92
        self.cluster_nc_tick: float = 0.10

        self.total_nc_ok: int = 0
        self.recent_ticks: Deque[float] = deque(maxlen=40)

    def recalculate_pacing(self, total_bots: int):
        n = max(1, total_bots)
        if self.throttle_mode == "safe":
            self.per_bot_nc_gap = 1.10
        else:
            self.per_bot_nc_gap = 0.92
        self.cluster_nc_tick = round(1.0 / n, 3)

    def set_mode(self, mode: str, total_bots: int):
        mode = mode.lower().strip()
        if mode in ("max", "turbo", "on", "fast"):
            self.throttle_mode = "max"
            self.bypass_active = True
        elif mode in ("safe", "stable", "balanced"):
            self.throttle_mode = "safe"
            self.bypass_active = True
        elif mode in ("off", "disable", "raw"):
            self.throttle_mode = "off"
            self.bypass_active = False
        self.recalculate_pacing(total_bots)

    def record_nc_success(self):
        self.recent_ticks.append(time.monotonic())
        self.total_nc_ok += 1

    def get_live_hz(self) -> float:
        if len(self.recent_ticks) < 2:
            return 0.0
        now = time.monotonic()
        span = now - self.recent_ticks[0]
        if span <= 0 or (now - self.recent_ticks[-1]) > 5.0:
            return 0.0
        return round((len(self.recent_ticks) - 1) / span, 2)

    def mark_bot_present(self, chat_id: int, bot_id: int):
        self.missing_until.pop((chat_id, bot_id), None)

    def mark_bot_missing_in_chat(self, chat_id: int, bot_id: int, duration: float = 25.0):
        self.missing_until[(chat_id, bot_id)] = time.monotonic() + duration

    def is_bot_usable_in_chat(self, chat_id: int, bot_id: int) -> bool:
        until = self.missing_until.get((chat_id, bot_id), 0.0)
        if time.monotonic() < until:
            return False
        if (chat_id, bot_id) in self.missing_until:
            del self.missing_until[(chat_id, bot_id)]
        return True

    def is_flooded(self, bot_id: int) -> bool:
        until = self.cooldowns.get(bot_id, 0.0)
        if time.monotonic() < until:
            return True
        if bot_id in self.cooldowns:
            del self.cooldowns[bot_id]
            log_event("info", "BYPASS", f"Bot {bot_id} cooldown expired. Rejoined pipeline.")
        return False

    def is_ready_for_nc(self, bot_id: int, chat_id: Optional[int] = None) -> bool:
        if bot_id in self.in_flight or self.is_flooded(bot_id):
            return False
        if chat_id is not None and not self.is_bot_usable_in_chat(chat_id, bot_id):
            return False
        if not self.bypass_active:
            return True
        return (time.monotonic() - self.last_nc_call.get(bot_id, 0.0)) >= self.per_bot_nc_gap

    def pick_best_nc_bot(self, bots: List["Bot"], chat_id: int) -> Optional["Bot"]:
        """LRU selector: ready bot jo sabse zyada rest ho. Sab missing ho to
        self-heal karke flags reset — NC kabhi rukega nahi."""
        best_bot = None
        oldest_call = float("inf")
        usable_any = False

        for b in bots:
            if self.is_bot_usable_in_chat(chat_id, b.id):
                usable_any = True
            if self.is_ready_for_nc(b.id, chat_id):
                last_t = self.last_nc_call.get(b.id, 0.0)
                if last_t < oldest_call:
                    oldest_call = last_t
                    best_bot = b

        if not usable_any and bots:
            for b in bots:
                self.mark_bot_present(chat_id, b.id)
            for b in bots:
                if self.is_ready_for_nc(b.id, chat_id):
                    last_t = self.last_nc_call.get(b.id, 0.0)
                    if last_t < oldest_call:
                        oldest_call = last_t
                        best_bot = b
        return best_bot

    def count_healthy_bots_in_chat(self, bots: List["Bot"], chat_id: int) -> int:
        return sum(1 for b in bots if self.is_bot_usable_in_chat(chat_id, b.id))

    def mark_started(self, bot_id: int):
        self.in_flight.add(bot_id)
        self.last_nc_call[bot_id] = time.monotonic()

    def mark_finished(self, bot_id: int):
        self.in_flight.discard(bot_id)

    def mark_flooded(self, bot_id: int, retry_after: float, chat_id: Optional[int] = None):
        wait = max(1.0, float(retry_after))
        self.cooldowns[bot_id] = time.monotonic() + wait
        self.flood_history[bot_id] = self.flood_history.get(bot_id, 0) + 1
        if chat_id is not None:
            self.mark_bot_missing_in_chat(chat_id, bot_id, duration=wait + 2.0)
        log_event("warn", "BYPASS", f"Bot {bot_id} flooded → {wait:.0f}s cooldown.")


class BotQueue:
    """Per-bot minimum gap — har bot ke beech fixed spacing (pipeline etiquette)."""

    def __init__(self, min_gap_ms: int = 100):
        self.min_gap = min_gap_ms / 1000.0
        self.last: Dict[int, float] = {}

    async def wait(self, bid: int):
        now = time.monotonic()
        nxt = self.last.get(bid, 0.0) + self.min_gap
        if nxt > now:
            await asyncio.sleep(nxt - now)
        self.last[bid] = time.monotonic()


# ==============================================================================
# BOUNDED LOG / STATE MANAGER (tasks · persistence · sudo · caches · delays)
# ==============================================================================
def load_sudo() -> Set[int]:
    try:
        with open(SUDO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {int(x) for x in data}
    except Exception:
        pass
    return set()


class StateManager:
    def __init__(self):
        self.tasks: Dict[str, Dict[int, asyncio.Task]] = {"nc": {}, "spam": {}, "fwd": {},
                                                          "slide": {}, "media": {}, "pfp": {}}
        self.persisted_tasks: Dict[str, Dict[str, List[Any]]] = {
            "nc": {}, "spam": {}, "fwd": {}, "slide": {}, "media": {}, "pfp": {},
        }
        self.known_chats: Set[int] = set()
        self.failed_bots: List[str] = []
        self.global_nc_count: int = 0
        self.unsaved_nc_hits: int = 0
        self.chat_nc_counts: Dict[int, int] = {}
        self.sudo: Set[int] = load_sudo()
        self.delays: Dict[str, float] = {
            "nc": 0.1, "spam": 0.0, "rspm": 0.0, "slide": 0.0,
            "fwd": 0.08, "media": 1.5, "pfp": 5.0, "gpfp": 5.0, "react": 1.0,
        }
        self.fwd_queue: Dict[int, Deque[Dict[str, Any]]] = {}
        self.pfp_cache: Dict[str, Dict[int, List[bytes]]] = {"pfp": {}, "gpfp": {}}
        self.media: Dict[str, Dict[int, List[str]]] = {
            "sticker": {}, "gif": {}, "photo": {}, "voice": {},
        }
        self.tracked_users: Dict[int, Dict[int, float]] = {}
        # Interceptors: rnorv (auto-insult reply) · rct (auto-react) · swp (auto-swipe)
        self.targets: Dict[str, Dict[Any, Any]] = {
            "reply": {}, "react": {}, "swipe": {},
        }
        self.global_muted: Dict[int, float] = {}   # uid → expiry (0 = permanent)
        self._dirty = False

    # ── sudo ──────────────────────────────────────────────────────────────
    def save_sudo(self):
        with suppress(Exception):
            with open(SUDO_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(self.sudo), f)

    # ── gameover gif ─────────────────────────────────────────────────────
    def save_gameover_gif(self, raw_bytes: bytes):
        with suppress(Exception):
            with open(GAMEOVER_FILE, "wb") as f:
                f.write(raw_bytes)

    def load_gameover_gif(self) -> Optional[bytes]:
        with suppress(Exception):
            if os.path.exists(GAMEOVER_FILE):
                with open(GAMEOVER_FILE, "rb") as f:
                    return f.read()
        return None

    # ── pfp disk cache ───────────────────────────────────────────────────
    def save_pfp_to_disk(self, cat: str, cid: int):
        with suppress(Exception):
            items = self.pfp_cache[cat].get(cid) or []
            if not items:
                return
            d = os.path.join(CACHE_DIR, f"{cat}_{abs(cid)}")
            os.makedirs(d, exist_ok=True)
            for i, raw in enumerate(items[:MAX_PFP_CACHE_PER_CHAT]):
                with open(os.path.join(d, f"{i}.jpg"), "wb") as f:
                    f.write(raw)

    def load_pfp_from_disk(self):
        with suppress(Exception):
            for cat in ("pfp", "gpfp"):
                root = CACHE_DIR
                for name in os.listdir(root):
                    if not name.startswith(f"{cat}_"):
                        continue
                    cid = int(name.split("_", 1)[1]) * (-1 if name.split("_", 1)[0].endswith("-") else 1)
                    d = os.path.join(root, name)
                    files = sorted(
                        (os.path.join(d, x) for x in os.listdir(d) if x.endswith(".jpg")),
                        key=lambda p: int(os.path.splitext(os.path.basename(p))[0]),
                    )
                    blobs = []
                    for p in files:
                        with open(p, "rb") as f:
                            blobs.append(f.read())
                    if blobs:
                        self.pfp_cache[cat][cid] = blobs[-MAX_PFP_CACHE_PER_CHAT:]

    # ── runtime state persistence ────────────────────────────────────────
    def save_runtime_state(self):
        with suppress(Exception):
            data = {
                "global_nc_count": self.global_nc_count,
                "chat_nc_counts": {str(k): v for k, v in self.chat_nc_counts.items()},
                "known_chats": sorted(self.known_chats),
                "delays": self.delays,
                "persisted_tasks": self.persisted_tasks,
                "unsaved_nc_hits": self.unsaved_nc_hits,
                "targets": {k: {str(kk): vv for kk, vv in v.items()}
                            for k, v in self.targets.items()},
                "global_muted": {str(k): v for k, v in self.global_muted.items()},
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            self._dirty = False

    def load_runtime_state(self) -> Dict[str, Any]:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.global_nc_count = int(data.get("global_nc_count", 0) or 0)
                self.chat_nc_counts = {int(k): int(v) for k, v in
                                       (data.get("chat_nc_counts") or {}).items()}
                self.known_chats = {int(x) for x in (data.get("known_chats") or [])}
                for k, v in (data.get("delays") or {}).items():
                    if k in self.delays:
                        self.delays[k] = float(v)
                pt = data.get("persisted_tasks")
                if isinstance(pt, dict):
                    for cat, mapping in pt.items():
                        if cat in self.persisted_tasks and isinstance(mapping, dict):
                            self.persisted_tasks[cat] = mapping
                tg = data.get("targets")
                if isinstance(tg, dict):
                    for k in ("reply", "react", "swipe"):
                        raw = tg.get(k)
                        if isinstance(raw, dict):
                            # keys int hain (uid / chat_id) — JSON str banata hai
                            self.targets[k] = {int(kk): vv for kk, vv in raw.items()}
                gm = data.get("global_muted")
                if isinstance(gm, dict):
                    self.global_muted = {int(k): float(v) for k, v in gm.items()}
                return data
        except Exception:
            pass
        return {}

    # ── nc accounting ────────────────────────────────────────────────────
    def record_nc_hit(self, cid: int):
        self.global_nc_count += 1
        self.unsaved_nc_hits += 1
        self.chat_nc_counts[cid] = self.chat_nc_counts.get(cid, 0) + 1
        if self.unsaved_nc_hits >= 40:
            self.unsaved_nc_hits = 0
            self.save_runtime_state()

    def register_chat_bot(self, cid: int, bot_id: int, is_group: bool = True):
        if is_group and cid < 0:
            self.known_chats.add(cid)

    def get_all_known_groups(self, fallback_cid: Optional[int] = None) -> List[int]:
        out = sorted(c for c in self.known_chats if c < 0)
        if fallback_cid is not None and fallback_cid < 0 and fallback_cid not in out:
            out.insert(0, fallback_cid)
        return out

    # ── task registry ────────────────────────────────────────────────────
    def add_task(self, cat: str, cid: int, task: asyncio.Task):
        self.tasks.setdefault(cat, {})[cid] = task

    def cancel(self, cat: str, cid: int, save: bool = True):
        task = self.tasks.get(cat, {}).pop(cid, None)
        if task and not task.done():
            task.cancel()
        if save:
            self.persisted_tasks.get(cat, {}).pop(str(cid), None)

    def stop_all(self, cid: Optional[int] = None, save: bool = True):
        stopped = 0
        for cat, mapping in self.tasks.items():
            for k in list(mapping.keys()):
                if cid is None or k == cid:
                    t = mapping.pop(k)
                    if t and not t.done():
                        t.cancel()
                        stopped += 1
                    if save:
                        self.persisted_tasks.get(cat, {}).pop(str(k), None)
        if save:
            self.save_runtime_state()
        return stopped


STATE = StateManager()
fbe = FloodBypassEngine()
bq = BotQueue(min_gap_ms=100)

active_bots: List[Bot] = []
bot_usernames: Dict[int, str] = {}
LIVE_BOTS: Dict[str, Bot] = {}
MULTIMODE: bool = False
REACT_LOOP: Dict[int, List[str]] = {}          # cid → emoji rotation
PENDING_REACT: Dict[int, Tuple[int, List[str]]] = {}  # cid → (msg_id, emojis)

router = Router()


# ==============================================================================
# AUTH FILTERS
# ==============================================================================
class IsOwner(Filter):
    async def __call__(self, message: Message):
        u = message.from_user
        return bool(u and OWNER_ID and u.id == OWNER_ID)


class IsSudo(Filter):
    async def __call__(self, message: Message):
        u = message.from_user
        return bool(u and OWNER_ID and (u.id == OWNER_ID or u.id in STATE.sudo))
# ==============================================================================
# TITLE GENERATORS  (wall · style · mix · template · time · crash · glitch …)
# ==============================================================================
def _truncate(s: str, n: int = 120) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def read_template() -> List[str]:
    try:
        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return [str(x) for x in data]
    except Exception:
        pass
    return list(BUILTIN_TEMPLATES)


def generate_single_title(base: str, mode: str, tick_idx: int,
                          style_key: Optional[str] = None,
                          tmpl_format: Optional[Any] = None) -> str:
    style = DYNAMIC_NC_MAP.get(style_key or "", None)
    rnd = random.choice
    prefix = rnd(PREFIX_EMOJIS)

    if mode == "wall":                      # classic 7-line 𒐫 wall NC
        wall = rnd(WALL_CHARS)
        lines = [f"{prefix} {wall}"] * 3 + [f"{_truncate(base, 60)}"] + [f"{wall} {prefix}"] * 3
        return "\n".join(lines)

    if mode == "style" and style:
        head = rnd(style["prefixes"])
        body = style["fmt"].format(base=_truncate(base, 48))
        return f"{head} {body} {style['wall']}"

    if mode == "template":
        chosen = tmpl_format[tick_idx % len(tmpl_format)] if isinstance(tmpl_format, list) else (
            tmpl_format or BUILTIN_TEMPLATES[tick_idx % len(BUILTIN_TEMPLATES)])
        return _truncate(str(chosen).replace("{base}", _truncate(base, 50)))

    if mode == "time":
        pat = TIMENC_PATTERNS[tick_idx % len(TIMENC_PATTERNS)]
        now = get_ist_now()
        return _truncate(pat.format(base=_truncate(base, 40),
                                    t=now.strftime("%I:%M:%S %p"),
                                    d=now.strftime("%d/%m")))

    if mode == "raid":
        return _truncate(f"{base} {rnd(RAID_TEXTS)} {rnd(EMOJIS)}")

    if mode == "long":
        return _truncate(f"{LONG_WALL}\n{base}\n{LONG_WALL}", 120)

    if mode in ("crash", "crash2", "crash3"):
        pool = CRASH_SET_B if mode == "crash" else CRASH_SET_A
        junk = "".join(rnd(list(pool)) for _ in range(28 if mode != "crash3" else 56))
        return _truncate(f"{base} {junk}")

    if mode in ("glitch", "glitch2"):
        out = []
        for ch in base:
            out.append(ch)
            if random.random() < 0.45:
                out.append(rnd(list(GLITCH_SET)))
        if mode == "glitch2":
            out.append(" " + "".join(rnd(list(ZERO_SET)) for _ in range(12)))
        return _truncate("".join(out))

    if mode in ("ani", "h", "i", "j", "fuck", "ft"):
        pools = {"ani": ANINC_TEMPLATES, "h": HNC_TEMPLATES, "i": INC_TEMPLATES,
                 "j": JNC_TEMPLATES, "fuck": FUCKNC_TEMPLATES, "ft": FTNC_TEMPLATES}
        pat = pools[mode][tick_idx % len(pools[mode])]
        return _truncate(pat.format(base=_truncate(base, 44)))

    # fallback → style default
    return _truncate(f"{prefix} {_truncate(base, 70)}")


def build_title_pool(base: str, mode: str, style_key: Optional[str] = None,
                     tmpl_format: Optional[Any] = None) -> Tuple[str, ...]:
    """Pre-generate a rotating pool (fast path for wall/style/template/mix)."""
    if mode == "mix":
        pool: List[str] = []
        for k in STYLE_KEYS_TUPLE:
            for i in range(2):
                pool.append(generate_single_title(base, "style", i, style_key=k))
        for i in range(len(BUILTIN_TEMPLATES)):
            pool.append(generate_single_title(base, "template", i, tmpl_format=tmpl_format))
        pool.append(generate_single_title(base, "wall", 0))
        pool.append(generate_single_title(base, "long", 0))
        random.shuffle(pool)
        return tuple(pool)

    if mode in ("style", "wall", "template", "long"):
        n = 15 if mode == "style" else (10 if mode == "wall" else 8)
        return tuple(generate_single_title(base, mode, i, style_key=style_key,
                                           tmpl_format=tmpl_format) for i in range(n))

    # dynamic modes (time/raid/crash/glitch/…) — pool banaya jayega tick par
    return tuple(generate_single_title(base, mode, i, style_key=style_key,
                                       tmpl_format=tmpl_format) for i in range(6))


def build_emoji_rotation(emoji: str, n: int = 8) -> List[str]:
    e = (emoji or "🔥").strip()[:2] or "🔥"
    return [e] * n


# ==============================================================================
# MESSAGE BACKUP / GAMEOVER MEDIA
# ==============================================================================
def extract_message_backup(msg: Message) -> Dict[str, Any]:
    b: Dict[str, Any] = {"type": "text", "text": msg.text or msg.caption or ""}
    if msg.sticker:
        b = {"type": "sticker", "file_id": msg.sticker.file_id}
    elif msg.photo:
        b = {"type": "photo", "file_id": msg.photo[-1].file_id,
             "caption": msg.caption or ""}
    elif msg.video:
        b = {"type": "video", "file_id": msg.video.file_id, "caption": msg.caption or ""}
    elif msg.animation:
        b = {"type": "animation", "file_id": msg.animation.file_id, "caption": msg.caption or ""}
    elif msg.voice:
        b = {"type": "voice", "file_id": msg.voice.file_id}
    elif msg.audio:
        b = {"type": "audio", "file_id": msg.audio.file_id, "caption": msg.caption or ""}
    elif msg.document:
        b = {"type": "document", "file_id": msg.document.file_id, "caption": msg.caption or ""}
    return b


async def send_backup_snapshot(bot: Bot, chat_id: int, backup: Dict[str, Any]):
    t = backup.get("type")
    try:
        if t == "text":
            await bot.send_message(chat_id, backup.get("text") or "")
        elif t == "sticker":
            await bot.send_sticker(chat_id, backup.get("file_id"))
        elif t == "photo":
            await bot.send_photo(chat_id, backup.get("file_id"),
                                 caption=backup.get("caption") or None)
        elif t == "video":
            await bot.send_video(chat_id, backup.get("file_id"),
                                 caption=backup.get("caption") or None)
        elif t == "animation":
            await bot.send_animation(chat_id, backup.get("file_id"),
                                     caption=backup.get("caption") or None)
        elif t == "voice":
            await bot.send_voice(chat_id, backup.get("file_id"))
        elif t == "audio":
            await bot.send_audio(chat_id, backup.get("file_id"),
                                 caption=backup.get("caption") or None)
        elif t == "document":
            await bot.send_document(chat_id, backup.get("file_id"),
                                    caption=backup.get("caption") or None)
    except Exception:
        pass


async def set_bot_profile_photo_safe(bot: Bot, raw_bytes: bytes) -> bool:
    try:
        photo = BufferedInputFile(raw_bytes, "pfp.jpg")
        if InputProfilePhotoStatic is not None:
            await bot.set_my_profile_photo(InputProfilePhotoStatic(photo=photo))
        else:
            await bot.set_my_profile_photo(photo)
        return True
    except Exception:
        return False


# ==============================================================================
# WORKERS  (sab cluster-style: har worker khud bots pick karta hai)
# ==============================================================================
async def _fire_single_nc(bot: Bot, chat_id: int, title: str):
    """Non-blocking single title update — network RTT pipeline ko stall nahi karta."""
    bid = bot.id
    fbe.mark_started(bid)
    try:
        await bot.set_chat_title(chat_id, title)
        fbe.record_nc_success()
        STATE.record_nc_hit(chat_id)
    except TelegramRetryAfter as e:
        fbe.mark_flooded(bid, e.retry_after, chat_id=chat_id)
    except TelegramForbiddenError:
        fbe.mark_bot_missing_in_chat(chat_id, bid, duration=25.0)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(w in err for w in ("chat not found", "not a member", "not enough rights",
                                  "have no rights", "chat_admin_required")):
            fbe.mark_bot_missing_in_chat(chat_id, bid, duration=25.0)
    except Exception:
        pass
    finally:
        fbe.mark_finished(bid)


async def nc_cluster_worker(chat_id: int, base: str, mode: str,
                            style_key: Optional[str] = None,
                            tmpl_format: Optional[Any] = None):
    """Non-Stop 24/7 Zero-Drift 10-Bot LRU Pipelined NC dispatcher."""
    title_pool = build_title_pool(base, mode, style_key, tmpl_format)
    pool_len = len(title_pool)
    title_idx = 0
    dynamic = mode in ("time", "raid", "crash", "crash2", "crash3",
                       "glitch", "glitch2", "ani", "h", "i", "j", "fuck", "ft")
    create_task = asyncio.create_task
    sleep = asyncio.sleep
    monotonic = time.monotonic

    while True:
        try:
            total = len(active_bots)
            if total == 0:
                await sleep(0.5)
                continue

            selected_bot = fbe.pick_best_nc_bot(active_bots, chat_id)
            if selected_bot is not None:
                t0 = monotonic()
                if dynamic:
                    new_title = generate_single_title(base, mode, title_idx,
                                                      style_key=style_key,
                                                      tmpl_format=tmpl_format)
                else:
                    new_title = title_pool[title_idx % pool_len]
                title_idx += 1
                create_task(_fire_single_nc(selected_bot, chat_id, new_title))

                if fbe.bypass_active:
                    healthy = max(1, fbe.count_healthy_bots_in_chat(active_bots, chat_id))
                    dynamic_tick = fbe.per_bot_nc_gap / healthy
                    base_tick = STATE.delays["nc"] if STATE.delays["nc"] > 0 else fbe.cluster_nc_tick
                    tick = max(base_tick, dynamic_tick)
                else:
                    tick = STATE.delays["nc"] if STATE.delays["nc"] > 0 else 0.08

                elapsed = monotonic() - t0
                rem = tick - elapsed
                if rem > 0:
                    await sleep(rem)
            else:
                await sleep(0.008)
        except asyncio.CancelledError:
            break
        except Exception:
            await sleep(0.02)


async def _fire_single_forward(bot: Bot, chat_id: int, item: Dict[str, Any]):
    bid = bot.id
    from_cid = int(item["from_chat_id"])
    msg_id = int(item["message_id"])
    use_copy = bool(item.get("copy", False))
    backup = item.get("backup")
    try:
        if use_copy:
            await bot.copy_message(chat_id=chat_id, from_chat_id=from_cid, message_id=msg_id)
        else:
            await bot.forward_message(chat_id=chat_id, from_chat_id=from_cid, message_id=msg_id)
    except TelegramRetryAfter as e:
        fbe.mark_flooded(bid, e.retry_after, chat_id=chat_id)
    except TelegramForbiddenError:
        fbe.mark_bot_missing_in_chat(chat_id, bid, duration=25.0)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(w in err for w in ("chat not found", "not a member", "not enough rights",
                                  "have no rights")):
            fbe.mark_bot_missing_in_chat(chat_id, bid, duration=25.0)
        elif backup:
            with suppress(Exception):
                await send_backup_snapshot(bot, chat_id, backup)
    except Exception:
        pass


async def fwd_cluster_worker(chat_id: int):
    item_idx = 0
    bot_idx = 0
    create_task = asyncio.create_task
    sleep = asyncio.sleep

    while True:
        try:
            items = STATE.fwd_queue.get(chat_id)
            total = len(active_bots)
            if not items or total == 0:
                await sleep(0.4)
                continue

            selected_bot = None
            for _ in range(total):
                candidate = active_bots[bot_idx % total]
                bot_idx += 1
                if not fbe.is_flooded(candidate.id) and fbe.is_bot_usable_in_chat(chat_id, candidate.id):
                    selected_bot = candidate
                    break

            if selected_bot is not None:
                await bq.wait(selected_bot.id)
                item = items[item_idx % len(items)]
                item_idx += 1
                create_task(_fire_single_forward(selected_bot, chat_id, item))
                await sleep(max(0.08, STATE.delays["fwd"]))
            else:
                await sleep(0.02)
        except asyncio.CancelledError:
            break
        except Exception:
            await sleep(0.05)


async def spam_worker(chat_id: int, text: str, mode: str,
                      tmpl_format: Optional[Any] = None, initial_delay_ms: int = 0):
    """Cluster spam: static / raid / template / crash-text modes."""
    if initial_delay_ms > 0:
        await asyncio.sleep(initial_delay_ms / 1000.0)
    delay_key = "rspm" if mode in ("rs", "ns") else "spam"
    sleep = asyncio.sleep
    choice = random.choice
    is_raid = mode in ("rs", "ns")
    is_tmpl = (mode == "template" and bool(tmpl_format))
    is_crash = (mode == "crash")
    bot_idx = 0

    while True:
        try:
            total = len(active_bots)
            if total == 0:
                await sleep(0.5)
                continue

            bot = None
            for _ in range(total):
                cand = active_bots[bot_idx % total]
                bot_idx += 1
                if not fbe.is_flooded(cand.id) and fbe.is_bot_usable_in_chat(chat_id, cand.id):
                    bot = cand
                    break
            if bot is None:
                await sleep(0.3)
                continue

            await bq.wait(bot.id)

            if is_tmpl:
                chosen = choice(tmpl_format) if isinstance(tmpl_format, list) else tmpl_format
                base_msg = str(chosen).replace("{base}", text)
            elif is_raid:
                base_msg = f"{text} {choice(RAID_TEXTS)}"
            elif is_crash:
                junk = "".join(choice(list(CRASH_SET_A)) for _ in range(60))
                base_msg = f"{text} {junk}"
            else:
                base_msg = text
            msg = f"<b>{_hesc(base_msg)}{GAP_100}{_hesc(base_msg)}</b>"

            await bot.send_message(chat_id, msg, parse_mode=ParseMode.HTML)

            dly = STATE.delays[delay_key]
            if dly > 0:
                await sleep(dly)
            else:
                await sleep(0.05)

        except TelegramRetryAfter as e:
            fbe.mark_flooded(bot.id if bot else 0, e.retry_after, chat_id=chat_id)
            await sleep(0.1)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            if any(w in str(e).lower() for w in ("chat not found", "not a member",
                                                 "forbidden", "have no rights")):
                if bot:
                    fbe.mark_bot_missing_in_chat(chat_id, bot.id, duration=25.0)
            await sleep(0.4)
        except asyncio.CancelledError:
            break
        except Exception:
            await sleep(0.4)


async def slide_worker(chat_id: int, uid: int, name: str,
                       text: Optional[str] = None, initial_delay_ms: int = 0):
    """Targeted mention-slide: @user + taunt + emoji, 10-bot round-robin."""
    if initial_delay_ms > 0:
        await asyncio.sleep(initial_delay_ms / 1000.0)
    sleep = asyncio.sleep
    choice = random.choice
    mention = f'<a href="tg://user?id={uid}">{_hesc(name)}</a>'
    bot_idx = 0

    while True:
        try:
            total = len(active_bots)
            if total == 0:
                await sleep(0.5)
                continue
            bot = None
            for _ in range(total):
                cand = active_bots[bot_idx % total]
                bot_idx += 1
                if not fbe.is_flooded(cand.id) and fbe.is_bot_usable_in_chat(chat_id, cand.id):
                    bot = cand
                    break
            if bot is None:
                await sleep(0.3)
                continue

            await bq.wait(bot.id)
            base_msg = f"{mention} {text if text else choice(RAID_TEXTS)} {choice(EMOJIS)}"
            msg = f"{base_msg}{GAP_100}{base_msg}"
            await bot.send_message(chat_id, msg, parse_mode=ParseMode.HTML)

            dly = STATE.delays["slide"]
            if dly > 0:
                await sleep(dly)
            else:
                await sleep(0.05)
        except TelegramRetryAfter as e:
            fbe.mark_flooded(bot.id if bot else 0, e.retry_after, chat_id=chat_id)
            await sleep(0.15)
        except asyncio.CancelledError:
            break
        except Exception:
            await sleep(0.4)


async def pfp_worker(chat_id: int, is_global: bool = False, initial_delay_ms: int = 0):
    if initial_delay_ms > 0:
        await asyncio.sleep(initial_delay_ms / 1000.0)
    idx = 0
    m_cat = "gpfp" if is_global else "pfp"
    bot_idx = 0

    while True:
        try:
            total = len(active_bots)
            if total == 0:
                await sleep(0.8)
                continue
            items = STATE.pfp_cache[m_cat].get(chat_id)
            if not items:
                await asyncio.sleep(1.5)
                continue

            bot = None
            for _ in range(total):
                cand = active_bots[bot_idx % total]
                bot_idx += 1
                if not fbe.is_flooded(cand.id) and (
                        is_global or fbe.is_bot_usable_in_chat(chat_id, cand.id)):
                    bot = cand
                    break
            if bot is None:
                await asyncio.sleep(0.6)
                continue

            raw_bytes = items[idx % len(items)]
            if is_global:
                await set_bot_profile_photo_safe(bot, raw_bytes)
            else:
                await bot.set_chat_photo(chat_id, photo=BufferedInputFile(raw_bytes, "pfp.jpg"))

            await asyncio.sleep(STATE.delays[m_cat])
            idx += 1
        except TelegramRetryAfter as e:
            fbe.mark_flooded(bot.id if bot else 0, e.retry_after,
                             chat_id=None if is_global else chat_id)
            await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.8)


async def media_worker(chat_id: int, mtype: str, initial_delay_ms: int = 0):
    if initial_delay_ms > 0:
        await asyncio.sleep(initial_delay_ms / 1000.0)
    idx = 0
    bot_idx = 0
    senders = {"sticker": "send_sticker", "gif": "send_animation",
               "photo": "send_photo", "voice": "send_voice"}

    while True:
        try:
            total = len(active_bots)
            if total == 0:
                await asyncio.sleep(0.8)
                continue
            items = STATE.media[mtype].get(chat_id)
            if not items:
                await asyncio.sleep(1.5)
                continue

            bot = None
            for _ in range(total):
                cand = active_bots[bot_idx % total]
                bot_idx += 1
                if not fbe.is_flooded(cand.id) and fbe.is_bot_usable_in_chat(chat_id, cand.id):
                    bot = cand
                    break
            if bot is None:
                await asyncio.sleep(0.5)
                continue

            await bq.wait(bot.id)
            fid = items[idx % len(items)]
            await getattr(bot, senders[mtype])(chat_id, fid)
            await asyncio.sleep(STATE.delays["media"])
            idx += 1
        except TelegramRetryAfter as e:
            fbe.mark_flooded(bot.id if bot else 0, e.retry_after, chat_id=chat_id)
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.4)


async def reaction_worker(chat_id: int, msg_id: int, emojis: List[str]):
    """Ek message pe rotation reaction loop."""
    i = 0
    while True:
        try:
            if not active_bots:
                await asyncio.sleep(0.8)
                continue
            bot = active_bots[i % len(active_bots)]
            emoji = emojis[i % len(emojis)] if emojis else "🔥"
            with suppress(Exception):
                await bot.set_message_reaction(
                    chat_id, msg_id,
                    reaction=[ReactionTypeEmoji(emoji=emoji)],
                )
            i += 1
            await asyncio.sleep(max(0.4, STATE.delays["react"]))
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.6)


async def auto_react_worker(message: Message):
    """!autoreact ON — har naye message pe rotation emoji."""
    cfg = REACT_LOOP.get(message.chat.id)
    if not cfg or not message.message_id:
        return
    with suppress(Exception):
        bot = message.bot or (active_bots[0] if active_bots else None)
        if bot:
            emoji = random.choice(cfg)
            await bot.set_message_reaction(
                message.chat.id, message.message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
# ==============================================================================
# SPAWN / FANOUT (multimode = command ko saare known GCs me chalao)
# ==============================================================================
_WORKER_MAP = {
    "nc": nc_cluster_worker,
    "spam": spam_worker,
    "slide": slide_worker,
    "fwd": fwd_cluster_worker,
    "media": media_worker,
    "pfp": pfp_worker,
}


def spawn(cid: int, cat: str, func, *args, save: bool = True):
    """Ek (cat, cid) par ek hi task — purana cancel, naya start."""
    STATE.cancel(cat, cid, save=False)
    if cid < 0:
        STATE.known_chats.add(cid)

    if save:
        STATE.persisted_tasks.setdefault(cat, {})[str(cid)] = list(args)

    task = asyncio.create_task(func(cid, *args))
    STATE.add_task(cat, cid, task)
    return task


def target_cids(message: Message) -> List[int]:
    """Multimode ON → sab known GCs; OFF → sirf current chat."""
    cid = message.chat.id
    if MULTIMODE:
        seen: List[int] = []
        for c in STATE.get_all_known_groups(cid):
            if c not in seen:
                seen.append(c)
        if cid not in seen:
            seen.insert(0, cid)
        return seen
    return [cid]


def fanout_note(message: Message) -> str:
    if MULTIMODE:
        n = len(target_cids(message))
        return f"\n📡 <b>MULTIMODE:</b> <code>{n} GCs</code> par armed!"
    return ""


def fanout(message: Message, cat: str, func, *args) -> int:
    cids = target_cids(message)
    for cid in cids:
        spawn(cid, cat, func, *args)
    return len(cids)


def fanout_stop(message: Message, cats: Tuple[str, ...]) -> int:
    cids = target_cids(message)
    stopped = 0
    for cid in cids:
        for cat in cats:
            if cat == "all":
                stopped += STATE.stop_all(cid)
            else:
                if STATE.tasks.get(cat, {}).get(cid):
                    STATE.cancel(cat, cid)
                    stopped += 1
    STATE.save_runtime_state()
    return stopped


# ==============================================================================
# GAMEOVER EXECUTE
# ==============================================================================
async def send_gameover_media_to_gc(cid: int, caption_html: str):
    gif = STATE.load_gameover_gif()
    bot = active_bots[0] if active_bots else None
    if not bot:
        return
    try:
        if gif:
            await bot.send_animation(cid, BufferedInputFile(gif, "go.mp4"),
                                     caption=caption_html, parse_mode=ParseMode.HTML)
        else:
            await bot.send_animation(cid, DEFAULT_GAMEOVER_GIF_URL,
                                     caption=caption_html, parse_mode=ParseMode.HTML)
    except Exception:
        with suppress(Exception):
            await bot.send_message(cid, caption_html, parse_mode=ParseMode.HTML)


async def notify_owner_dm(html_text: str):
    if not OWNER_ID:
        return
    for b in active_bots[:1]:
        with suppress(Exception):
            await b.send_message(OWNER_ID, html_text, parse_mode=ParseMode.HTML)
            return


async def execute_gameover(cid: int, custom_target: Optional[str] = None):
    """Sab tasks stop + gameover GIF + IST time + owner DM."""
    stopped = STATE.stop_all(cid)
    now = get_ist_str()
    target = _hesc(custom_target) if custom_target else "TARGET"
    gc_hits = STATE.chat_nc_counts.get(cid, 0)
    caption = (
        f"☠️ <b>G A M E · O V E R</b> ☠️\n\n"
        f"🎯 <b>Target:</b> <code>{target}</code>\n"
        f"🔥 <b>NC Hits:</b> <code>{gc_hits:,}</code>\n"
        f"🛑 <b>Tasks Stopped:</b> <code>{stopped}</code>\n"
        f"🕐 <b>Time:</b> <code>{now}</code>\n\n"
        f"<i>{START_BANNER_TEXT}</i>"
    )
    await send_gameover_media_to_gc(cid, caption)
    if custom_target:
        await notify_owner_dm(
            f"☠️ <b>GAME OVER</b>\n🎯 <code>{_hesc(custom_target)}</code>\n"
            f"🔥 Hits: <code>{gc_hits:,}</code>\n🕐 <code>{now}</code>"
        )


# ==============================================================================
# RESTORE PERSISTED TASKS · WATCHDOG · RAM GUARD
# ==============================================================================
async def restore_persisted_tasks():
    restored = 0
    for cat, mapping in list(STATE.persisted_tasks.items()):
        func = _WORKER_MAP.get(cat)
        if not func:
            continue
        for cid_s, args in list(mapping.items()):
            try:
                cid = int(cid_s)
                if not isinstance(args, list):
                    args = [args]
                task = asyncio.create_task(func(cid, *args))
                STATE.add_task(cat, cid, task)
                restored += 1
            except Exception as e:
                log_event("warn", "RESTORE", f"{cat}/{cid_s}: {e}")
    if restored:
        log_event("info", "RESTORE", f"{restored} persisted tasks resumed.")
        await notify_owner_dm(f"🔄 <b>Nik Engine V5</b> resumed <code>{restored}</code> tasks.")


async def task_watchdog_daemon():
    """Har 30s: persisted tasks agar dead ho to auto-respawn."""
    while True:
        try:
            await asyncio.sleep(30)
            for cat, mapping in list(STATE.persisted_tasks.items()):
                func = _WORKER_MAP.get(cat)
                if not func:
                    continue
                for cid_s, args in list(mapping.items()):
                    try:
                        cid = int(cid_s)
                        task = STATE.tasks.get(cat, {}).get(cid)
                        if task is None or task.done():
                            if not isinstance(args, list):
                                args = [args]
                            nt = asyncio.create_task(func(cid, *args))
                            STATE.add_task(cat, cid, nt)
                            log_event("info", "WATCHDOG", f"Respawned {cat} for {cid}.")
                    except Exception:
                        pass
            if STATE.unsaved_nc_hits > 0:
                STATE.unsaved_nc_hits = 0
                STATE.save_runtime_state()
            for cid in list(STATE.tracked_users.keys()):
                if len(STATE.tracked_users[cid]) > MAX_TRACKED_USERS_PER_CHAT:
                    STATE.tracked_users[cid].clear()
            gc.collect()
            ram = get_ram_mb()
            if ram > 380.0:
                STATE.tracked_users.clear()
                LOG_BUFFER.clear()
                gc.collect()
                log_event("warn", "RAM-GUARD", f"High RAM ({ram:.1f}MB) auto-purged.")
        except asyncio.CancelledError:
            break
        except Exception:
            pass


# ==============================================================================
# HEALTH WEB SERVER (keep-alive / HF / panel)
# ==============================================================================
async def handle_health(request):
    return web.json_response({
        "status": "online",
        "banner": START_BANNER_TEXT,
        "dev": "@Nikkkwa (Nik)",
        "ist_time": get_ist_str(),
        "active_bots": len(active_bots),
        "live_hz": fbe.get_live_hz(),
        "global_nc_count": STATE.global_nc_count,
        "ram_mb": round(get_ram_mb(), 2),
        "known_gcs": len(STATE.known_chats),
        "persisted_nc_chats": len(STATE.persisted_tasks["nc"]),
        "multimode": MULTIMODE,
    })


async def start_panel_or_hf_webserver():
    try:
        app = web.Application()
        app.router.add_get("/", handle_health)
        app.router.add_get("/health", handle_health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        log_event("info", "WEB", f"Keep-Alive Server bound to 0.0.0.0:{PORT}")
    except Exception as e:
        log_event("info", "VPS", f"Port {PORT} bind skipped ({e}). Panel mode.")


# ==============================================================================
# ADMIN HELPERS  (promote · restrict · purge · whois · parse_duration)
# ==============================================================================
def parse_duration(s: str) -> Optional[float]:
    """'30s' '5m' '2h' '1d' → seconds."""
    if not s:
        return None
    m = re.fullmatch(r"(\d+)\s*([smhd])?", s.strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


async def smart_promote(chat_id: int, target_user_id: int) -> bool:
    rights = {
        "can_change_info": True, "can_delete_messages": True,
        "can_restrict_members": True, "can_invite_users": True,
        "can_pin_messages": True, "can_promote_members": False,
    }
    for b in active_bots:
        try:
            await b.promote_chat_member(chat_id, target_user_id, **rights)
            return True
        except Exception:
            continue
    return False


async def smart_restrict(chat_id: int, target_user_id: int, mute: bool) -> bool:
    perms = (ChatPermissions(can_send_messages=False,
                             can_send_audios=False, can_send_documents=False,
                             can_send_photos=False, can_send_videos=False,
                             can_send_video_notes=False, can_send_voice_notes=False,
                             can_send_polls=False, can_send_other_messages=False,
                             can_add_web_page_previews=False)
             if mute else ChatPermissions(can_send_messages=True,
                                         can_send_audios=True, can_send_documents=True,
                                         can_send_photos=True, can_send_videos=True,
                                         can_send_video_notes=True, can_send_voice_notes=True,
                                         can_send_polls=True, can_send_other_messages=True,
                                         can_add_web_page_previews=True))
    for b in active_bots:
        try:
            await b.restrict_chat_member(chat_id, target_user_id, permissions=perms)
            return True
        except Exception:
            continue
    return False


async def purge_messages(message: Message, count: int) -> int:
    """Reply ke message se peeche `count` messages delete (admin rights chahiye)."""
    deleted = 0
    start = (message.reply_to_message.message_id if message.reply_to_message
             else message.message_id - 1)
    ids = list(range(max(1, start - count + 1), start + 1))
    for b in active_bots:
        try:
            # delete_messages (aiogram 3.10+) fallback → loop
            if hasattr(b, "delete_messages"):
                await b.delete_messages(message.chat.id, ids)
                return len(ids)
        except Exception:
            pass
        break
    for mid in ids:
        for b in active_bots:
            try:
                await b.delete_message(message.chat.id, mid)
                deleted += 1
                break
            except Exception:
                continue
        await asyncio.sleep(0.05)
    return deleted


def resolve_target(message: Message) -> Optional[Tuple[int, str]]:
    """Reply-to se ya @username se target dhundho."""
    rm = message.reply_to_message
    if rm and rm.from_user and not rm.from_user.is_bot:
        u = rm.from_user
        name = " ".join(x for x in (u.first_name, u.last_name) if x) or u.username or str(u.id)
        return u.id, name
    return None
# ==============================================================================
# PUBLIC COMMANDS
# ==============================================================================
@router.message(DCommand("start"))
async def cmd_public_start(message: Message):
    if message.chat.type != "private":
        return
    u = message.from_user
    if not u or not OWNER_ID or (u.id != OWNER_ID and u.id not in STATE.sudo):
        return
    await message.reply(
        f"👑 <b>NIKFYT ENGINE · V5 ULTIMATE</b>\n"
        f"<i>{START_BANNER_TEXT}</i>\n\n"
        f"⚡ <b>{len(active_bots)}</b> nodes online · prefix <code>{PREFIX}</code> "
        f"(+) <code>~</code> <code>/</code>\n"
        f"◈ <code>{PREFIX}menu</code> — full warfare menu",
        parse_mode=ParseMode.HTML,
    )


# ==============================================================================
# MENU  (ms1.py style — live cluster stats + poora command index)
# ==============================================================================
def _active_count(cat: str) -> int:
    return len([t for t in STATE.tasks.get(cat, {}).values() if t and not t.done()])


@router.message(DCommand("menu", "help"))
async def cmd_menu(message: Message):
    if message.chat.type == "private" and not (
            message.from_user and (message.from_user.id == OWNER_ID
                                   or message.from_user.id in STATE.sudo)):
        return
    if message.from_user and not (
            message.from_user.id == OWNER_ID or message.from_user.id in STATE.sudo):
        return

    ram_now = get_ram_mb()
    known_gc_count = len(STATE.get_all_known_groups(message.chat.id))
    gc_hits = STATE.chat_nc_counts.get(message.chat.id, 0)
    global_hits = STATE.global_nc_count
    P = PREFIX

    menu_text = (
        f"👑 <b>ɴɪᴋꜰʏᴛ ᴇɴɢɪɴᴇ · ᴠ5 ᴜʟᴛɪᴍᴀᴛᴇ</b> 👑\n"
        f"<i>✨ ᴀᴅᴠᴀɴᴄᴇ ʙᴏᴛ ʙʏ @Nikkkwa (Nik)</i>\n\n"
        f"📊 <b>ʟɪᴠᴇ ᴄʟᴜsᴛᴇʀ sᴛᴀᴛᴜs</b>\n"
        f"🤖 <b>ɴᴏᴅᴇs:</b> <code>{len(active_bots)}</code> • 🌍 <b>ᴋɴᴏᴡɴ ɢᴄs:</b> "
        f"<code>{known_gc_count}</code>\n"
        f"⚡ <b>sᴘᴇᴇᴅ:</b> <code>100ms ᴘɪᴘᴇʟɪɴᴇᴅ</code> "
        f"(<code>{STATE.delays['nc']}s</code> • <code>{fbe.get_live_hz()} Hz</code>)\n"
        f"🔥 <b>ɢᴄ ɴᴄ ʜɪᴛs:</b> <code>{gc_hits:,}</code> • 🌐 <b>ɢʟᴏʙᴀʟ ɴᴄ:</b> "
        f"<code>{global_hits:,}</code>\n"
        f"🔄 <b>ʀᴜɴɴɪɴɢ:</b> <code>{_active_count('nc')} ɴᴄ</code> • "
        f"<code>{_active_count('spam')} sᴘᴍ</code> • "
        f"<code>{_active_count('fwd')} ꜰᴡᴅ</code> • "
        f"<code>{_active_count('slide')} sʟᴅ</code> • 💾 <code>{ram_now:.1f} MB</code>\n"
        f"📡 <b>ᴍᴜʟᴛɪᴍᴏᴅᴇ:</b> {'🟢 ON' if MULTIMODE else '🔴 OFF'}\n\n"

        f"𝟏. 🌪️ <b>sɪɴɢʟᴇ-ɢᴄ ɴᴄ ʀᴇᴀᴄᴛᴏʀ (25 sᴛʏʟᴇs)</b>\n"
        f"◈ <code>{P}nmix &lt;txt&gt;</code> / <code>{P}mix</code> » ᴍɪx all walls+fonts\n"
        f"◈ <code>{P}nsmix &lt;txt&gt;</code> » ɴᴄ + ʀᴀɪᴅ sᴘᴀᴍ ᴄᴏᴍʙᴏ\n"
        f"◈ <code>{P}n1</code> to <code>{P}n25 &lt;txt&gt;</code> » 25 unique designs\n"
        f"◈ <code>{P}n</code> / <code>{P}nemo</code> » ᴄʟᴀssɪᴄ 7-ʟɪɴᴇ 𒐫 ᴡᴀʟʟ\n"
        f"◈ <code>{P}tn &lt;txt&gt;</code> » ᴛᴇᴍᴘʟᴀᴛᴇ ɴᴄ · <code>{P}ncloop</code> » ɢᴏᴅ ʟᴏᴏᴘ\n"
        f"◈ <code>{P}timenc &lt;txt&gt;</code> » ʟɪᴠᴇ ᴄʟᴏᴄᴋ ɴᴄ\n"
        f"◈ <code>{P}raidnc [name]</code> (ʀᴇᴘʟʏ) / <code>{P}longnc</code> / <code>{P}nc2</code>\n"
        f"◈ <code>{P}crashnc|glitchnc|aninc|hnc|inc|jnc|fucknc|ftnc &lt;txt&gt;</code>\n"
        f"◈ <code>{P}dn</code> / <code>{P}dlyn &lt;ms&gt;</code> » sᴛᴏᴘ / ɴᴄ ᴅᴇʟᴀʏ\n"
        f"◈ 📑 <code>{P}menu1</code> to <code>{P}menu5</code> » ᴄᴀᴛᴇɢᴏʀʏ ᴘᴀɴᴇʟs\n\n"

        f"𝟐. 🌍 <b>ɢʟᴏʙᴀʟ ɴᴄ (ᴀʟʟ ɢᴄs)</b>\n"
        f"◈ <code>{P}gmix &lt;txt&gt;</code> / <code>{P}gnc &lt;txt&gt;</code> / "
        f"<code>{P}gn1</code>-<code>{P}gn15</code>\n"
        f"◈ <code>{P}gns</code> / <code>{P}gnsmix</code> / <code>{P}dgnc</code>\n\n"

        f"𝟑. 💣 <b>sᴘᴀᴍ · sʟɪᴅᴇ · ʙʀᴏᴀᴅᴄᴀsᴛ</b>\n"
        f"◈ <code>{P}s &lt;txt&gt;</code> / <code>{P}rs &lt;txt&gt;</code> (ʀᴀɪᴅ) / "
        f"<code>{P}ts &lt;txt&gt;</code> (ᴛᴇᴍᴘʟᴀᴛᴇ)\n"
        f"◈ <code>{P}tsl</code> / <code>{P}sls</code> (ʀᴇᴘʟʏ) / <code>{P}spam1</code> (ᴛᴀʀɢᴇᴛ)\n"
        f"◈ <code>{P}swipe [pfx]</code> (ʀᴇᴘʟʏ) / <code>{P}crashtextraid</code> / "
        f"<code>{P}picraid</code> (ʀᴇᴘʟʏ ᴘʜᴏᴛᴏ)\n"
        f"◈ <code>{P}c</code> (ᴄᴏᴘʏ) / <code>{P}bc &lt;txt&gt;</code> / "
        f"<code>{P}ds</code> / <code>{P}dtsl</code> / <code>{P}stoppicraid</code>\n\n"

        f"𝟒. 🔁 <b>ʀᴇᴘᴇᴀᴛ ꜰᴏʀᴡᴀʀᴅ ʟᴏᴏᴘ</b>\n"
        f"◈ <code>{P}fwd</code> / <code>{P}f</code> / <code>{P}cfwd</code> (ʀᴇᴘʟʏ)\n"
        f"◈ <code>{P}nfwd &lt;txt&gt;</code> / <code>{P}gfwd</code> / "
        f"<code>{P}dfwd</code> / <code>{P}dgfwd</code>\n\n"

        f"𝟓. ☠️ <b>ɢᴀᴍᴇ ᴏᴠᴇʀ</b>\n"
        f"◈ <code>{P}gameover [name]</code> » ɢɪꜰ + ɪsᴛ + sᴛᴏᴘ\n"
        f"◈ <code>{P}setgo</code> (ʀᴇᴘʟʏ) » sᴀᴠᴇ ᴄᴜsᴛᴏᴍ ɢɪꜰ\n\n"

        f"𝟔. 🖼️ <b>ᴘꜰᴘ &amp; ᴍᴇᴅɪᴀ ʀᴏᴛᴀᴛɪᴏɴ</b>\n"
        f"◈ <code>{P}botpfp</code> / <code>{P}sv</code> / <code>{P}gsv</code> / "
        f"<code>{P}p</code> / <code>{P}gp</code> / <code>{P}dp</code>\n"
        f"◈ <code>{P}stk</code> / <code>{P}gif</code> / <code>{P}pic</code> / "
        f"<code>{P}vc</code> / <code>{P}dstk</code> / <code>{P}rmall</code>\n\n"

        f"𝟕. 🛡️ <b>ᴀᴅᴍɪɴ · sʟɪᴅᴇ · ʀᴇᴀᴄᴛ</b>\n"
        f"◈ <code>{P}m</code>/<code>{P}um</code> (ʀᴇᴘʟʏ) / <code>{P}mlist</code> / "
        f"<code>{P}prom</code> / <code>{P}prombots</code>\n"
        f"◈ <code>{P}purge &lt;n&gt;</code> / <code>{P}lock</code> / <code>{P}unlock</code>\n"
        f"◈ <code>{P}react &lt;emoji&gt;</code> (ʀᴇᴘʟʏ) / <code>{P}stopreact</code> / "
        f"<code>{P}autoreact &lt;emoji&gt;</code>\n"
        f"◈ <code>{P}rnorv</code>(ʀᴇᴘʟʏ) / <code>{P}rct &lt;emo&gt;</code>(ʀᴇᴘʟʏ) / "
        f"<code>{P}swp &lt;txt&gt;</code> » ɪɴᴛᴇʀᴄᴇᴘᴛᴏʀs\n"
        f"◈ <code>{P}drnorv</code> / <code>{P}drct</code> / <code>{P}dswp</code> » ꜱᴛᴏᴘ\n"
        f"◈ <code>{P}gmute [time]</code>(ʀᴇᴘʟʏ) / <code>{P}gunmute</code> » ɢʟᴏʙᴀʟ ᴍᴜᴛᴇ\n"
        f"◈ <code>{P}whois</code> (ʀᴇᴘʟʏ)\n\n"

        f"𝟖. ⚙️ <b>sʏsᴛᴇᴍ · ᴛᴏᴋᴇɴs · sᴜᴅᴏ</b>\n"
        f"◈ <code>{P}mm</code> / <code>{P}multimode on|off|list</code> » ᴀʟʟ-GC ʙʀᴏᴀᴅᴄᴀsᴛ\n"
        f"◈ <code>{P}addtoken</code> / <code>{P}remtoken</code> / <code>{P}tokens</code>\n"
        f"◈ <code>{P}setbotname</code> / <code>{P}setbotbio</code> / <code>{P}setdelay &lt;sec&gt;</code>\n"
        f"◈ <code>{P}sudo</code>(ʀᴇᴘʟʏ) / <code>{P}sudolist</code> / <code>{P}clearsudo</code>\n"
        f"◈ <code>{P}throttle max|safe|off</code> / <code>{P}fb</code> / <code>{P}count</code>\n"
        f"◈ <code>{P}tasks</code> / <code>{P}logs</code> / <code>{P}stopall</code> / "
        f"<code>{P}leave</code> / <code>{P}o</code>\n"
        f"◈ <code>{P}stat</code> / <code>{P}ping</code> / <code>{P}uptime</code> / "
        f"<code>{P}link</code> / <code>{P}bots</code>\n"
        f"◈ <code>{P}prefix &lt;p&gt;</code> » ʙᴀᴅʟᴏ (ʜᴍᴇsʜᴀ <code>!</code> <code>~</code> "
        f"<code>/</code> ᴄʜᴀʟᴇɴɢᴇ)\n"
        f"◈ <code>{P}bc &lt;txt&gt;</code> » ᴏᴡɴᴇʀ ʙʀᴏᴀᴅᴄᴀsᴛ"
    )
    await message.reply(menu_text, parse_mode=ParseMode.HTML)


# ==============================================================================
# PREFIX / TOKENS / SUDO
# ==============================================================================
@router.message(DCommand("prefix", "setprefix"), IsOwner())
async def cmd_prefix(message: Message, command: CommandObject):
    global PREFIX
    new = (command.args or "").strip()[:4]
    if not new:
        return await message.reply(
            f"◈ Usage: <code>{PREFIX}prefix ~</code>\n"
            f"◈ Current: <code>{PREFIX}</code> (fallback <code>!</code> <code>~</code> "
            f"<code>/</code> always on)",
            parse_mode=ParseMode.HTML)
    PREFIX = new
    _RT["prefix"] = new
    _save_rt()
    await message.reply(f"✅ Prefix → <code>{_hesc(new)}</code>  "
                        f"(+ <code>!</code> <code>~</code> <code>/</code> always)",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("tokens", "listtokens", "listbots"), IsSudo())
async def cmd_tokens(message: Message):
    slots = build_tokens()
    lines = []
    for i, t in enumerate(slots, 1):
        if t:
            bid = ""
            with suppress(Exception):
                for b in active_bots:
                    if LIVE_BOTS.get(t) is b:
                        bid = f" · id <code>{b.id}</code>"
                        break
            status = "🟢 live" if any(LIVE_BOTS.get(t) is b for b in active_bots) else "🟠 saved"
            lines.append(f"  #{i} <code>{_hesc(t[:14])}…</code> {status}{bid}")
        else:
            lines.append(f"  #{i} — empty —")
    await message.reply(
        f"🤖 <b> TOKEN SLOTS ({sum(1 for x in slots if x)}/{MAX_TOKENS})</b>\n" +
        "\n".join(lines) +
        f"\n\n◈ <code>{PREFIX}addtoken &lt;token&gt;</code> · "
        f"<code>{PREFIX}remtoken &lt;no&gt;</code>",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("addtoken", "addtokens"), IsOwner())
async def cmd_addtoken(message: Message, command: CommandObject):
    raw = (command.args or "").strip()
    if not raw:
        return await message.reply(f"◈ Usage: <code>{PREFIX}addtoken &lt;bot_token&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    new_tokens = [t.strip() for t in re.split(r"[\s,]+", raw) if t.strip()]
    slots = build_tokens()
    added = 0
    for tok in new_tokens:
        if tok in slots:
            continue
        for i in range(1, MAX_TOKENS):
            if not slots[i]:
                _RT["overrides"][i - 1] = tok
                slots[i] = tok
                added += 1
                break
    _save_rt()
    STATE.save_runtime_state()
    await message.reply(f"✅ <b>{added}</b> token slot me save hue. "
                        f"Restart pe boot ho jayenge · abhi live karne ke liye "
                        f"<code>{PREFIX}o</code> (optimize) ya restart karo.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("remtoken", "remtokens", "removetoken", "deltoken"), IsOwner())
async def cmd_remtoken(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    slots = build_tokens()
    if not arg:
        return await message.reply(f"◈ Usage: <code>{PREFIX}remtoken &lt;1-10&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    try:
        no = int(arg)
        if no < 2 or no > MAX_TOKENS:
            raise ValueError
    except ValueError:
        return await message.reply("⚠️ Slot #1 (MAIN) protected — sirf 2..10.",
                                   parse_mode=ParseMode.HTML)
    tok = slots[no - 1]
    _RT["overrides"][no - 2] = ""
    _save_rt()
    with suppress(Exception):
        b = LIVE_BOTS.pop(tok, None)
        if b in active_bots:
            active_bots.remove(b)
        if b:
            asyncio.create_task(b.session.close())
    await message.reply(f"🗑️ Slot #{no} cleared.", parse_mode=ParseMode.HTML)


@router.message(DCommand("sudo", "dsudo"), IsOwner())
async def cmd_sudo(message: Message, command: CommandObject):
    target = resolve_target(message)
    if not target:
        return await message.reply("◈ Reply karo kisi ke message pe <code>" + PREFIX +
                                   "sudo</code>", parse_mode=ParseMode.HTML)
    uid, name = target
    if command.command.lower().startswith("d") or (command.args or "").lower() == "del":
        STATE.sudo.discard(uid)
        STATE.save_sudo()
        return await message.reply(f"🗑️ <code>{uid}</code> sudo se hata diya.",
                                   parse_mode=ParseMode.HTML)
    STATE.sudo.add(uid)
    STATE.save_sudo()
    await message.reply(f"👑 <b>{_hesc(name)}</b> (<code>{uid}</code>) ab sudo hai.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("delsudo"), IsOwner())
async def cmd_delsudo(message: Message):
    target = resolve_target(message)
    if not target:
        return await message.reply("◈ Reply karo sudo ke message pe.", parse_mode=ParseMode.HTML)
    uid, name = target
    STATE.sudo.discard(uid)
    STATE.save_sudo()
    await message.reply(f"🗑️ <code>{_hesc(name)}</code> sudo se hata diya.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("sudolist"), IsSudo())
async def cmd_sudolist(message: Message):
    ids = sorted(STATE.sudo)
    body = "\n".join(f"  <code>{i}</code>" for i in ids) or "  — none —"
    await message.reply(f"👑 <b>SUDO LIST</b>\n  owner: <code>{OWNER_ID}</code>\n{body}",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("clearsudo"), IsOwner())
async def cmd_clearsudo(message: Message):
    STATE.sudo.clear()
    STATE.save_sudo()
    await message.reply("🧹 Sab sudo clear.", parse_mode=ParseMode.HTML)
# ==============================================================================
# NC COMMANDS — SINGLE GC  (n · n1..n15 · mix · nsmix · tn · timenc · raidnc ·
#                           longnc · crashnc(1-3) · glitchnc(1-3) · ani/h/i/j/fuck/ft)
# ==============================================================================
def _base_arg(command: CommandObject) -> str:
    return (command.args or "").strip() or "GROUP"


async def _nc_ack(message: Message, label: str, base: str, mode: str,
                  extra: str = ""):
    await message.reply(
        f"🌪️ <b>{label}</b> started\n"
        f"🎯 Target: <code>{_hesc(base)}</code>\n"
        f"🤖 Bots: <code>{len(active_bots)}</code> · mode: <code>{mode}</code>\n"
        f"⚡ Speed: <code>100ms LRU pipeline</code> · non-stop 24/7"
        f"{extra}{fanout_note(message)}",
        parse_mode=ParseMode.HTML,
    )


@router.message(DCommand("n", "nemo", "nken", "ns"), IsSudo())
async def cmd_nc(message: Message, command: CommandObject):
    base = _base_arg(command)
    cids = fanout(message, "nc", nc_cluster_worker, base, "wall")
    mode = "wall"
    label = "CLASSIC 𒐫 WALL NC"
    extra = ""
    if command.command.lower() == "ns":
        fanout(message, "spam", spam_worker, base, "rs")
        label = "NC + RAID SPAM COMBO"
        extra = "\n💣 raid-spam bhi armed"
    await _nc_ack(message, label, base, mode, extra)


@router.message(DCommand(*[f"n{i}" for i in range(1, 16)]), IsSudo())
async def cmd_nc_styles(message: Message, command: CommandObject):
    key = command.command.lower()
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "style", key)
    await _nc_ack(message, f"STYLE {key.upper()}", base, key)


@router.message(DCommand("nmix", "mix"), IsSudo())
async def cmd_nc_mix(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "mix")
    await _nc_ack(message, "15-STYLE MIX NC", base, "mix")


@router.message(DCommand("nsmix", "mixspam"), IsSudo())
async def cmd_nc_smix(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "mix")
    fanout(message, "spam", spam_worker, base, "rs")
    await _nc_ack(message, "MIX NC + RAID SPAM COMBO", base, "mix",
                  extra="\n💣 raid-spam bhi armed")


@router.message(DCommand("tn"), IsSudo())
async def cmd_template_nc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "template", None, read_template())
    await _nc_ack(message, "TEMPLATE NC", base, "template")


@router.message(DCommand("timenc"), IsSudo())
async def cmd_time_nc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "time")
    sample = generate_single_title(base, "time", 0)
    await _nc_ack(message, "TIME NC", base, "time",
                  extra=f"\n🕐 Sample: <code>{_hesc(sample)}</code>")


@router.message(DCommand("raidnc"), IsSudo())
async def cmd_raid_nc(message: Message, command: CommandObject):
    base = (command.args or "").strip()
    if not base:
        t = resolve_target(message)
        if t:
            base = t[1]
    base = base or "GROUP"
    fanout(message, "nc", nc_cluster_worker, base, "raid")
    await _nc_ack(message, "RAID NC", base, "raid")


@router.message(DCommand("longnc"), IsSudo())
async def cmd_long_nc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "long")
    await _nc_ack(message, "LONG WALL NC", base, "long")


@router.message(DCommand("crashnc"), IsSudo())
async def cmd_crashnc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "crash")
    await _nc_ack(message, "CRASH NC", base, "crash")


@router.message(DCommand("crashnc2"), IsSudo())
async def cmd_crashnc2(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "crash2")
    await _nc_ack(message, "CRASH NC 2", base, "crash2")


@router.message(DCommand("crashnc3"), IsSudo())
async def cmd_crashnc3(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "crash3")
    await _nc_ack(message, "CRASH NC 3 (MAX)", base, "crash3")


@router.message(DCommand("glitchnc"), IsSudo())
async def cmd_glitchnc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "glitch")
    await _nc_ack(message, "GLITCH NC", base, "glitch")


@router.message(DCommand("glitchnc2"), IsSudo())
async def cmd_glitchnc2(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "glitch2")
    await _nc_ack(message, "GLITCH NC 2", base, "glitch2")


@router.message(DCommand("aninc"), IsSudo())
async def cmd_aninc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "ani")
    await _nc_ack(message, "ANI NC", base, "ani")


@router.message(DCommand("hnc"), IsSudo())
async def cmd_hnc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "h")
    await _nc_ack(message, "HUNT NC", base, "h")


@router.message(DCommand("inc"), IsSudo())
async def cmd_inc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "i")
    await _nc_ack(message, "INSANE NC", base, "i")


@router.message(DCommand("jnc"), IsSudo())
async def cmd_jnc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "j")
    await _nc_ack(message, "JUNGLE NC", base, "j")


@router.message(DCommand("fucknc"), IsSudo())
async def cmd_fucknc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "fuck")
    await _nc_ack(message, "FUCK NC", base, "fuck")


@router.message(DCommand("ftnc"), IsSudo())
async def cmd_ftnc(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "ft")
    await _nc_ack(message, "FINAL THRONE NC", base, "ft")


@router.message(DCommand("nc2"), IsSudo())
async def cmd_nc2(message: Message, command: CommandObject):
    base = _base_arg(command)
    fanout(message, "nc", nc_cluster_worker, base, "mix")
    fanout(message, "spam", spam_worker, base, "static")
    await _nc_ack(message, "NC2 · MIX + STATIC SPAM", base, "mix",
                  extra="\n💣 static spam bhi armed")


@router.message(DCommand("ncloop"), IsSudo())
async def cmd_ncloop(message: Message, command: CommandObject):
    """Infinite classic wall loop — max pacing, non-stop."""
    base = _base_arg(command)
    fbe.set_mode("max", len(active_bots))
    fanout(message, "nc", nc_cluster_worker, base, "wall")
    await _nc_ack(message, "NC GOD LOOP (MAX PACING)", base, "wall",
                  extra="\n🧬 throttle → max")


@router.message(DCommand("swipe"), IsSudo())
async def cmd_swipe(message: Message, command: CommandObject):
    """Reply-based swipe slide — custom prefix ke saath mention spam."""
    target = resolve_target(message)
    if not target:
        return await message.reply(
            f"◈ Target ke message pe reply karo → <code>{PREFIX}swipe [prefix]</code>",
            parse_mode=ParseMode.HTML)
    uid, name = target
    prefix = (command.args or "").strip() or None
    fanout(message, "slide", slide_worker, uid, name, prefix)
    await message.reply(
        f"👆 <b>SWIPE</b> armed → <code>{_hesc(name)}</code>"
        + (f" · prefix <code>{_hesc(prefix)}</code>" if prefix else "")
        + fanout_note(message),
        parse_mode=ParseMode.HTML)


@router.message(DCommand("picraid"), IsSudo())
async def cmd_picraid(message: Message):
    """Reply to photo → usi photo ki full-cluster raid."""
    rm = message.reply_to_message
    if not rm or not rm.photo:
        return await message.reply(
            f"◈ Pehle photo pe reply karo → <code>{PREFIX}picraid</code>",
            parse_mode=ParseMode.HTML)
    fid = rm.photo[-1].file_id
    items = STATE.media["photo"].setdefault(message.chat.id, [])
    if fid not in items:
        items.append(fid)
        del items[:-MAX_MEDIA_PER_CHAT]
    fanout(message, "media", media_worker, "photo")
    await message.reply(f"🖼️ <b>PIC RAID</b> armed · photo loop started"
                        f"{fanout_note(message)}", parse_mode=ParseMode.HTML)


@router.message(DCommand("stoppicraid", "dpicraid"), IsSudo())
async def cmd_stop_picraid(message: Message):
    stopped = fanout_stop(message, ("media",))
    await message.reply(f"🛑 Picraid stopped · <code>{stopped}</code>.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("setdelay"), IsSudo())
async def cmd_setdelay(message: Message, command: CommandObject):
    """!setdelay <sec> — spam/slide/rafm sabka ek saath delay."""
    try:
        sec = float((command.args or "0.1").strip())
        sec = max(0.0, sec)
    except ValueError:
        return await message.reply(
            f"◈ Usage: <code>{PREFIX}setdelay &lt;sec&gt;</code>  (ex: 0.1 = 100ms)",
            parse_mode=ParseMode.HTML)
    for k in ("spam", "rspm", "slide"):
        STATE.delays[k] = sec
    STATE.save_runtime_state()
    await message.reply(f"⏱ Global spam/slide delay → <code>{sec}s</code>",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("setbotname"), IsOwner())
async def cmd_setbotname(message: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        return await message.reply(f"◈ Usage: <code>{PREFIX}setbotname &lt;name&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    ok = 0
    for b in active_bots:
        with suppress(Exception):
            await b.set_my_name(name)
            ok += 1
    await message.reply(f"✏️ Name updated on <code>{ok}/{len(active_bots)}</code> bots → "
                        f"<b>{_hesc(name)}</b>", parse_mode=ParseMode.HTML)


@router.message(DCommand("setbotbio"), IsOwner())
async def cmd_setbotbio(message: Message, command: CommandObject):
    bio = (command.args or "").strip()
    if not bio:
        return await message.reply(f"◈ Usage: <code>{PREFIX}setbotbio &lt;bio&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    ok = 0
    for b in active_bots:
        with suppress(Exception):
            await b.set_my_short_description(bio)
            ok += 1
    await message.reply(f"📝 Bio updated on <code>{ok}/{len(active_bots)}</code> bots.",
                        parse_mode=ParseMode.HTML)


# ==============================================================================
# INTERCEPTORS  (ms1.py style — auto reply / auto react / auto swipe)
# ==============================================================================
@router.message(DCommand("rnorv", "rct", "swp"), IsSudo())
async def cmd_interceptors(message: Message, command: CommandObject):
    cmd = command.command.lower()
    if cmd == "swp":
        if not command.args:
            return await message.reply(f"◈ Usage: <code>{PREFIX}swp &lt;text&gt;</code>",
                                       parse_mode=ParseMode.HTML)
        STATE.targets["swipe"][message.chat.id] = command.args
        STATE.save_runtime_state()
        return await message.reply("⚡ <b>Auto-Intercept Swipe</b> active.",
                                   parse_mode=ParseMode.HTML)

    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await message.reply("◈ Reply to a user.", parse_mode=ParseMode.HTML)
    uid = message.reply_to_message.from_user.id

    if cmd == "rnorv":
        STATE.targets["reply"][uid] = True
        STATE.save_runtime_state()
        await message.reply("🗣️ <b>Auto-Insult</b> active for <code>{uid}</code>.",
                            parse_mode=ParseMode.HTML)
    elif cmd == "rct":
        if not command.args:
            return await message.reply(f"◈ Provide an emoji: <code>{PREFIX}rct 🔥</code>",
                                       parse_mode=ParseMode.HTML)
        STATE.targets["react"][uid] = command.args.strip()
        STATE.save_runtime_state()
        await message.reply(f"✅ <b>Auto-React</b> active for <code>{uid}</code>.",
                            parse_mode=ParseMode.HTML)


@router.message(DCommand("drnorv", "drct", "dswp"), IsSudo())
async def cmd_stop_interceptors(message: Message, command: CommandObject):
    cmd = command.command.lower()
    if cmd == "dswp":
        STATE.targets["swipe"].pop(message.chat.id, None)
    elif cmd == "drnorv":
        if message.reply_to_message and message.reply_to_message.from_user:
            STATE.targets["reply"].pop(message.reply_to_message.from_user.id, None)
        else:
            STATE.targets["reply"].clear()
    elif cmd == "drct":
        if message.reply_to_message and message.reply_to_message.from_user:
            STATE.targets["react"].pop(message.reply_to_message.from_user.id, None)
        else:
            STATE.targets["react"].clear()
    STATE.save_runtime_state()
    await message.reply(f"🛑 <code>{cmd[1:].upper()}</code> deactivated.",
                        parse_mode=ParseMode.HTML)


async def interceptor_engine(message: Message):
    """Har message pe: auto-insult / auto-react / auto-swipe interceptors."""
    u = message.from_user
    if not u or u.is_bot:
        return
    if message.chat.type == "private":
        return

    # auto-swipe (chat-level): target ki har message pe custom slide
    swipe_text = STATE.targets["swipe"].get(message.chat.id)
    if swipe_text and (u.id != OWNER_ID and u.id not in STATE.sudo):
        name = " ".join(x for x in (u.first_name, u.last_name) if x) or u.username or str(u.id)
        with suppress(Exception):
            bot = message.bot or (active_bots[0] if active_bots else None)
            if bot:
                mention = f'<a href="tg://user?id={u.id}">{_hesc(name)}</a>'
                msg = f"{mention} {swipe_text}{GAP_100}{mention} {swipe_text}"
                await bot.send_message(message.chat.id, msg, parse_mode=ParseMode.HTML)

    # auto-insult reply (uid-level)
    if STATE.targets["reply"].get(u.id) and (u.id != OWNER_ID and u.id not in STATE.sudo):
        with suppress(Exception):
            bot = message.bot or (active_bots[0] if active_bots else None)
            if bot:
                text = f"{random.choice(RAID_TEXTS)} {random.choice(EMOJIS)}"
                await bot.send_message(message.chat.id, text)

    # auto-react (uid-level)
    emoji = STATE.targets["react"].get(u.id)
    if emoji and message.message_id:
        with suppress(Exception):
            bot = message.bot or (active_bots[0] if active_bots else None)
            if bot:
                await bot.set_message_reaction(
                    message.chat.id, message.message_id,
                    reaction=[ReactionTypeEmoji(emoji=str(emoji)[:2] or "🔥")],
                )


# ==============================================================================
# GMUTE (global mute — sab GCs me)
# ==============================================================================
@router.message(DCommand("gmute"), IsOwner())
async def cmd_gmute(message: Message, command: CommandObject):
    target = resolve_target(message)
    if not target:
        return await message.reply(f"◈ Reply → <code>{PREFIX}gmute [time]</code>",
                                   parse_mode=ParseMode.HTML)
    uid, name = target
    if uid == OWNER_ID:
        return await message.reply("⚠️ Owner ko global mute nahi kar sakte.",
                                   parse_mode=ParseMode.HTML)
    secs = parse_duration((command.args or "").split()[0]) if command.args else None
    STATE.global_muted[uid] = (time.time() + secs) if secs else 0.0
    STATE.save_runtime_state()
    suffix = f" for <code>{command.args.strip()}</code>" if secs else " (permanent)"
    await message.reply(f"🌐 <b>GLOBAL MUTE ON</b> — <b>{_hesc(name)}</b>{suffix}",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("gunmute"), IsOwner())
async def cmd_gunmute(message: Message):
    target = resolve_target(message)
    if not target:
        return await message.reply(f"◈ Reply → <code>{PREFIX}gunmute</code>",
                                   parse_mode=ParseMode.HTML)
    uid, name = target
    STATE.global_muted.pop(uid, None)
    STATE.save_runtime_state()
    await message.reply(f"🔊 Global mute removed: <b>{_hesc(name)}</b>",
                        parse_mode=ParseMode.HTML)


async def global_mute_watcher():
    """Expiry pe global mutes hatao."""
    while True:
        try:
            await asyncio.sleep(20)
            now = time.time()
            for uid, exp in list(STATE.global_muted.items()):
                if exp and exp <= now:
                    STATE.global_muted.pop(uid, None)
                    log_event("info", "GMUTE", f"Expired: {uid}")
        except asyncio.CancelledError:
            break
        except Exception:
            pass


# ==============================================================================
# GLOBAL NC  (sare known GCs ek saath)
# ==============================================================================
async def _gnc_ack(message: Message, label: str, base: str, mode: str):
    n = len(STATE.get_all_known_groups(message.chat.id))
    await message.reply(
        f"🌍 <b>{label}</b> started\n"
        f"🎯 Target: <code>{_hesc(base)}</code>\n"
        f"🌐 GCs: <code>{n}</code> · 🤖 Bots: <code>{len(active_bots)}</code>\n"
        f"⚡ <code>100ms LRU pipeline</code> · non-stop",
        parse_mode=ParseMode.HTML,
    )


async def _global_nc(message: Message, base: str, mode: str,
                     style_key: Optional[str] = None, tmpl: Optional[Any] = None):
    cids = STATE.get_all_known_groups(message.chat.id)
    for cid in cids:
        spawn(cid, "nc", nc_cluster_worker, base, mode, style_key, tmpl)
    return len(cids)


@router.message(DCommand("gnc", "gmix"), IsSudo())
async def cmd_global_nc(message: Message, command: CommandObject):
    base = _base_arg(command)
    mode = "mix" if command.command.lower() == "gmix" else "wall"
    await _global_nc(message, base, mode)
    await _gnc_ack(message, "GLOBAL MIX NC" if mode == "mix" else "GLOBAL WALL NC",
                   base, mode)


@router.message(DCommand(*[f"gn{i}" for i in range(1, 16)]), IsSudo())
async def cmd_global_nc_styles(message: Message, command: CommandObject):
    key = command.command.lower().replace("g", "n", 1)
    base = _base_arg(command)
    await _global_nc(message, base, "style", key)
    await _gnc_ack(message, f"GLOBAL {key.upper()}", base, key)


@router.message(DCommand("gns", "gnsmix"), IsSudo())
async def cmd_global_nc_spam(message: Message, command: CommandObject):
    base = _base_arg(command)
    await _global_nc(message, base, "mix")
    for cid in STATE.get_all_known_groups(message.chat.id):
        spawn(cid, "spam", spam_worker, base, "rs")
    await _gnc_ack(message, "GLOBAL NC + RAID SPAM", base, "mix")


@router.message(DCommand("dgnc", "dgmix", "dgns", "dgnmix"), IsSudo())
async def cmd_stop_global_nc(message: Message):
    stopped = 0
    for cid in STATE.get_all_known_groups(message.chat.id):
        if STATE.tasks.get("nc", {}).get(cid):
            STATE.cancel("nc", cid)
            stopped += 1
    STATE.save_runtime_state()
    await message.reply(f"🛑 Global NC stopped in <code>{stopped}</code> GCs.",
                        parse_mode=ParseMode.HTML)


# ==============================================================================
# NC STOP / DELAY / THROTTLE / COUNT
# ==============================================================================
@router.message(DCommand("dn", "dnc", "stopnc", "stopn", "dns"), IsSudo())
async def cmd_dnc(message: Message):
    stopped = fanout_stop(message, ("nc",))
    await message.reply(f"🛑 NC stopped · <code>{stopped}</code> tasks.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("dlyn"), IsSudo())
async def cmd_dlyn(message: Message, command: CommandObject):
    try:
        ms = float((command.args or "100").strip())
        STATE.delays["nc"] = max(0.0, ms / 1000.0)
    except ValueError:
        return await message.reply(f"◈ Usage: <code>{PREFIX}dlyn &lt;ms&gt;</code> "
                                   f"(0 = auto pipeline)",
                                   parse_mode=ParseMode.HTML)
    STATE.save_runtime_state()
    await message.reply(f"⚡ NC delay → <code>{STATE.delays['nc']}s</code> "
                        f"(<code>{ms:.0f}ms</code>)", parse_mode=ParseMode.HTML)


@router.message(DCommand("dlyspam", "dlys"), IsSudo())
async def cmd_dlyspam(message: Message, command: CommandObject):
    try:
        sec = float((command.args or "0.1").strip())
        STATE.delays["spam"] = max(0.0, sec)
        STATE.delays["rspm"] = max(0.0, sec)
    except ValueError:
        return await message.reply(f"◈ Usage: <code>{PREFIX}dlys &lt;sec&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    STATE.save_runtime_state()
    await message.reply(f"💣 Spam delay → <code>{sec}s</code>", parse_mode=ParseMode.HTML)


@router.message(DCommand("dlyslide", "dsl"), IsSudo())
async def cmd_dlyslide(message: Message, command: CommandObject):
    try:
        sec = float((command.args or "0.1").strip())
        STATE.delays["slide"] = max(0.0, sec)
    except ValueError:
        return await message.reply(f"◈ Usage: <code>{PREFIX}dsl &lt;sec&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    STATE.save_runtime_state()
    await message.reply(f"🎯 Slide delay → <code>{sec}s</code>", parse_mode=ParseMode.HTML)


@router.message(DCommand("throttle"), IsSudo())
async def cmd_throttle(message: Message, command: CommandObject):
    mode = (command.args or "max").strip().lower()
    if mode not in ("max", "turbo", "on", "fast", "safe", "stable", "balanced",
                    "off", "disable", "raw"):
        return await message.reply(
            f"◈ <code>{PREFIX}throttle max|safe|off</code>\n"
            f"  max = fastest zero-flood · safe = gentle · off = raw",
            parse_mode=ParseMode.HTML)
    fbe.set_mode(mode, len(active_bots))
    await message.reply(
        f"🎛️ Throttle → <code>{fbe.throttle_mode}</code> · "
        f"bypass <b>{'ON' if fbe.bypass_active else 'OFF'}</b> · "
        f"gap <code>{fbe.per_bot_nc_gap}s</code> · "
        f"tick <code>{fbe.cluster_nc_tick}s</code>",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("fb", "bypass"), IsSudo())
async def cmd_flood_bypass(message: Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    if arg in ("on", "off", "1", "0"):
        fbe.bypass_active = arg in ("on", "1")
        fbe.set_mode("max" if fbe.bypass_active else "off", len(active_bots))
    await message.reply(
        f"🧬 Flood-bypass: <b>{'ON' if fbe.bypass_active else 'OFF'}</b> · "
        f"mode <code>{fbe.throttle_mode}</code> · "
        f"live <code>{fbe.get_live_hz()} Hz</code> · "
        f"total ok <code>{fbe.total_nc_ok:,}</code>\n"
        f"◈ <code>{PREFIX}fb on|off</code> · <code>{PREFIX}throttle max|safe|off</code>",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("count", "nccount", "counts"), IsSudo())
async def cmd_nc_counts(message: Message):
    gc_hits = STATE.chat_nc_counts.get(message.chat.id, 0)
    await message.reply(
        f"🔢 <b>NC COUNTS</b>\n"
        f"🔥 This GC: <code>{gc_hits:,}</code>\n"
        f"🌐 Global: <code>{STATE.global_nc_count:,}</code>\n"
        f"⚡ Pipeline: <code>{fbe.get_live_hz()} Hz</code> · "
        f"ok <code>{fbe.total_nc_ok:,}</code>",
        parse_mode=ParseMode.HTML)
# ==============================================================================
# SPAM / SLIDE / COPY / BROADCAST
# ==============================================================================
@router.message(DCommand("s", "spam", "rs", "rspam"), IsSudo())
async def cmd_spam(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    if not text:
        return await message.reply(f"◈ Usage: <code>{PREFIX}s &lt;txt&gt;</code> · "
                                   f"<code>{PREFIX}rs &lt;txt&gt;</code> (raid)",
                                   parse_mode=ParseMode.HTML)
    mode = "rs" if command.command.lower() in ("rs", "rspam") else "static"
    fanout(message, "spam", spam_worker, text, mode)
    await message.reply(
        f"💣 <b>SPAM {'RAID' if mode == 'rs' else 'LOOP'}</b> armed\n"
        f"📝 <code>{_hesc(text[:60])}</code>\n"
        f"🤖 <code>{len(active_bots)}</code> bots{fanout_note(message)}",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("ts"), IsSudo())
async def cmd_template_spam(message: Message, command: CommandObject):
    text = (command.args or "").strip() or "GROUP"
    fanout(message, "spam", spam_worker, text, "template", read_template())
    await message.reply(f"📝 <b>TEMPLATE SPAM</b> armed → <code>{_hesc(text)}</code>"
                        f"{fanout_note(message)}", parse_mode=ParseMode.HTML)


@router.message(DCommand("crashtextraid"), IsSudo())
async def cmd_crash_text_raid(message: Message, command: CommandObject):
    text = (command.args or "").strip() or "WAR"
    fanout(message, "spam", spam_worker, text, "crash")
    await message.reply(f"💥 <b>CRASH TEXT RAID</b> armed{fanout_note(message)}",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("tsl", "sls", "slide"), IsSudo())
async def cmd_slide(message: Message, command: CommandObject):
    target = resolve_target(message)
    if not target:
        return await message.reply(
            f"◈ Target ke message pe reply karo phir <code>{PREFIX}tsl</code> "
            f"ya <code>{PREFIX}tsl @name</code> (id reply se).",
            parse_mode=ParseMode.HTML)
    uid, name = target
    text = (command.args or "").strip() or None
    fanout(message, "slide", slide_worker, uid, name, text)
    await message.reply(
        f"🎯 <b>TARGET SLIDE</b> armed\n"
        f"👤 <code>{_hesc(name)}</code> (<code>{uid}</code>){fanout_note(message)}",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("spam1", "tsp"), IsSudo())
async def cmd_spam1(message: Message, command: CommandObject):
    target = resolve_target(message)
    if not target:
        return await message.reply(f"◈ Reply to target's message → <code>{PREFIX}spam1</code>",
                                   parse_mode=ParseMode.HTML)
    uid, name = target
    text = (command.args or "").strip() or None
    fanout(message, "slide", slide_worker, uid, name, text)
    await message.reply(f"💣 <b>SPAM1</b> → <code>{_hesc(name)}</code>{fanout_note(message)}",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("c", "copy"), IsSudo())
async def cmd_copy_text(message: Message, command: CommandObject):
    rm = message.reply_to_message
    if not rm or not (rm.text or rm.caption):
        return await message.reply(f"◈ Reply to a text message → <code>{PREFIX}c</code>",
                                   parse_mode=ParseMode.HTML)
    text = rm.text or rm.caption
    fanout(message, "spam", spam_worker, text, "static")
    await message.reply(f"📋 <b>COPY-SPAM</b> armed from reply.{fanout_note(message)}",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("bc", "broadcast"), IsOwner())
async def cmd_broadcast(message: Message, command: CommandObject):
    text = (command.args or "").strip()
    if not text:
        return await message.reply(f"◈ Usage: <code>{PREFIX}bc &lt;txt&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    cids = STATE.get_all_known_groups(message.chat.id)

    async def run_broadcast():
        ok = 0
        for cid in cids:
            for b in active_bots:
                try:
                    await b.send_message(cid, text)
                    ok += 1
                    break
                except Exception:
                    continue
            await asyncio.sleep(0.15)
        with suppress(Exception):
            await message.reply(f"📢 Broadcast done → <code>{ok}/{len(cids)}</code> GCs.",
                                parse_mode=ParseMode.HTML)

    asyncio.create_task(run_broadcast())
    await message.reply(f"📢 Broadcast queued → <code>{len(cids)}</code> GCs.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("ds", "drs", "dts", "dalls", "dtsl", "dsls",
                         "stopspam", "stops", "stopslide", "stoptsl"), IsSudo())
async def cmd_stop_spams(message: Message):
    stopped = fanout_stop(message, ("spam", "slide"))
    await message.reply(f"🛑 Spam/slide stopped · <code>{stopped}</code>.",
                        parse_mode=ParseMode.HTML)


# ==============================================================================
# FORWARD LOOP
# ==============================================================================
@router.message(DCommand("fwd", "f", "cfwd", "cf", "nfwd", "fnmix"), IsSudo())
async def cmd_forward_loop(message: Message, command: CommandObject):
    rm = message.reply_to_message
    if not rm:
        return await message.reply(
            f"⚠️ Reply to any message (text/photo/sticker/video/voice) with "
            f"<code>{PREFIX}fwd</code> to repeat-forward it!",
            parse_mode=ParseMode.HTML)

    cmd = command.command.lower()
    use_copy = cmd in ("cfwd", "cf")
    item = {
        "from_chat_id": rm.chat.id,
        "message_id": rm.message_id,
        "copy": use_copy,
        "backup": extract_message_backup(rm),
    }

    for cid in target_cids(message):
        q = STATE.fwd_queue.setdefault(cid, deque(maxlen=MAX_FWD_QUEUE_PER_CHAT))
        if (command.args or "").strip().lower() == "new":
            q.clear()
        q.append(dict(item))
        spawn(cid, "fwd", fwd_cluster_worker)

    if cmd in ("nfwd", "fnmix"):
        nc_text = (command.args or "").strip() or (rm.text or rm.caption or "NIKFYT")[:25]
        fanout(message, "nc", nc_cluster_worker, nc_text, "mix")
        return await message.reply(
            f"🌪️ <b>COMBO: MIX NC + REPEAT FORWARD!</b>\n"
            f"🔁 Queued: <code>{len(STATE.fwd_queue.get(message.chat.id, []))}"
            f"/{MAX_FWD_QUEUE_PER_CHAT}</code>\n"
            f"🎨 NC: <code>{_hesc(nc_text)}</code>{fanout_note(message)}",
            parse_mode=ParseMode.HTML)

    await message.reply(
        f"🔁 <b>{'COPY' if use_copy else 'FORWARD'} LOOP</b> armed · "
        f"queued <code>{len(STATE.fwd_queue.get(message.chat.id, []))}"
        f"/{MAX_FWD_QUEUE_PER_CHAT}</code>{fanout_note(message)}",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("gfwd", "gf", "gcfwd"), IsSudo())
async def cmd_global_forward(message: Message):
    rm = message.reply_to_message
    if not rm:
        return await message.reply(f"◈ Reply to a message → <code>{PREFIX}gfwd</code>",
                                   parse_mode=ParseMode.HTML)
    item = {"from_chat_id": rm.chat.id, "message_id": rm.message_id, "copy": False,
            "backup": extract_message_backup(rm)}
    cids = STATE.get_all_known_groups(message.chat.id)
    for cid in cids:
        q = STATE.fwd_queue.setdefault(cid, deque(maxlen=MAX_FWD_QUEUE_PER_CHAT))
        q.append(dict(item))
        spawn(cid, "fwd", fwd_cluster_worker)
    await message.reply(f"🌍 <b>GLOBAL FWD</b> → <code>{len(cids)}</code> GCs.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("dfwd", "df", "dcfwd", "dnfwd", "dstopfwd"), IsSudo())
async def cmd_stop_forward(message: Message):
    stopped = fanout_stop(message, ("fwd",))
    await message.reply(f"🛑 Forward loop stopped · <code>{stopped}</code>.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("dgfwd", "dgf"), IsSudo())
async def cmd_stop_global_forward(message: Message):
    stopped = 0
    for cid in STATE.get_all_known_groups(message.chat.id):
        if STATE.tasks.get("fwd", {}).get(cid):
            STATE.cancel("fwd", cid)
            stopped += 1
    STATE.save_runtime_state()
    await message.reply(f"🛑 Global fwd stopped · <code>{stopped}</code>.",
                        parse_mode=ParseMode.HTML)


# ==============================================================================
# GAMEOVER
# ==============================================================================
@router.message(DCommand("setgo", "setgameover"), IsSudo())
async def cmd_set_gameover_gif(message: Message):
    rm = message.reply_to_message
    if not rm or not (rm.animation or rm.video):
        return await message.reply(
            f"⚠️ Reply to a GIF/short MP4 with <code>{PREFIX}setgo</code> to save "
            f"as Game Over media!", parse_mode=ParseMode.HTML)
    fid = rm.animation.file_id if rm.animation else rm.video.file_id
    f = await message.bot.get_file(fid)
    bio = io.BytesIO()
    try:
        await message.bot.download_file(f.file_path, destination=bio)
        STATE.save_gameover_gif(bio.getvalue())
    finally:
        bio.close()
    await message.reply("✅ Custom Game Over GIF saved to disk!", parse_mode=ParseMode.HTML)


@router.message(DCommand("gameover", "go"), IsSudo())
async def cmd_gameover(message: Message, command: CommandObject):
    rm = message.reply_to_message
    if rm and (rm.animation or rm.video):
        with suppress(Exception):
            fid = rm.animation.file_id if rm.animation else rm.video.file_id
            f = await message.bot.get_file(fid)
            bio = io.BytesIO()
            await message.bot.download_file(f.file_path, destination=bio)
            STATE.save_gameover_gif(bio.getvalue())
            bio.close()
    custom = (command.args or "").strip() or None
    if MULTIMODE:
        for cid in target_cids(message):
            await execute_gameover(cid, custom_target=custom)
    else:
        await execute_gameover(message.chat.id, custom_target=custom)


# ==============================================================================
# PFP / MEDIA ROTATION
# ==============================================================================
@router.message(DCommand("sv", "gsv"), IsSudo())
async def cmd_save_pfp(message: Message, command: CommandObject):
    rm = message.reply_to_message
    if not rm or not rm.photo:
        return await message.reply(f"◈ Reply to a photo → <code>{PREFIX}sv</code>",
                                   parse_mode=ParseMode.HTML)
    is_global = command.command.lower() == "gsv"
    cat = "gpfp" if is_global else "pfp"
    fid = rm.photo[-1].file_id
    f = await message.bot.get_file(fid)
    bio = io.BytesIO()
    try:
        await message.bot.download_file(f.file_path, destination=bio)
        raw = bio.getvalue()
    finally:
        bio.close()
    items = STATE.pfp_cache[cat].setdefault(message.chat.id, [])
    items.append(raw)
    del items[:-MAX_PFP_CACHE_PER_CHAT]
    STATE.save_pfp_to_disk(cat, message.chat.id)
    await message.reply(f"🖼️ Saved ({len(items)}) · "
                        f"{'global bot pfp' if is_global else 'chat pfp'} rotation "
                        f"ke liye <code>{PREFIX}{'gp' if is_global else 'p'}</code>",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("p", "gp"), IsSudo())
async def cmd_start_pfp(message: Message, command: CommandObject):
    is_global = command.command.lower() == "gp"
    cat = "gpfp" if is_global else "pfp"
    if not STATE.pfp_cache[cat].get(message.chat.id) and not is_global:
        return await message.reply(f"◈ Pehle photo save karo: reply → <code>{PREFIX}sv</code>",
                                   parse_mode=ParseMode.HTML)
    fanout(message, "pfp", pfp_worker, is_global)
    await message.reply(f"🖼️ PFP rotation {'GLOBAL' if is_global else 'CHAT'} started.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("dp"), IsSudo())
async def cmd_stop_pfp(message: Message):
    stopped = fanout_stop(message, ("pfp",))
    await message.reply(f"🛑 PFP rotation stopped · <code>{stopped}</code>.",
                        parse_mode=ParseMode.HTML)


def _register_media(cmd: str, mtype: str):
    @router.message(DCommand(cmd), IsSudo())
    async def hndlr(message: Message):
        rm = message.reply_to_message
        ok = {"sticker": lambda m: m.sticker, "gif": lambda m: m.animation,
              "photo": lambda m: m.photo, "voice": lambda m: m.voice}[mtype]
        if not rm or not ok(rm):
            return await message.reply(f"◈ Reply to a {mtype} → <code>{PREFIX}{cmd}</code>",
                                       parse_mode=ParseMode.HTML)
        fid = {"sticker": rm.sticker.file_id, "gif": rm.animation.file_id,
               "photo": rm.photo[-1].file_id, "voice": rm.voice.file_id}[mtype]
        items = STATE.media[mtype].setdefault(message.chat.id, [])
        items.append(fid)
        del items[:-MAX_MEDIA_PER_CHAT]
        fanout(message, "media", media_worker, mtype)
        await message.reply(f"🖼️ {mtype.upper()} saved ({len(items)}) + rotation started.",
                            parse_mode=ParseMode.HTML)


for _cmd, _mt in (("stk", "sticker"), ("gif", "gif"), ("pic", "photo"), ("vc", "voice")):
    _register_media(_cmd, _mt)


@router.message(DCommand("dstk", "dgif", "dpic", "dvc", "dmedia"), IsSudo())
async def cmd_stop_media(message: Message):
    stopped = fanout_stop(message, ("media",))
    await message.reply(f"🛑 Media rotation stopped · <code>{stopped}</code>.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("botpfp"), IsSudo())
async def cmd_botpfp(message: Message):
    rm = message.reply_to_message
    if not rm or not rm.photo:
        return await message.reply(f"◈ Reply to a photo → <code>{PREFIX}botpfp</code>",
                                   parse_mode=ParseMode.HTML)
    fid = rm.photo[-1].file_id
    ok = 0
    for b in active_bots:
        try:
            f = await b.get_file(fid)
            bio = io.BytesIO()
            await b.download_file(f.file_path, destination=bio)
            raw = bio.getvalue()
            bio.close()
            if await set_bot_profile_photo_safe(b, raw):
                ok += 1
        except Exception:
            continue
    await message.reply(f"🖼️ Bot PFP updated on <code>{ok}/{len(active_bots)}</code> bots.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("rm"), IsSudo())
async def cmd_del_pfp(message: Message):
    STATE.pfp_cache["pfp"].pop(message.chat.id, None)
    await message.reply("🗑️ Chat pfp cache cleared.", parse_mode=ParseMode.HTML)


@router.message(DCommand("rmall"), IsSudo())
async def cmd_del_all_media(message: Message):
    STATE.pfp_cache = {"pfp": {}, "gpfp": {}}
    STATE.media = {"sticker": {}, "gif": {}, "photo": {}, "voice": {}}
    await message.reply("🗑️ Sab pfp/media caches cleared.", parse_mode=ParseMode.HTML)
# ==============================================================================
# ADMIN — mute · unmute · mutelist · promote · purge · lock/unlock · whois
# ==============================================================================
@router.message(DCommand("m", "mute"), IsSudo())
async def cmd_mute(message: Message, command: CommandObject):
    target = resolve_target(message)
    if not target:
        return await message.reply(f"◈ Reply to target's message → <code>{PREFIX}m</code>",
                                   parse_mode=ParseMode.HTML)
    uid, name = target
    if uid == OWNER_ID or uid in STATE.sudo:
        return await message.reply("⚠️ Owner/sudo ko mute nahi kar sakte.",
                                   parse_mode=ParseMode.HTML)
    secs = parse_duration((command.args or "").split()[0]) if command.args else None
    ok = await smart_restrict(message.chat.id, uid, mute=True)
    if ok:
        STATE.tracked_users.setdefault(message.chat.id, {})[uid] = (
            time.time() + secs if secs else 0.0)
    await message.reply(
        f"🔇 Muted <b>{_hesc(name)}</b>" + (f" for <code>{command.args.strip()}</code>" if secs else "")
        if ok else "❌ Mute failed — bot admin rights check karo.",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("um", "unmute"), IsSudo())
async def cmd_unmute(message: Message):
    target = resolve_target(message)
    if not target:
        return await message.reply(f"◈ Reply → <code>{PREFIX}um</code>", parse_mode=ParseMode.HTML)
    uid, name = target
    ok = await smart_restrict(message.chat.id, uid, mute=False)
    STATE.tracked_users.get(message.chat.id, {}).pop(uid, None)
    await message.reply(f"🔊 Unmuted <b>{_hesc(name)}</b>." if ok else "❌ Unmute failed.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("mlist"), IsSudo())
async def cmd_mutelist(message: Message):
    tracked = STATE.tracked_users.get(message.chat.id, {})
    if not tracked:
        return await message.reply("📭 Koi muted user track nahi.", parse_mode=ParseMode.HTML)
    lines = []
    for uid, until in list(tracked.items())[:40]:
        if until and until > time.time():
            left = int(until - time.time())
            lines.append(f"  <code>{uid}</code> · {left}s left")
        else:
            lines.append(f"  <code>{uid}</code> · permanent")
    await message.reply(f"🔇 <b>MUTED ({len(tracked)})</b>\n" + "\n".join(lines),
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("prom", "promote"), IsSudo())
async def cmd_promoteall(message: Message, command: CommandObject):
    uid = None
    t = resolve_target(message)
    if t:
        uid = t[0]
    elif command.args and command.args.strip().isdigit():
        uid = int(command.args.strip())
    if not uid:
        return await message.reply(f"◈ Reply ya <code>{PREFIX}prom &lt;id&gt;</code>",
                                   parse_mode=ParseMode.HTML)
    ok = await smart_promote(message.chat.id, uid)
    await message.reply(f"👑 Promoted <code>{uid}</code>." if ok else "❌ Promote failed.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("prombots"), IsSudo())
async def cmd_promotebots(message: Message):
    ok = 0
    for b in active_bots:
        with suppress(Exception):
            await b.promote_chat_member(
                message.chat.id, b.id,
                can_change_info=True, can_delete_messages=True,
                can_restrict_members=True, can_invite_users=True,
                can_pin_messages=True, can_promote_members=False)
            ok += 1
    await message.reply(f"🤖 Promoted <code>{ok}/{len(active_bots)}</code> bots.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("purge"), IsSudo())
async def cmd_purge(message: Message, command: CommandObject):
    try:
        n = min(100, max(1, int((command.args or "20").strip())))
    except ValueError:
        n = 20
    ack = await message.reply(f"🧹 Purging {n} messages…", parse_mode=ParseMode.HTML)
    deleted = await purge_messages(message, n)
    with suppress(Exception):
        await ack.edit_text(f"🧹 Purged ~<code>{deleted}</code> messages.",
                            parse_mode=ParseMode.HTML)


@router.message(DCommand("lock"), IsSudo())
async def cmd_lock(message: Message):
    for b in active_bots:
        with suppress(Exception):
            await b.set_chat_permissions(
                message.chat.id,
                ChatPermissions(can_send_messages=False, can_send_polls=False,
                                can_send_other_messages=False,
                                can_add_web_page_previews=False))
            await message.reply("🔒 Chat locked.", parse_mode=ParseMode.HTML)
            return
    await message.reply("❌ Lock failed — bot admin chahiye.", parse_mode=ParseMode.HTML)


@router.message(DCommand("unlock"), IsSudo())
async def cmd_unlock(message: Message):
    for b in active_bots:
        with suppress(Exception):
            await b.set_chat_permissions(
                message.chat.id,
                ChatPermissions(can_send_messages=True, can_send_polls=True,
                                can_send_other_messages=True,
                                can_add_web_page_previews=True))
            await message.reply("🔓 Chat unlocked.", parse_mode=ParseMode.HTML)
            return
    await message.reply("❌ Unlock failed.", parse_mode=ParseMode.HTML)


@router.message(DCommand("whois"), IsSudo())
async def cmd_whois(message: Message):
    t = resolve_target(message)
    if not t:
        return await message.reply(f"◈ Reply → <code>{PREFIX}whois</code>",
                                   parse_mode=ParseMode.HTML)
    uid, name = t
    rm = message.reply_to_message
    u = rm.from_user
    lines = [
        f"🕵️ <b>WHOIS</b>",
        f"👤 Name: <b>{_hesc(name)}</b>",
        f"🆔 ID: <code>{uid}</code>",
        f" uname: <code>{_hesc('@' + u.username) if u.username else '—'}</code>",
        f"📜 Bio: <code>{_hesc(u.bio or '—')}</code>" if hasattr(u, "bio") else "",
        f"🛡️ Status: {'OWNER' if uid == OWNER_ID else ('SUDO' if uid in STATE.sudo else 'member')}",
    ]
    await message.reply("\n".join(x for x in lines if x), parse_mode=ParseMode.HTML)


# ==============================================================================
# REACTIONS
# ==============================================================================
@router.message(DCommand("react"), IsSudo())
async def cmd_react(message: Message, command: CommandObject):
    rm = message.reply_to_message
    if not rm:
        return await message.reply(f"◈ Reply to a message → <code>{PREFIX}react 🔥</code>",
                                   parse_mode=ParseMode.HTML)
    emoji = (command.args or "🔥").strip()[:8] or "🔥"
    emojis = list(dict.fromkeys(re.findall(r"\S+", emoji))) or ["🔥"]
    cid = message.chat.id
    PENDING_REACT.pop(cid, None)
    task = asyncio.create_task(reaction_worker(cid, rm.message_id, emojis))
    PENDING_REACT[cid] = (rm.message_id, emojis)
    STATE.tasks.setdefault("react", {})[cid] = task
    await message.reply(f"⚛️ Reaction loop on <code>{rm.message_id}</code> · "
                        f"<code>{_hesc(' '.join(emojis))}</code>",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("stopreact"), IsSudo())
async def cmd_stop_react(message: Message):
    task = STATE.tasks.get("react", {}).pop(message.chat.id, None)
    if task and not task.done():
        task.cancel()
    PENDING_REACT.pop(message.chat.id, None)
    await message.reply("🛑 Reaction loop stopped.", parse_mode=ParseMode.HTML)


@router.message(DCommand("autoreact"), IsSudo())
async def cmd_autoreact(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    cid = message.chat.id
    if arg.lower() in ("off", "0", "stop"):
        REACT_LOOP.pop(cid, None)
        return await message.reply("🛑 Auto-react OFF.", parse_mode=ParseMode.HTML)
    emojis = list(dict.fromkeys(re.findall(r"\S+", arg))) or ["🔥"]
    REACT_LOOP[cid] = emojis
    await message.reply(f"⚛️ Auto-react ON · <code>{_hesc(' '.join(emojis))}</code> "
                        f"— har nayi message pe.", parse_mode=ParseMode.HTML)


# ==============================================================================
# MULTIMODE (sare commands ko sab GCs me broadcast)
# ==============================================================================
@router.message(DCommand("mm", "multimode"), IsSudo())
async def cmd_multimode(message: Message, command: CommandObject):
    global MULTIMODE
    raw = (command.args or "").strip().lower()
    cids = STATE.get_all_known_groups(message.chat.id)

    if raw == "list":
        if not cids:
            return await message.reply("ℹ️ Koi known GC nahi — pehle bot ko group me add karo.",
                                       parse_mode=ParseMode.HTML)
        lines = []
        for i, cid in enumerate(cids, 1):
            active = []
            for cat in ("nc", "spam", "fwd", "slide", "media", "pfp"):
                if STATE.tasks.get(cat, {}).get(cid):
                    active.append(cat.upper())
            status = "🔴 " + "/".join(active) if active else "💤 idle"
            lines.append(f"  <code>{i:02d}. {cid}</code> — {status}")
        await message.reply(
            f"╭━━━〔 📡 MULTIMODE — GC LIST 〕━━━╮\n\n"
            f"  Total GCs  :: <code>{len(cids)}</code>\n"
            f"  Multimode  :: {'🟢 ON' if MULTIMODE else '🔴 OFF'}\n"
            f"  Bots       :: <code>{len(active_bots)}</code> active\n\n"
            + "\n".join(lines) +
            f"\n\n╰━━━〔 NIKFYT CORE 〕━━━╯",
            parse_mode=ParseMode.HTML)
        return

    if raw not in ("on", "off"):
        active_gc = sum(1 for cid in cids
                        if any(STATE.tasks.get(cat, {}).get(cid)
                               for cat in ("nc", "spam", "fwd")))
        return await message.reply(
            f"╭━━━〔 📡 MULTIMODE PANEL 〕━━━╮\n\n"
            f"  Status    :: {'🟢 ON' if MULTIMODE else '🔴 OFF'}\n"
            f"  GCs       :: <code>{len(cids)}</code> known | <code>{active_gc}</code> active\n"
            f"  Bots      :: <code>{len(active_bots)}</code> loaded\n\n"
            f"  <code>{PREFIX}mm on</code>   — sab GCs me broadcast\n"
            f"  <code>{PREFIX}mm off</code>  — sirf current GC\n"
            f"  <code>{PREFIX}mm list</code> — known GCs dekho\n\n"
            f"╰━━━〔 NIKFYT CORE 〕━━━╯",
            parse_mode=ParseMode.HTML)
        return

    MULTIMODE = (raw == "on")
    if MULTIMODE:
        nodes = []
        for i, cid in enumerate(cids, 1):
            has = any(STATE.tasks.get(cat, {}).get(cid)
                      for cat in ("nc", "spam", "fwd", "slide"))
            nodes.append(f"  {'🔴' if has else '🟢'} GC-{i:02d} :: <code>{cid}</code>")
        return await message.reply(
            f"╭━━━〔 📡 MULTIMODE ON 〕━━━╮\n\n"
            f"  🟢 <b>BROADCAST ARMED!</b>\n"
            f"  📡 GCs :: <code>{len(cids)}</code> targets locked\n"
            f"  🤖 Bots :: <code>{len(active_bots)}</code> active\n\n"
            f"──────── TARGET NODES ────────\n"
            + ("\n".join(nodes) if nodes else "  💤 No GCs yet") +
            f"\n──────────────────────────────\n\n"
            f"  Ab kisi bhi GC me command do —\n"
            f"  sab <code>{len(cids)}</code> groups me ek saath chalegi!\n\n"
            f"╰━━━〔 NIKFYT CORE 〕━━━╯",
            parse_mode=ParseMode.HTML)
    else:
        await message.reply(
            f"╭━━━〔 📡 MULTIMODE OFF 〕━━━╮\n\n"
            f"  🔴 <b>BROADCAST DISARMED</b>\n"
            f"  _Commands sirf current GC me chalenge._\n\n"
            f"╰━━━〔 NIKFYT CORE 〕━━━╯",
            parse_mode=ParseMode.HTML)


# ==============================================================================
# SYSTEM — stat/ping/uptime/tasks/logs/stopall/leave/optimize/link/bots
# ==============================================================================
_UPTIME_T0 = time.time()


def _fmt_uptime() -> str:
    s = int(time.time() - _UPTIME_T0)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    return f"{d}d {h}h {m}m {sec}s"


@router.message(DCommand("stat", "up", "on", "ping", "stats"), IsSudo())
async def cmd_stats(message: Message):
    t0 = time.monotonic()
    probe = await message.reply("🏓 pong…", parse_mode=ParseMode.HTML)
    rtt = (time.monotonic() - t0) * 1000
    await probe.edit_text(
        f"👑 <b>NIKFYT V5 · LIVE STATS</b>\n"
        f"🏓 Ping: <code>{rtt:.0f} ms</code>\n"
        f"🤖 Nodes: <code>{len(active_bots)}</code> · prefix <code>{PREFIX}</code>\n"
        f"⚡ <code>{fbe.get_live_hz()} Hz</code> · ok <code>{fbe.total_nc_ok:,}</code>\n"
        f"🔥 Global NC: <code>{STATE.global_nc_count:,}</code>\n"
        f"🌍 Known GCs: <code>{len(STATE.known_chats)}</code>\n"
        f"🔄 NC <code>{_active_count('nc')}</code> · SPM <code>{_active_count('spam')}</code> · "
        f"FWD <code>{_active_count('fwd')}</code> · SLD <code>{_active_count('slide')}</code>\n"
        f"💾 RAM: <code>{get_ram_mb():.1f} MB</code> · ⏱ <code>{_fmt_uptime()}</code>\n"
        f"🕐 <code>{get_ist_str()}</code>",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("uptime"), IsSudo())
async def cmd_uptime(message: Message):
    await message.reply(f"⏱ Uptime: <code>{_fmt_uptime()}</code> · "
                        f"🕐 <code>{get_ist_str()}</code>", parse_mode=ParseMode.HTML)


@router.message(DCommand("tasks", "running"), IsSudo())
async def cmd_tasks(message: Message):
    lines = []
    total = 0
    for cat in ("nc", "spam", "fwd", "slide", "media", "pfp", "react"):
        mapping = STATE.tasks.get(cat, {})
        live = {cid: t for cid, t in mapping.items() if t and not t.done()}
        total += len(live)
        for cid, t in live.items():
            lines.append(f"  🔴 <code>{cat}</code> · <code>{cid}</code>")
    body = "\n".join(lines) if lines else "  💤 No running tasks."
    await message.reply(f"📋 <b>RUNNING TASKS ({total})</b>\n{body}",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("logs", "log"), IsSudo())
async def cmd_logs(message: Message, command: CommandObject):
    try:
        n = min(40, max(5, int((command.args or "15").strip())))
    except ValueError:
        n = 15
    tail = list(LOG_BUFFER)[-n:]
    await message.reply(f"📜 <b>LOGS (last {len(tail)})</b>\n<code>" +
                        _hesc("\n".join(tail)) + "</code>", parse_mode=ParseMode.HTML)


@router.message(DCommand("clearlogs"), IsSudo())
async def cmd_clear_logs(message: Message):
    LOG_BUFFER.clear()
    await message.reply("🧹 Logs cleared.", parse_mode=ParseMode.HTML)


@router.message(DCommand("stopall", "dall", "dstop"), IsSudo())
async def cmd_dall(message: Message):
    stopped = fanout_stop(message, ("all",))
    for cat in STATE.tasks:
        STATE.tasks[cat].clear()
    STATE.persisted_tasks = {k: {} for k in STATE.persisted_tasks}
    STATE.fwd_queue.clear()
    STATE.save_runtime_state()
    await message.reply(f"🛑 <b>STOPALL</b> · <code>{stopped}</code> tasks killed.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("leave"), IsSudo())
async def cmd_leave(message: Message):
    ok = False
    for b in active_bots:
        with suppress(Exception):
            await b.leave_chat(message.chat.id)
            ok = True
            break
    await message.reply("👋 Bot left this chat." if ok else "❌ Leave failed.",
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("o", "optimize"), IsSudo())
async def cmd_optimize(message: Message):
    before = get_ram_mb()
    for cat in list(STATE.tasks.keys()):
        dead = [cid for cid, t in STATE.tasks[cat].items() if t.done()]
        for cid in dead:
            STATE.tasks[cat].pop(cid, None)
    gc.collect()
    after = get_ram_mb()
    await message.reply(
        f"⚙️ <b>OPTIMIZE</b>\n💾 RAM <code>{before:.1f} → {after:.1f} MB</code>\n"
        f"🤖 Nodes <code>{len(active_bots)}</code> · gap "
        f"<code>{fbe.per_bot_nc_gap}s</code>",
        parse_mode=ParseMode.HTML)


@router.message(DCommand("link", "links"), IsSudo())
async def cmd_links(message: Message):
    url = None
    for b in active_bots:
        with suppress(Exception):
            url = await b.export_chat_invite_link(message.chat.id)
            break
    if url:
        await message.reply(f"🔗 <code>{_hesc(url)}</code>", parse_mode=ParseMode.HTML)
    else:
        await message.reply("❌ Invite link failed — admin chahiye.", parse_mode=ParseMode.HTML)


@router.message(DCommand("bots", "mbots"), IsSudo())
async def cmd_bots(message: Message):
    lines = []
    for i, t in enumerate(build_tokens(), 1):
        if not t:
            continue
        b = LIVE_BOTS.get(t)
        if b:
            flooded = "🔴 flood" if fbe.is_flooded(b.id) else "🟢 ok"
            uname = bot_usernames.get(b.id, "?")
            lines.append(f"  #{i} @{_hesc(uname)} · <code>{b.id}</code> · {flooded}")
        else:
            lines.append(f"  #{i} <code>{_hesc(t[:12])}…</code> · saved")
    await message.reply(f"🤖 <b>BOT NODES</b>\n" + ("\n".join(lines) or "  — none —"),
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("knownchats", "gc", "gclist"), IsOwner())
async def cmd_knownchats(message: Message):
    cids = STATE.get_all_known_groups()
    if not cids:
        return await message.reply("ℹ️ Koi known GC nahi.", parse_mode=ParseMode.HTML)
    lines = [f"  <code>{i:02d}. {cid}</code>" for i, cid in enumerate(cids, 1)]
    await message.reply(f"🌍 <b>KNOWN GCS ({len(cids)})</b>\n" + "\n".join(lines),
                        parse_mode=ParseMode.HTML)


@router.message(DCommand("shutdown"), IsOwner())
async def cmd_shutdown(message: Message):
    stopped = STATE.stop_all(save=True)
    await message.reply(
        f"🛑 <b>FULL SHUTDOWN</b> · <code>{stopped}</code> tasks stopped, "
        f"state saved. Bot process abhi bhi chal raha hai — "
        f"panel se kill karo ya <code>{PREFIX}stopall</code> use karo.",
        parse_mode=ParseMode.HTML)


# ==============================================================================
# MENU PANELS  (!menu1 … !menu5 — category-wise quick reference)
# ==============================================================================
_PANEL_TEMPLATES: Dict[str, str] = {
    "1": (
        "╭━━━〔 🏷️ <b>ᴍᴇɴᴜ 1 · ɴᴀᴍᴇ ᴄʜᴀɴɢᴇ</b> 〕━━━╮\n"
        "┃ <code>{P}n &lt;name&gt;</code> · <code>{P}nemo</code> · <code>{P}nken</code>\n"
        "┃ <code>{P}n1</code> … <code>{P}n25 &lt;name&gt;</code>  (25 designs)\n"
        "┃ <code>{P}nmix</code> · <code>{P}nsmix</code> · <code>{P}tn</code>\n"
        "┃ <code>{P}timenc</code> · <code>{P}raidnc</code> · <code>{P}longnc</code>\n"
        "┃ <code>{P}nc2</code> · <code>{P}ncloop</code>\n"
        "┃ <code>{P}crashnc|2|3</code> · <code>{P}glitchnc|2</code>\n"
        "┃ <code>{P}aninc</code> · <code>{P}hnc</code> · <code>{P}inc</code> · "
        "<code>{P}jnc</code>\n"
        "┃ <code>{P}fucknc</code> · <code>{P}ftnc</code>\n"
        "┃ <code>{P}dn</code> · <code>{P}dlyn &lt;ms&gt;</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    ),
    "2": (
        "╭━━━〔 💣 <b>ᴍᴇɴᴜ 2 · ꜱᴩᴀᴍ & ꜱʟɪᴅᴇ</b> 〕━━━╮\n"
        "┃ <code>{P}s &lt;text&gt;</code> · <code>{P}rs &lt;text&gt;</code> (ʀᴀɪᴅ)\n"
        "┃ <code>{P}ts &lt;text&gt;</code> (ᴛᴇᴍᴘʟᴀᴛᴇ) · <code>{P}c</code> (ʀᴇᴘʟʏ)\n"
        "┃ <code>{P}tsl</code> / <code>{P}sls</code> (ʀᴇᴘʟʏ → ᴛᴀʀɢᴇᴛ)\n"
        "┃ <code>{P}spam1</code> (ʀᴇᴘʟʏ) · <code>{P}swipe [pfx]</code>\n"
        "┃ <code>{P}crashtextraid</code> · <code>{P}picraid</code> (ʀᴇᴘʟʏ ᴘʜᴏᴛᴏ)\n"
        "┃ <code>{P}bc &lt;text&gt;</code> (ʙʀᴏᴀᴅᴄᴀsᴛ)\n"
        "┃ <code>{P}ds</code> · <code>{P}dtsl</code> · <code>{P}stoppicraid</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    ),
    "3": (
        "╭━━━〔 🔁 <b>ᴍᴇɴᴜ 3 · ꜰᴏʀᴡᴀʀᴅ & ɢᴀᴍᴇᴏᴠᴇʀ</b> 〕━━━╮\n"
        "┃ <code>{P}fwd</code> / <code>{P}f</code> (ʀᴇᴘʟʏ)\n"
        "┃ <code>{P}cfwd</code> (ᴄᴏᴘʏ) · <code>{P}nfwd &lt;txt&gt;</code> (ᴄᴏᴍʙᴏ)\n"
        "┃ <code>{P}gfwd</code> (ɢʟᴏʙᴀʟ) · <code>{P}dfwd</code> / <code>{P}dgfwd</code>\n"
        "┃ <code>{P}gameover [name]</code> · <code>{P}setgo</code> (ʀᴇᴘʟʏ)\n"
        "┃ <code>{P}sv</code>/<code>{P}gsv</code> · <code>{P}p</code>/"
        "<code>{P}gp</code>/<code>{P}dp</code>\n"
        "┃ <code>{P}stk</code> · <code>{P}gif</code> · <code>{P}pic</code> · "
        "<code>{P}vc</code> · <code>{P}botpfp</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    ),
    "4": (
        "╭━━━〔 👑 <b>ᴍᴇɴᴜ 4 · ᴀᴅᴍɪɴ & ʀᴇᴀᴄᴛ</b> 〕━━━╮\n"
        "┃ <code>{P}m</code>/<code>{P}um</code> (ʀᴇᴘʟʏ · <code>30s 5m 2h 1d</code>)\n"
        "┃ <code>{P}mlist</code> · <code>{P}prom</code> · <code>{P}prombots</code>\n"
        "┃ <code>{P}purge &lt;n&gt;</code> · <code>{P}lock</code>/<code>{P}unlock</code>\n"
        "┃ <code>{P}whois</code> (ʀᴇᴘʟʏ)\n"
        "┃ <code>{P}react &lt;emoji&gt;</code> (ʀᴇᴘʟʏ) · <code>{P}stopreact</code>\n"
        "┃ <code>{P}autoreact &lt;emoji&gt;</code> · <code>{P}gmute [t]</code> · "
        "<code>{P}gunmute</code>\n"
        "┃ <code>{P}rnorv</code> · <code>{P}rct</code> · <code>{P}swp</code> "
        "(ɪɴᴛᴇʀᴄᴇᴘᴛᴏʀs)\n"
        "┃ <code>{P}sudo</code>(ʀᴇᴘʟʏ) · <code>{P}sudolist</code> · "
        "<code>{P}clearsudo</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    ),
    "5": (
        "╭━━━〔 ⚙️ <b>ᴍᴇɴᴜ 5 · ꜱʏꜱᴛᴇᴍ & ᴛᴏᴋᴇɴꜱ</b> 〕━━━╮\n"
        "┃ <code>{P}mm on|off|list</code> (ᴍᴜʟᴛɪᴍᴏᴅᴇ)\n"
        "┃ <code>{P}addtoken</code> · <code>{P}remtoken</code> · "
        "<code>{P}tokens</code> · <code>{P}bots</code>\n"
        "┃ <code>{P}setbotname</code> · <code>{P}setbotbio</code> · "
        "<code>{P}botpfp</code>\n"
        "┃ <code>{P}setdelay &lt;sec&gt;</code> · <code>{P}throttle max|safe|off</code> · "
        "<code>{P}fb</code>\n"
        "┃ <code>{P}stat</code> · <code>{P}ping</code> · <code>{P}uptime</code> · "
        "<code>{P}count</code>\n"
        "┃ <code>{P}tasks</code> · <code>{P}logs</code> · <code>{P}stopall</code> · "
        "<code>{P}leave</code>\n"
        "┃ <code>{P}link</code> · <code>{P}knownchats</code> · "
        "<code>{P}prefix &lt;p&gt;</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    ),
}


@router.message(DCommand("menu1", "menu2", "menu3", "menu4", "menu5"), IsSudo())
async def cmd_menu_panel(message: Message, command: CommandObject):
    key = re.sub(r"[^0-9]", "", command.command) or "1"
    # PANEL_TEMPLATES use {P} placeholder — render with live prefix
    panel = _PANEL_TEMPLATES.get(key)
    if not panel:
        return await message.reply(f"◈ Usage: <code>{PREFIX}menu1</code> … "
                                   f"<code>{PREFIX}menu5</code>", parse_mode=ParseMode.HTML)
    await message.reply(panel.format(P=PREFIX), parse_mode=ParseMode.HTML)


# ==============================================================================
# MESSAGE HOOK (last resort handler — interceptors · gmute · auto-react)
# ==============================================================================
@router.message()
async def auto_react_hook(message: Message):
    u = message.from_user
    if not u or u.is_bot or message.chat.type == "private":
        return

    # global mute enforcement — muted user ka message delete + re-mute
    if u.id in STATE.global_muted and u.id != OWNER_ID and u.id not in STATE.sudo:
        exp = STATE.global_muted[u.id]
        if exp and exp <= time.time():
            STATE.global_muted.pop(u.id, None)
        else:
            with suppress(Exception):
                if message.message_id:
                    await message.delete()
            with suppress(Exception):
                await smart_restrict(message.chat.id, u.id, mute=True)
            return

    # interceptors (auto-insult / auto-react / auto-swipe)
    await interceptor_engine(message)

    # rotation auto-react
    if message.chat.id in REACT_LOOP:
        await auto_react_worker(message)
# ==============================================================================
# MAIN LIFECYCLE (PARALLEL FAST BOOT + ALL-BOT FAILOVER POLLING + 24/7 PIPELINE)
# ==============================================================================
async def _init_single_bot(t: str) -> Optional[Bot]:
    session = AiohttpSession(limit=100)
    b = Bot(token=t, session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        me = await b.get_me()
        await b.delete_webhook(drop_pending_updates=True)
        bot_usernames[b.id] = me.username or ""
        LIVE_BOTS[t] = b
        log_event("info", "BOOT", f"Node @{me.username} (id {b.id}) online.")
        return b
    except Exception as e:
        with suppress(Exception):
            await session.close()
        STATE.failed_bots.append(t)
        log_event("warn", "BOOT", f"Token {t[:12]}... failed: {e}")
        return None


async def main():
    global active_bots
    active_bots.clear()
    await start_panel_or_hf_webserver()
    asyncio.create_task(auto_ram_guard())

    dp = Dispatcher()
    dp.include_router(router)

    valid_tokens = [t.strip() for t in TOKENS
                    if t.strip() and not t.startswith("YOUR_BOT")]
    log_event("info", "BOOT",
              f"Parallel-initializing {MAX_TOKENS}-Bot Pipelined Cluster "
              f"({len(valid_tokens)} tokens)...")

    booted = await asyncio.gather(*(_init_single_bot(t) for t in valid_tokens))
    active_bots.extend([b for b in booted if b is not None])

    gc.collect()
    if not active_bots:
        log_event("error", "BOOT", "No valid bots loaded. Retrying in 10s...")
        await asyncio.sleep(10)
        return

    fbe.recalculate_pacing(len(active_bots))
    STATE.delays["nc"] = round(1.0 / max(1, len(active_bots)), 3)
    STATE.load_runtime_state()
    STATE.load_pfp_from_disk()
    STATE.delays["nc"] = round(1.0 / max(1, len(active_bots)), 3)

    await restore_persisted_tasks()
    asyncio.create_task(task_watchdog_daemon())
    asyncio.create_task(_muted_watcher())
    asyncio.create_task(global_mute_watcher())

    log_event("info", "BOOT",
              f"{len(active_bots)} Nodes Online | All-Bot Failover Active | "
              f"Pipelined NC Tick: {STATE.delays['nc']}s | prefix {PREFIX} (+ ~ /)")

    while True:
        try:
            await dp.start_polling(
                *active_bots,
                allowed_updates=dp.resolve_used_update_types(),
                polling_timeout=25,
                handle_signals=False,
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            log_event("error", "SYSTEM",
                      f"Polling interrupted: {str(e)}. Reconnecting in 2s...")
            gc.collect()
            await asyncio.sleep(2)

    STATE.stop_all(save=False)
    for b in active_bots:
        with suppress(Exception):
            await b.session.close()


async def auto_ram_guard():
    """Background RAM sentinel — 380MB+ par auto purge."""
    while True:
        try:
            await asyncio.sleep(120)
            ram = get_ram_mb()
            if ram > 380.0:
                gc.collect()
                log_event("warn", "RAM-GUARD",
                          f"RAM {ram:.1f}MB — gc.collect() forced.")
        except asyncio.CancelledError:
            break
        except Exception:
            pass


async def _muted_watcher():
    """Timed mutes ka expiry — time khatam ho to auto unmute."""
    while True:
        try:
            await asyncio.sleep(15)
            now = time.time()
            for cid, users in list(STATE.tracked_users.items()):
                for uid, until in list(users.items()):
                    if until and 0 < until <= now:
                        await smart_restrict(cid, uid, mute=False)
                        users.pop(uid, None)
                        log_event("info", "MUTE", f"Auto-unmute {uid} in {cid}.")
        except asyncio.CancelledError:
            break
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--check", "-c"):
        print("=== NIKFYT V5 / CONFIG CHECK ===")
        print(f"  OWNER_ID    : {OWNER_ID or 0}")
        print(f"  MAIN_TOKEN  : " + ("set" if MAIN_TOKEN else "EMPTY -> CONFIG[MAIN_TOKEN] bharo"))
        print(f"  extra slots : {sum(1 for t in TOKENS[1:] if t)}/9 filled")
        print(f"  total       : {sum(1 for t in TOKENS if t)}/{MAX_TOKENS}")
        print(f"  PREFIX      : {PREFIX}  (! ~ / ALWAYS on)")
        print(f"  styles      : {len(DYNAMIC_NC_MAP)} NC designs + mix/time/raid/crash/glitch")
        print(f"  commands    : !addtoken / !remtoken / !tokens / !prefix ~")
        print(f"  menu        : !menu")
        sys.exit(0)
    while True:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            STATE.save_runtime_state()
            print("🛑 MANUAL SHUTDOWN. State preserved in nikfyt_state.json.")
            break
        except Exception as fatal_err:
            STATE.save_runtime_state()
            print(f"⚠️ FATAL CRASH DETECTED: {fatal_err}\n{traceback.format_exc()}",
                  flush=True)
            print("🔄 Auto-restarting script in 2 seconds and resuming all GC tasks...",
                  flush=True)
            time.sleep(2)
