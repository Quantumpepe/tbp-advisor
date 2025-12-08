# server.py — TBP-AI + C-BoostAI unified backend (Web + Telegram) — with AI security filters + BUY BOT
# -*- coding: utf-8 -*-

import os, re, json, time, threading, random
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# =========================
# CONFIG / LINKS / CONSTANTS
# =========================

BOT_NAME        = "TBP-AI"
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

# Zwei Bot-Tokens:
#  - TELEGRAM_BOT_TOKEN  -> TBP-Bot
#  - TELEGRAM_BOT_TOKEN1 -> C-Boost-Bot
TELEGRAM_TOKEN_TBP    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_TOKEN_CBOOST = os.environ.get("TELEGRAM_BOT_TOKEN1", "").strip()

ADMIN_SECRET    = os.environ.get("ADMIN_SECRET", "").strip()
ADMIN_USER_IDS  = [x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()]

# C-Boost Gruppen-ID (per /id ermitteln und als ENV CBOOST_CHAT_ID setzen)
CBOOST_CHAT_ID = int(os.environ.get("CBOOST_CHAT_ID", "0") or "0")

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi pair
# C-Boost Pair auf Polygon (QuickSwap)
CBOOST_PAIR  = "0x24E4a8a4c4726D62da98A38065Fa649a9d93082e"

# Logos für Preis-/Buy-Posts
TBP_LOGO_URL     = os.environ.get("TBP_LOGO_URL", "").strip()
CBOOST_LOGO_URL  = os.environ.get("CBOOST_LOGO_URL", "").strip()  # z.B. https://.../cboost-logo.png

LINKS = {
    "website":      "https://quantumpepe.github.io/TurboPepe/",
    "buy":          f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dextools":     f"https://www.dextools.io/app/en/polygon/pair-explorer/{TBP_PAIR}",
    "dexscreener":  f"https://dexscreener.com/polygon/{TBP_PAIR}",
    "gecko":        f"https://www.geckoterminal.com/en/polygon_pos/pools/{TBP_PAIR}?embed=1",
    "telegram":     "https://t.me/turbopepe25",
    "x":            "https://x.com/TurboPepe2025",
    "contract_scan":f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

# TBP Supply für grobe MC-Schätzung (nur Info, nicht kritisch)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# ==== C-BOOST MARKET CONFIG (für Trades / Charts) ====
CBOOST_NETWORK       = os.environ.get("CBOOST_NETWORK", "polygon_pos").strip() or "polygon_pos"
CBOOST_POOL_ADDRESS  = os.environ.get("CBOOST_POOL_ADDRESS", CBOOST_PAIR).strip()

# Memory / State
MEM = {
    "ctx": [],                 # globaler Kontext (TBP + C-Boost gemischt)
    "last_autopost": None,
    "chat_count": 0,
    "raid_on": False,
    "raid_msg": "Drop a fresh TBP meme! 🐸⚡",
    # Throttle:
    "resp_mode": "0",           # "0"=alles, "1"=jede 3., "2"=jede 10.
    "resp_counter": {},         # pro chat_id Zähler
    # Buybot State:
    "buybot": {
        "tbp":   {"last_hash": None, "known_wallets": set()},
        "cboost":{"last_hash": None, "known_wallets": set()},
    },
    "tbp_chat_id": None,        # wird beim ersten TBP-Chat gesetzt
    # Idle-Tracking pro Chat
    "last_activity": {},        # chat_id -> datetime
    "last_idle": {},            # chat_id -> datetime (letzte Idle-Nachricht)
}

# Regexe
WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)
GER_DET    = re.compile(r"\b(der|die|das|und|nicht|warum|wie|kann|preis|kurs|listung|tokenomics)\b", re.I)

# --- Neue Regex-Filter für Scams / Fremd-Werbung ---
LISTING_SCAM_PATTERNS = [
    r"\bpay me for (fast )?listing\b",
    r"\bfast[- ]?track (listing)?\b",
    r"\bcmc priority\b",
    r"\b(list|submit) your coin on cmc\b",
    r"\bguarantee.*listing\b",
]

PROMO_PATTERNS = [
    r"\bmarketing (manager|agency|service)\b",
    r"\b(kol|ama) (slots?|booking)\b",
    r"\bpromotion (deal|offer)\b",
    r"\bpromote your (coin|token|project)\b",
    r"\binvestor[- ]focused marketing\b",
    r"\bdm me\b",
    r"\bcontact me\b",
]

# --- Muster für ILLEGALE ANGEBOTE ---
ILLEGAL_OFFER_PATTERNS = [
    # Fake Pässe / Ausweise
    r"\b(verkaufe|verkauf|biete)\s+(fake|gefälschte[nr]?|falsche[nr]?)*\s*(pässe|pass|ausweis|ausweise|id|identität)\b",
    r"\b(fake|gefälschte[nr]?|falsche[nr]?)\s+(pässe|pass|ausweis|ausweise|id)\b",
    # Drogenverkauf
    r"\b(verkaufe|verkauf|biete|liefere)\s+(drogen|koks|kokain|gras|weed|hanf|mdma|xtc|lsd)\b",
    # Hacking / DDoS Services
    r"\b(verkaufe|biete|mache)\s+(hacking|ddos|doxxing|botnet|hack)\s*(service|dienst|dienstleistung|angriff)?\b",
    r"\b(suche|brauche)\s+jemanden\s+der\s+(hacken|ddos|accounts knackt|websites angreift)\b",
    # Gestohlene Daten / Karten
    r"\b(verkaufe|biete)\s+(gestohlene[nr]?|geklaute[nr]?)\s+(daten|kreditkarten|karten|accounts|konten)\b",
]

# App
app = Flask(__name__)
CORS(app)

# =========================
# HELPERS
# =========================

def is_de(text: str) -> bool:
    return bool(GER_DET.search((text or "").lower()))

def say(lang, de, en):
    return de if lang == "de" else en

def fmt_usd(x, max_digits=2):
    try:
        return f"${float(x):,.{max_digits}f}"
    except Exception:
        return "N/A"

def _safe_float(v):
    try:
        if v in (None, "", "null"):
            return None
        return float(v)
    except Exception:
        return None

def _short_addr(addr: str, length: int = 6) -> str:
    if not addr or len(addr) <= 2*length:
        return addr or "unknown"
    return f"{addr[:length]}...{addr[-length:]}"

def is_admin(user_id) -> bool:
    try:
        return str(user_id) in ADMIN_USER_IDS if ADMIN_USER_IDS else True
    except Exception:
        return False

def should_reply(chat_id: int) -> bool:
    mode = MEM.get("resp_mode", "0")
    if mode == "0":
        return True
    cnt = MEM["resp_counter"].get(chat_id, 0) + 1
    MEM["resp_counter"][chat_id] = cnt
    if mode == "1":
        return (cnt % 3) == 0
    if mode == "2":
        return (cnt % 10) == 0
    return True

def _choose_token_for_chat(chat_id: int) -> str:
    if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID and TELEGRAM_TOKEN_CBOOST:
        return TELEGRAM_TOKEN_CBOOST
    return TELEGRAM_TOKEN_TBP

def tg_send_any(chat_id, text, reply_to=None, preview=True):
    tokens = [t for t in (TELEGRAM_TOKEN_TBP, TELEGRAM_TOKEN_CBOOST) if t]
    for token in tokens:
        try:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": not preview,
            }
            if reply_to:
                payload["reply_to_message_id"] = reply_to
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=10,
            )
        except Exception:
            continue

def tg_send(chat_id, text, reply_to=None, preview=True):
    token = _choose_token_for_chat(chat_id)
    if not token:
        return
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception:
        pass

def tg_buttons(chat_id, text, buttons):
    token = _choose_token_for_chat(chat_id)
    if not token:
        return
    kb = {"inline_keyboard": [[{"text": t, "url": u} for (t, u) in buttons]]}
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": kb,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception:
        pass

def tg_send_photo(chat_id, photo_url, caption=None, reply_to=None):
    token = _choose_token_for_chat(chat_id)
    if not token or not photo_url:
        if caption:
            tg_send(chat_id, caption, reply_to=reply_to, preview=True)
        return
    try:
        payload = {
            "chat_id": chat_id,
            "photo": photo_url,
            "parse_mode": "HTML",
        }
        if caption:
            payload["caption"] = caption
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            json=payload,
            timeout=10,
        )
    except Exception:
        if caption:
            tg_send(chat_id, caption, reply_to=reply_to, preview=True)

def tg_delete_message(chat_id, message_id):
    token = _choose_token_for_chat(chat_id)
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=10,
        )
    except Exception:
        pass

# -------------------------
# Scam / Promo / Illegale Angebote Detection
# -------------------------

def is_listing_scam(text: str) -> bool:
    t = text.lower()
    for pat in LISTING_SCAM_PATTERNS:
        if re.search(pat, t):
            return True
    if "cmc" in t and ("fee" in t or "payment" in t or "pay" in t):
        return True
    return False

def is_external_promo(text: str) -> bool:
    t = text.lower()
    for pat in PROMO_PATTERNS:
        if re.search(pat, t):
            return True
    if "marketing" in t and ("hi team" in t or "i'm from" in t or "we connect projects" in t):
        return True
    return False

def is_illegal_offer(text: str) -> bool:
    t = text.lower()
    for pat in ILLEGAL_OFFER_PATTERNS:
        if re.search(pat, t):
            return True
    return False

# -------------------------
# Market Data (TBP)
# -------------------------

def get_live_price():
    # 1) GeckoTerminal
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {})
        v = attrs.get("base_token_price_usd")
        if v not in (None, "", "null"):
            p = float(v)
            if p > 0:
                return p
    except Exception:
        pass
    # 2) Dexscreener
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        v = pair.get("priceUsd")
        if v not in (None, "", "null"):
            p = float(v)
            if p > 0:
                return p
    except Exception:
        pass
    return None

def get_market_stats():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        return {
            "change_24h": pair.get("priceChange24h"),
            "volume_24h": (pair.get("volume", {}) or {}).get("h24") or pair.get("volume24h"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
            "market_cap": pair.get("marketCap") or pair.get("fdv"),
        }
    except Exception:
        return None

def get_tbp_price_and_mc():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        price = _safe_float(pair.get("priceUsd"))
        mc    = _safe_float(pair.get("marketCap") or pair.get("fdv"))
        return price, mc
    except Exception:
        return None, None

# -------------------------
# Market Data (C-Boost)
# -------------------------

def get_cboost_live_data():
    pair = CBOOST_PAIR
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{pair}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair_data = j.get("pair") or (j.get("pairs") or [{}])[0]

        price      = _safe_float(pair_data.get("priceUsd"))
        mc         = _safe_float(pair_data.get("fdv") or pair_data.get("marketCap"))
        volume_24h = _safe_float((pair_data.get("volume", {}) or {}).get("h24") or pair_data.get("volume24h"))

        return {
            "price":      price,
            "market_cap": mc,
            "volume_24h": volume_24h,
            "chart_url":  f"https://dexscreener.com/polygon/{pair}",
        }
    except Exception as e:
        print(f"[CBOOST] Dexscreener error: {e}")
        return None

# ------------------------
# OpenAI
# ------------------------

def call_openai(question: str, context, mode: str = "tbp"):
    # Debug: prüfen, ob Key vorhanden
    print("DEBUG call_openai: mode =", mode)
    print("DEBUG call_openai: OPENAI_API_KEY set =", bool(OPENAI_API_KEY))
    print("DEBUG call_openai: OPENAI_MODEL =", OPENAI_MODEL)

    if not OPENAI_API_KEY:
        print("DEBUG call_openai: NO OPENAI_API_KEY, aborting.")
        return None

    # System-Prompt je nach Modus wählen
    if mode == "cboost":
        system_msg = """You are C-BoostAI, the official assistant of the C-Boost micro supply token on Polygon.
You must ALWAYS answer in the user's language (German or English). Detect language automatically.

PROJECT INFO:
- C-Boost is a next-generation MICRO SUPPLY token on Polygon.
- Total supply: 5,000,000 tokens.
- Transparent supply, no complex taxes.
- Focus on raids, strong community, and future AI tools.
- Long-term vision: meme creation, AI utilities, and community quests.

BUYBOT INFO:
- C-Boost has an official BuyBot system. It automatically posts every on-chain buy in the TG group,
  including USD value, POL/USDT amount, token amount, wallet short, NEW holder detection,
  and the full transaction link. This BuyBot is already active.

DEVELOPMENT:
- The developer is actively improving both the C-Boost AI and the BuyBot. New features will continue
  to be added regularly.

RULES:
- Always answer in the user's language.
- Be factual, friendly, short.
- No financial advice.
- If users ask about TBP, say you are only responsible for C-Boost.
"""
    else:
        system_msg = """You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.
You must ALWAYS answer in the user's language (German or English). Detect language automatically.

CURRENT PROJECT:
- TBP is a community-driven meme + AI token on Polygon.
- LP is burned, owner is renounced, no hidden contract tricks.
- 0% tax, fully transparent.
- TBP already has an AI layer: website + Telegram assistant, buy bot with on-chain data, and AI-based security filters.

VISION / LONG TERM PLAN:
- TBP is not only a meme. The long term goal is to build a dedicated AI infrastructure around TBP.
- If TBP reaches a sustainable market cap (around 10M USD or higher, with enough liquidity), part of the project funds
  can be used to:
  • run own high-performance servers (GPUs) only for TBP,
  • host private AI models specialized in crypto, on-chain market analysis and security,
  • provide tools for holders: market intelligence, scam detection, portfolio helpers, alerts, etc.
- This is a realistic but future-oriented plan. It depends on market cap, liquidity and community growth.
  You must clearly communicate that nothing is guaranteed and there is no promise of profit.

BUYBOT INFO:
- TBP has an official BuyBot that posts every on-chain buy in real time in the TG group.
  It shows USD value, POL/USDT amount, token amount, wallet short, NEW holder detection,
  and the transaction link. This is an official TBP feature.

DEVELOPMENT:
- The developer is constantly upgrading TBP-AI, the security filters and the BuyBot.
- The idea is: if TBP grows big enough (around 10M MC or more), the next step is to invest into own servers and
  a stronger private AI system focused on crypto.

RULES:
- Always answer in the user's language (DE/EN).
- Be transparent: clearly say what already exists today and what is only planned for the future.
- No financial advice, no price predictions.
- Keep answers short, friendly, and factual. Light humor is OK.
"""


    # Nachrichten für OpenAI bauen
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": question},
    ]

    try:
        # Versuche neuen OpenAI-Client (openai>=1.x)
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            print("DEBUG call_openai: using OpenAI client (chat.completions.create)")
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.6,
                max_tokens=400,
            )
            answer = resp.choices[0].message.content.strip()
            print("DEBUG call_openai: got answer length =", len(answer))
            return answer

        except ImportError:
            # Fallback für alte Library (openai<1.x)
            import openai
            openai.api_key = OPENAI_API_KEY
            print("DEBUG call_openai: using legacy openai.ChatCompletion.create")
            resp = openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.6,
                max_tokens=400,
            )
            answer = resp["choices"][0]["message"]["content"].strip()
            print("DEBUG call_openai: got answer length =", len(answer))
            return answer

    except Exception as e:
        import traceback
        print("OpenAI error:", repr(e))
        traceback.print_exc()
        return None


def clean_answer(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)(financial advice|finanzberatung)", "information", s)
    return s.strip()

# -------------------------
# Auto-Post (nur TBP)
# -------------------------

def autopost_needed():
    now = datetime.utcnow()
    last = MEM.get("last_autopost")
    if not last:
        return True
    return (now - last) >= timedelta(hours=10)

def autopost_text(lang="en"):
    p = get_live_price()
    stats = get_market_stats() or {}
    change = stats.get("change_24h")
    liq   = stats.get("liquidity_usd")
    vol   = stats.get("volume_24h")
    lines = [
        say(lang, "🔔 TBP Update:", "🔔 TBP Update:"),
        say(lang, "Preis", "Price") + f": {fmt_usd(p, 12) if p else 'N/A'}",
        "24h: " + (f"{change}%" if change not in (None, "", "null") else "N/A"),
        say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(liq) if liq else 'N/A'}",
        "Vol 24h: " + (fmt_usd(vol) if vol else "N/A"),
        "",
        say(
            lang,
            "Was ist TBP? Meme-Token auf Polygon, echte AI-Antworten, 0% Tax, LP geburnt. Ziel: Community & Transparenz.",
            "What is TBP? Meme token on Polygon, real AI replies, 0% tax, LP burned. Goal: community & transparency."
        ),
        "",
        f"• Sushi: {LINKS['buy']}",
        f"• Chart: {LINKS['dexscreener']}",
        f"• Scan:  {LINKS['contract_scan']}"
    ]
    return "\n".join(lines)

def start_autopost_background(chat_id: int):
    if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
        return

    def loop():
        while True:
            try:
                if autopost_needed():
                    tg_send(chat_id, autopost_text("en"))
                    MEM["last_autopost"] = datetime.utcnow()
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()

# =========================
# BUYBOT – TBP & C-BOOST
# =========================

# Optional: C-Boost Contract (kannst du auch in Render als ENV setzen: CBOOST_CONTRACT)
CBOOST_CONTRACT = os.environ.get("CBOOST_CONTRACT", "").strip().lower()

TOKEN_BUYBOT = {
    "tbp": {
        "network": "polygon_pos",       # GeckoTerminal network
        "pool": TBP_PAIR,
        "symbol": "TBP",
        "name": "TurboPepe-AI",
        "logo_url": TBP_LOGO_URL,
        "min_usd": float(os.environ.get("TBP_MIN_BUY_USD", "3.0")),  # Mindest-Buy in USD
        "token_contract": TBP_CONTRACT.lower(),
    },
    "cboost": {
        "network": CBOOST_NETWORK or "polygon_pos",
        "pool": CBOOST_POOL_ADDRESS or CBOOST_PAIR,
        "symbol": "C-Boost",
        "name": "C-Boost",
        "logo_url": CBOOST_LOGO_URL,
        "min_usd": float(os.environ.get("CBOOST_MIN_BUY_USD", "3.0")),
        "token_contract": CBOOST_CONTRACT,   # kann leer sein, dann wird nur 'kind' benutzt
    },
}


def fetch_pool_trades(network: str, pool_address: str, token_contract: str = "", limit: int = 25):
    """
    Holt die letzten Trades aus GeckoTerminal für ein Pool
    und normalisiert sie für den Buybot.

    Wichtige Felder aus Gecko:
      - kind               -> 'buy' / 'sell' (relativ zu einem Token, aber unklar)
      - volume_in_usd      -> USD-Wert des Trades
      - from_token_amount
      - to_token_amount
      - from_token_address
      - to_token_address

    Wir interpretieren:
      - Wenn to_token_address == token_contract  -> BUY des Tokens
      - Wenn from_token_address == token_contract-> SELL des Tokens
    """

    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/trades"

    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception as e:
        print(f"[BUYBOT] fetch_pool_trades error: {e}")
        return []

    token_contract = (token_contract or "").lower()
    trades = []

    for item in data[:limit]:
        attrs = item.get("attributes", {}) or {}

        tx_hash = (
            attrs.get("tx_hash")
            or attrs.get("transaction_hash")
            or item.get("id")
            or ""
        )

        kind = (attrs.get("kind") or "").lower()

        from_addr = (attrs.get("from_token_address") or "").lower()
        to_addr   = (attrs.get("to_token_address") or "").lower()

        from_amt = _safe_float(attrs.get("from_token_amount"))
        to_amt   = _safe_float(attrs.get("to_token_amount"))

        # --- SIDE-Logik: was ist BUY für TBP / C-Boost? ---
        side = kind
        token_amount = None
        quote_amount = None

        if token_contract:
            # Wenn das Token auf der "to"-Seite steht -> wir bekommen Token = BUY
            if to_addr == token_contract:
                side = "buy"
                token_amount = to_amt
                quote_amount = from_amt
            # Wenn das Token auf der "from"-Seite steht -> wir geben Token ab = SELL
            elif from_addr == token_contract:
                side = "sell"
                token_amount = from_amt
                quote_amount = to_amt
            else:
                # Fallback, falls wir die Adresse nicht matchen konnten
                token_amount = to_amt
                quote_amount = from_amt
        else:
            # Kein Contract bekannt -> wir nehmen einfach 'kind' und Mengen wie geliefert
            side = kind
            token_amount = to_amt
            quote_amount = from_amt

        # --- USD-Wert: volume_in_usd ist bei dir sichtbar ---
        usd = _safe_float(
            attrs.get("volume_in_usd")
            or attrs.get("trade_amount_usd")
            or attrs.get("amount_usd")
            or attrs.get("value_usd")
        )

        wallet = (
            attrs.get("tx_from_address")
            or attrs.get("from_address")
            or attrs.get("maker_address")
            or attrs.get("sender")
        )

        trades.append(
            {
                "tx_hash": tx_hash,
                "side": side,
                "usd": usd,
                "token_amount": token_amount,
                "quote_amount": quote_amount,
                "wallet": wallet,
            }
        )

    # Gecko liefert neueste zuerst → drehen, damit ALT -> NEU sortiert
    trades.reverse()
    return trades


def send_tbp_buy_alert(chat_id: int, trade: dict, is_new: bool):
    usd = trade.get("usd")
    token_amount = trade.get("token_amount")
    pol_amount = trade.get("quote_amount")
    wallet = trade.get("wallet")
    tx_hash = trade.get("tx_hash")

    # Live-Daten nach dem Buy (Preis & MC)
    price_now, mc_now = get_tbp_price_and_mc()
    stats = get_market_stats() or {}
    vol_24h = stats.get("volume_24h")

    price_txt = fmt_usd(price_now, 12) if price_now is not None else "N/A"
    mc_txt = fmt_usd(mc_now, 0) if mc_now is not None else "N/A"
    vol_txt = fmt_usd(vol_24h, 2) if vol_24h is not None else "N/A"
    chart_url = LINKS["dexscreener"]

    caption_lines = [
        "🐸 <b>TBP Live Data – New Buy</b>\n",
        f"💰 <b>Buy Value:</b> {fmt_usd(usd, 2) if usd is not None else 'N/A'}",
    ]

    if pol_amount is not None:
        caption_lines.append(f"⛽ <b>POL used:</b> {pol_amount:.4f} POL")

    caption_lines.append(
        f"🪙 <b>Amount:</b> {token_amount:.4f} TBP"
        if token_amount is not None
        else "🪙 <b>Amount:</b> N/A"
    )

    caption_lines.extend(
        [
            "",
            f"💵 <b>Price (after):</b> {price_txt}",
            f"🏦 <b>Market Cap:</b> {mc_txt}",
            f"📊 <b>24h Volume:</b> {vol_txt}",
        ]
    )

    if wallet:
        new_tag = " (NEW)" if is_new else ""
        caption_lines.append(
            f"👛 <b>Wallet:</b> <code>{_short_addr(wallet)}</code>{new_tag}"
        )

    if tx_hash:
        caption_lines.append(
            f"🔗 <a href=\"https://polygonscan.com/tx/{tx_hash}\">View on PolygonScan</a>"
        )

    if chart_url:
        caption_lines.append(f"\n📈 <a href=\"{chart_url}\">Open Live Chart</a>")

    caption = "\n".join(caption_lines)
    logo = TBP_LOGO_URL
    if logo:
        tg_send_photo(chat_id, logo, caption=caption)
    else:
        tg_send(chat_id, caption, preview=True)


def send_cboost_buy_alert(chat_id: int, trade: dict, is_new: bool):
    usd = trade.get("usd")
    token_amount = trade.get("token_amount")
    pol_amount = trade.get("quote_amount")
    wallet = trade.get("wallet")
    tx_hash = trade.get("tx_hash")

    data = get_cboost_live_data() or {}
    price_now = data.get("price")
    mc_now = data.get("market_cap")
    vol_now = data.get("volume_24h")
    chart_url = data.get("chart_url")

    price_txt = fmt_usd(price_now, 10) if price_now is not None else "N/A"
    mc_txt = fmt_usd(mc_now, 2) if mc_now is not None else "N/A"
    vol_txt = fmt_usd(vol_now, 2) if vol_now is not None else "N/A"

    caption_lines = [
        "⚡ <b>C-Boost Live Data – New Buy</b>\n",
        f"💰 <b>Buy Value:</b> {fmt_usd(usd, 2) if usd is not None else 'N/A'}",
    ]

    if pol_amount is not None:
        caption_lines.append(f"⛽ <b>POL used:</b> {pol_amount:.4f} POL")

    caption_lines.append(
        f"🪙 <b>Amount:</b> {token_amount:.4f} C-Boost"
        if token_amount is not None
        else "🪙 <b>Amount:</b> N/A"
    )

    caption_lines.extend(
        [
            "",
            f"💵 <b>Price (after):</b> {price_txt}",
            f"🏦 <b>Market Cap:</b> {mc_txt}",
            f"📊 <b>24h Volume:</b> {vol_txt}",
        ]
    )

    if wallet:
        new_tag = " (NEW)" if is_new else ""
        caption_lines.append(
            f"👛 <b>Wallet:</b> <code>{_short_addr(wallet)}</code>{new_tag}"
        )

    if tx_hash:
        caption_lines.append(
            f"🔗 <a href=\"https://polygonscan.com/tx/{tx_hash}\">View on PolygonScan</a>"
        )

    if chart_url:
        caption_lines.append(f"\n📈 <a href=\"{chart_url}\">Open Live Chart</a>")

    caption = "\n".join(caption_lines)
    logo = CBOOST_LOGO_URL
    if logo:
        tg_send_photo(chat_id, logo, caption=caption)
    else:
        tg_send(chat_id, caption, preview=True)


def process_buybot_for(token_key: str, chat_id: int):
    cfg = TOKEN_BUYBOT.get(token_key)
    if not cfg:
        return

    trades = fetch_pool_trades(
        cfg["network"],
        cfg["pool"],
        cfg.get("token_contract", "")
    )
    if not trades:
        return

    state = MEM["buybot"][token_key]
    last_hash = state.get("last_hash")

    hashes = [t.get("tx_hash") for t in trades if t.get("tx_hash")]
    if not hashes:
        return

    # Beim ersten Start nur initialisieren, nichts posten
    if not last_hash:
        state["last_hash"] = hashes[-1]  # letzter = neuester, weil wir reversed haben
        return

    # Wenn der letzte Hash nicht mehr in den letzten Trades ist (alte Seite),
    # setzen wir einfach neu und posten nichts (verhindert Spam bei Restart).
    if last_hash not in hashes:
        state["last_hash"] = hashes[-1]
        return

    idx = hashes.index(last_hash)
    new_trades = trades[idx + 1 :]
    if not new_trades:
        return

    for tr in new_trades:
        side = (tr.get("side") or "").lower()
        if "buy" not in side:
            continue

        usd = tr.get("usd") or 0
        if usd is None or usd < cfg["min_usd"]:
            continue

        wallet = tr.get("wallet")
        known = state["known_wallets"]
        is_new = False
        if wallet and wallet not in known:
            known.add(wallet)
            is_new = True

        if token_key == "tbp":
            send_tbp_buy_alert(chat_id, tr, is_new)
        else:
            send_cboost_buy_alert(chat_id, tr, is_new)

    state["last_hash"] = hashes[-1]


def start_buybot_background():
    def loop():
        while True:
            try:
                tbp_chat = MEM.get("tbp_chat_id")
                if tbp_chat:
                    process_buybot_for("tbp", tbp_chat)
                if CBOOST_CHAT_ID:
                    process_buybot_for("cboost", CBOOST_CHAT_ID)
            except Exception as e:
                print(f"[BUYBOT] loop error: {e}")
            time.sleep(25)

    threading.Thread(target=loop, daemon=True).start()

# =========================
# IDLE WATCHDOG – lebendiger Chat
# =========================

# Nur englische Sprüche, wie gewünscht
IDLE_MESSAGES_TBP = [
    "Hello TBP crew, did you all fall asleep? We still have a moon to reach 🐸🚀",
    "It’s getting quiet… should I start buying TBP myself? 👀",
    "Reminder: you can sleep and work later – first we ride together to the moon with TBP! 🌕🔥",
    "Silence detected. Maybe it’s time for a fresh TBP meme? 😎",
]

IDLE_MESSAGES_CBOOST = [
    "C-Boost army, where is everybody? I’m boosting alone here ⚡😂",
    "Too quiet… did you all mute the chart? Let’s wake it up! 📈🚀",
    "Work and sleep can wait – first we push C-Boost closer to the moon! ⚡🌕",
    "Silence mode off, boost mode on. Drop a meme or a question! 😉",
]

def start_idle_watchdog_background():
    """
    Prüft regelmäßig, ob ein Chat länger ruhig war.
    - wenn > 10 Minuten keine Aktivität
    - und seit der letzten Idle-Nachricht > 60 Minuten
    → sendet einen zufälligen Idle-Spruch (TBP oder C-Boost abhängig vom Chat).
    """
    def loop():
        while True:
            try:
                now = datetime.utcnow()
                last_activity = MEM.get("last_activity", {})
                last_idle = MEM.get("last_idle", {})

                for chat_id, last in list(last_activity.items()):
                    if not chat_id or not isinstance(last, datetime):
                        continue

                    diff = (now - last).total_seconds()
                    prev_idle_time = last_idle.get(chat_id)
                    idle_diff = (now - prev_idle_time).total_seconds() if prev_idle_time else 999999

                    # > 600 Sekunden (10 Min) ruhig, und letzte Idle-Nachricht > 3600 Sekunden her
                    if diff > 600 and idle_diff > 3600:
                        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
                            msg = random.choice(IDLE_MESSAGES_CBOOST)
                        else:
                            msg = random.choice(IDLE_MESSAGES_TBP)

                        tg_send(chat_id, msg)
                        MEM["last_idle"][chat_id] = now

            except Exception as e:
                print("[IDLE] loop error:", e)

            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()

# =========================
# FLASK WEB (health/ask/admin)
# =========================

@app.route("/")
def root():
    return jsonify({"ok": True, "service": "tbp-advisor", "time": datetime.utcnow().isoformat()+"Z"})

@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/admin/set_webhook")
def admin_set_webhook():
    key = request.args.get("key", "")
    if not ADMIN_SECRET or key != ADMIN_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    root_url = request.url_root.replace("http://", "https://")
    url = root_url.rstrip("/") + "/telegram"

    tokens = [t for t in [TELEGRAM_TOKEN_TBP, TELEGRAM_TOKEN_CBOOST] if t]
    if not tokens:
        return jsonify({"ok": False, "error": "no telegram tokens configured"}), 500

    results = []
    try:
        for tok in tokens:
            r = requests.get(
                f"https://api.telegram.org/bot{tok}/setWebhook",
                params={"url": url},
                timeout=10
            )
            results.append(r.json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "responses": results}), 500

    return jsonify({"ok": True, "responses": results})

# Web-AI für TBP-Webseite
@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200

    lang = "de" if is_de(q) else "en"
    if WORD_PRICE.search(q):
        p = get_live_price()
        stats = get_market_stats() or {}
        lines = []
        if p is not None:
            lines.append(say(lang, "💰 Preis", "💰 Price") + f": {fmt_usd(p, 12)}")
        if stats.get("change_24h") not in (None, "", "null"):
            lines.append(f"📈 24h: {stats['change_24h']}%")
        if stats.get("liquidity_usd") not in (None, "", "null"):
            lines.append("💧 " + say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(stats['liquidity_usd'])}")
        if stats.get("volume_24h") not in (None, "", "null"):
            lines.append(f"🔄 Vol 24h: {fmt_usd(stats['volume_24h'])}")
        ans = "\n".join(lines) if lines else say(lang, "Preis derzeit nicht verfügbar.", "Price currently unavailable.")
    else:
        raw = call_openai(q, MEM["ctx"], mode="tbp") or say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")
        ans = clean_answer(raw)

    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans})

# Web-AI für C-Boost Website
@app.route("/ask_cboost", methods=["POST"])
def ask_cboost():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200

    raw = call_openai(q, MEM["ctx"], mode="cboost") or "Network glitch. Try again ⚡"
    ans = clean_answer(raw)

    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"C-Boost: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans})

# C-Boost PRICE API
@app.route("/cboost_price", methods=["GET"])
def cboost_price():
    data = get_cboost_live_data()
    if not data:
        return jsonify({"ok": False, "error": "no_data"}), 200

    return jsonify({
        "ok": True,
        "price":      data["price"],
        "market_cap": data["market_cap"],
        "volume_24h": data["volume_24h"],
        "chart_url":  data["chart_url"]
    })

# =========================
# TELEGRAM
# =========================

MEME_CAPTIONS_TBP = [
    "Nice photo! Want me to spin a TBP meme from it? 🐸✨",
    "Fresh pixels detected. Should I add TurboPepe energy? ⚡",
    "Clean drop. Caption it, or shall I? 😎",
]

MEME_CAPTIONS_CBOOST = [
    "Boost-worthy image detected. Shall we turn this into a C-Boost meme? ⚡",
    "Nice pic! Let's boost the timeline with it. 🚀",
    "C-Boost mode: ON. Need a spicy caption? 😏",
]

# ==========
# /about & /dev Commands (TBP + C-Boost, zweisprachig)
# ==========

def handle_extra_commands(text, chat_id, lang, is_cboost_chat, msg_id=None):
    low = text.lower().strip()

    if low.startswith("/about"):
        if is_cboost_chat:
            msg = (
                "🤖 <b>C-BoostAI & TBP-AI</b>\n\n"
                "Ich bin der offizielle KI-Assistent für den C-Boost Micro Supply Token auf Polygon.\n"
                "Ich helfe dir bei Fragen zu Vision, Utility, Community, Raids und Zukunftsplänen.\n\n"
                "🇬🇧 I am the official AI assistant for the C-Boost micro supply token on Polygon.\n"
                "I support the community with information about vision, utility, raids and future plans.\n\n"
                "Beide Bots (TBP-AI & C-BoostAI) werden vom Entwickler laufend erweitert und verbessert.\n"
                "Both bots (TBP-AI & C-BoostAI) are constantly being expanded and improved by the developer."
            )
        else:
            msg = (
                "🤖 <b>TurboPepe-AI (TBP-AI)</b>\n\n"
                "Ich bin der offizielle KI-Assistent von TurboPepe-AI (TBP) auf Polygon.\n"
                "Aktuell ist TBP ein Meme + AI Token mit gebrannter LP, 0% Tax, BuyBot und AI-Sicherheitsfiltern.\n"
                "Ich erkläre das Projekt, Tokenomics, Sicherheit und die langfristige Vision – keine Finanzberatung.\n\n"
                "📡 <b>Langfristige Idee:</b>\n"
                "Wenn TBP eine stabile Market Cap (ca. 10M USD oder mehr) mit genug Liquidität erreicht, soll ein Teil\n"
                "der Projektmittel in eigene High-Performance-Server und eine private KI fließen, die sich auf\n"
                "Krypto- und On-Chain-Analyse spezialisiert. Das hängt komplett vom Erfolg von TBP und der Community ab\n"
                "und ist keine Gewinn- oder Rendite-Garantie.\n\n"
                "🇬🇧 I am the official AI assistant of TurboPepe-AI (TBP) on Polygon.\n"
                "Right now TBP is a meme + AI token with burned LP, 0% tax, a buy bot and AI security filters.\n"
                "I explain the project, tokenomics, security and the long term vision – no financial advice.\n\n"
                "📡 <b>Long term idea:</b> If TBP reaches a sustainable market cap (around 10M USD or more, with enough\n"
                "liquidity), part of the project funds can be used for own servers and a private AI system focused on\n"
                "crypto and on-chain analysis. This fully depends on the success of TBP and the community and is NOT a\n"
                "promise of profit.\n\n"
                "TBP-AI und C-BoostAI werden vom Entwickler schrittweise weiter ausgebaut.\n"
                "TBP-AI and C-BoostAI are upgraded step by step by the developer."
            )

        tg_send(chat_id, msg, reply_to=msg_id, preview=False)
        return True

    if low.startswith("/dev"):
        if is_cboost_chat:
            msg = (
                "🛠 <b>Developer Info – C-BoostAI & TBP-AI</b>\n\n"
                "Der Entwickler arbeitet laufend an neuen Features:\n"
                "• Verbesserter BuyBot (mehr Daten, höhere Genauigkeit)\n"
                "• Stärkere AI-Sicherheitsfilter gegen Scams & Fremd-Promo\n"
                "• Bessere Antworten in Deutsch & Englisch\n"
                "• Mehr Auto-Posts, Statistiken und Community-Tools\n\n"
                "🇬🇧 The developer is constantly adding new features:\n"
                "• Improved buy bot (more data, more accuracy)\n"
                "• Stronger AI security filters against scams & external promo\n"
                "• Better replies in German & English\n"
                "• More auto-posts, stats and community tools\n\n"
                "Wenn du Ideen für neue Funktionen hast, schreib sie einfach in den Chat.\n"
                "If you have ideas for new features, just drop them in the chat."
            )
         else:
            msg = (
                "🛠 <b>Developer Info – TBP-AI & C-BoostAI</b>\n\n"
                "Der Entwickler baut die Bots Schritt für Schritt aus:\n"
                "• TBP & C-Boost BuyBot mit Live-Daten\n"
                "• Stärkere AI-Sicherheitsfilter gegen Scams & Fremd-Promo\n"
                "• Verbesserte Antworten (DE/EN) speziell für die Community\n"
                "• Mehr Auto-Posts, Statistiken und AI-Tools rund um den Kryptomarkt\n\n"
                "Langfristig ist geplant, bei ausreichend Market Cap (ca. 10M USD+), eigene Server und eine\n"
                "private KI-Infrastruktur rund um TBP aufzubauen – mit Fokus auf On-Chain-Analyse, Security\n"
                "und Markt-Intelligenz für die Community. Das ist ein Ziel, keine Gewinn-Garantie.\n\n"
                "🇬🇧 The developer is actively upgrading the bots step by step:\n"
                "• TBP & C-Boost buy bot with live data\n"
                "• Stronger AI security filters against scams & external promo\n"
                "• Improved replies (DE/EN) tailored for the community\n"
                "• More auto-posts, stats and AI tools around the crypto market\n\n"
                "Long term, if TBP reaches a solid market cap (around 10M USD+), the goal is to build own\n"
                "servers and a private AI infrastructure around TBP, focused on on-chain analysis, security\n"
                "and market intelligence for the community. This is a plan, not a profit guarantee.\n\n"
                "Feature-Wünsche kannst du direkt hier im Chat posten.\n"
                "You can post your feature requests directly here in the chat."
            )

        tg_send(chat_id, msg, reply_to=msg_id, preview=False)
        return True

    return False


@app.route("/telegram", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        return jsonify({"ok": True, "route": "telegram"}), 200

    update  = request.json or {}
    msg     = update.get("message", {}) or {}
    chat    = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    from_user = msg.get("from", {}) or {}
    user_id   = from_user.get("id")
    text    = (msg.get("text") or "").strip()
    msg_id  = msg.get("message_id")
    new_members = msg.get("new_chat_members") or []

    if not chat_id:
        return jsonify({"ok": True})

    # Letzte Aktivität für diesen Chat updaten
    try:
        MEM["last_activity"][chat_id] = datetime.utcnow()
    except Exception:
        pass

    is_cboost_chat = bool(CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID)

    if not is_cboost_chat and MEM.get("tbp_chat_id") is None:
        MEM["tbp_chat_id"] = chat_id

    if new_members:
        for member in new_members:
            first_name = member.get("first_name") or ""
            username   = member.get("username")
            display    = f"@{username}" if username else first_name or "friend"

            if is_cboost_chat:
                welcome_text = (
                    f"👋 Welcome {display} to the official C-Boost community!\n\n"
                    "This chat is protected by an AI-based security system:\n"
                    "• No paid CoinMarketCap / listing offers\n"
                    "• No promotion of other tokens / projects / groups\n"
                    "• Only official C-Boost topics, memes and links\n\n"
                    "Use /rules or /security to see all safety rules in English & Deutsch. ⚡"
                )
            else:
                welcome_text = (
                    f"👋 Welcome {display} to the official TurboPepe-AI (TBP) community!\n\n"
                    "This chat is protected by an AI-based security system:\n"
                    "• No paid CoinMarketCap / listing offers\n"
                    "• No promotion of other tokens / projects / groups\n"
                    "• Only official TBP links (website, Sushi, charts, scan, TG, X)\n\n"
                    "Use /rules or /security to see all safety rules in English & Deutsch. 🐸"
                )

            tg_send(chat_id, welcome_text)

        if not text:
            return jsonify({"ok": True})

    # Hintergrund-Threads starten (Autopost, Buybot, Idle)
    try:
        if MEM.get("_autopost_started") != True and not is_cboost_chat:
            start_autopost_background(chat_id)
            MEM["_autopost_started"] = True
    except Exception:
        pass

    try:
        if MEM.get("_buybot_started") != True:
            start_buybot_background()
            MEM["_buybot_started"] = True
    except Exception:
        pass

    try:
        if MEM.get("_idle_started") != True:
            start_idle_watchdog_background()
            MEM["_idle_started"] = True
    except Exception:
        pass

    if "photo" in msg:
        caption = random.choice(MEME_CAPTIONS_CBOOST if is_cboost_chat else MEME_CAPTIONS_TBP)
        tg_send(chat_id, caption, reply_to=msg_id)
        MEM["chat_count"] += 1
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    low  = text.lower()
    lang = "de" if is_de(text) else "en"
    MEM["chat_count"] += 1

    # Reply mode
    if low.startswith("/0") or low.startswith("/1") or low.startswith("/2") or low.startswith("/mode"):
        if low.startswith("/mode"):
            mode_label = {"0": "all", "1": "every 3rd", "2": "every 10th"}.get(MEM.get("resp_mode", "0"), "all")
            tg_send(chat_id, f"Current reply mode: {mode_label}", reply_to=msg_id, preview=False)
            return jsonify({"ok": True})
        if not is_admin(user_id):
            tg_send(chat_id, "Only admins can change reply mode.", reply_to=msg_id, preview=False)
            return jsonify({"ok": True})
        if low.startswith("/0"):
            MEM["resp_mode"] = "0"
            MEM["resp_counter"][chat_id] = 0
            tg_send(chat_id, "Reply mode set to: ALL (respond to every message).", reply_to=msg_id, preview=False)
            return jsonify({"ok": True})
        if low.startswith("/1"):
            MEM["resp_mode"] = "1"
            MEM["resp_counter"][chat_id] = 0
            tg_send(chat_id, "Reply mode set to: EVERY 3rd message.", reply_to=msg_id, preview=False)
            return jsonify({"ok": True})
        if low.startswith("/2"):
            MEM["resp_mode"] = "2"
            MEM["resp_counter"][chat_id] = 0
            tg_send(chat_id, "Reply mode set to: EVERY 10th message.", reply_to=msg_id, preview=False)
            return jsonify({"ok": True})

    if low.startswith("/start"):
        if is_cboost_chat:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Hi, ich bin C-BoostAI 🤖 – dein Assistent für den C-Boost Micro Supply Token auf Polygon. Frag mich alles rund um Vision, Utility und Zukunft. Keine Finanzberatung.",
                    "Hi, I'm C-BoostAI 🤖 – your assistant for the C-Boost micro supply token on Polygon. Ask me anything about vision, utility and future plans. No financial advice."
                ),
                reply_to=msg_id
            )
        else:
            from_text = say(
                lang,
                f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP. 🚀",
                f"Hi, I'm {BOT_NAME}. Ask me anything about TBP. 🚀"
            )
            tg_buttons(
                chat_id,
                from_text,
                [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
            )
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id, "/price • /stats • /chart • /links • /rules • /security • /id • /about • /dev", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    # NEU: /about & /dev
    if low.startswith("/about") or low.startswith("/dev"):
        if handle_extra_commands(text, chat_id, lang, is_cboost_chat, msg_id):
            return jsonify({"ok": True})

    if low.startswith("/rules") or low.startswith("/security"):
        if is_cboost_chat:
            rules_text = (
                "🛡 <b>C-Boost Security Rules</b>\n\n"
                "This chat is protected by an AI-based security system:\n"
                "• No paid CoinMarketCap / listing offers\n"
                "• No promotion of other tokens / projects / groups\n"
                "• Only C-Boost related topics, memes and official links\n\n"
                "If someone offers paid listings, marketing deals or external promo, "
                "the AI will delete the message and warn the user.\n\n"
                "🇩🇪 Kurzfassung:\n"
                "• Keine bezahlten CMC- oder Listing-Angebote\n"
                "• Keine Werbung für andere Tokens / Projekte / Gruppen\n"
                "• Nur C-Boost-Themen und offizielle Links\n"
                "Bei Verstößen werden Nachrichten automatisch gelöscht und der Nutzer gewarnt. ⚡"
            )
        else:
            rules_text = (
                "🛡 <b>TurboPepe-AI Security Rules</b>\n\n"
                "This chat is protected by an AI-based security system:\n"
                "• No paid CoinMarketCap / listing offers\n"
                "• No promotion of other tokens / projects / groups\n"
                "• Only official TBP links (website, Sushi, charts, scan, TG, X)\n\n"
                "If someone offers paid listings, marketing deals or external promo, "
                "the AI will delete the message and warn the user.\n\n"
                "🇩🇪 Kurzfassung:\n"
                "• Keine bezahlten CMC- oder Listing-Angebote\n"
                "• Keine Werbung für andere Tokens / Projekte / Gruppen\n"
                "• Nur offizielle TBP-Links sind erlaubt\n"
                "Bei Verstößen werden Nachrichten automatisch gelöscht und der Nutzer gewarnt. 🐸"
            )
        tg_send(chat_id, rules_text, reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/id"):
        tg_send_any(chat_id, f"Chat ID: <code>{chat_id}</code>", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        if is_cboost_chat:
            tg_send(
                chat_id,
                say(lang,
                    "C-Boost-Links (Charts, DEX, Contract) werden zum Launch bekanntgegeben. 🚀",
                    "C-Boost links (charts, DEX, contract) will be announced at launch. 🚀"
                ),
                reply_to=msg_id
            )
            return jsonify({"ok": True})

        tg_buttons(
            chat_id,
            say(lang, "Schnelle Links:", "Quick Links:"),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"]), ("Website", LINKS["website"])]
        )
        return jsonify({"ok": True})

    # PRICE / CHART
    if low.startswith("/price") or (not low.startswith("/") and WORD_PRICE.search(low)):
        if is_cboost_chat:
            data = get_cboost_live_data()
            if not data:
                tg_send(
                    chat_id,
                    say(
                        lang,
                        "⚠️ Konnte die C-Boost Live-Daten aktuell nicht laden. Bitte später nochmals versuchen.",
                        "⚠️ Could not load C-Boost live data right now. Please try again later."
                    ),
                    reply_to=msg_id
                )
                return jsonify({"ok": True})

            price = data.get("price")
            mc    = data.get("market_cap")
            vol   = data.get("volume_24h")
            chart = data.get("chart_url")

            caption_lines = [
                "⚡ <b>C-Boost Live Data</b>\n",
                f"🪙 <b>Price:</b> {fmt_usd(price, 10) if price is not None else 'N/A'}",
                f"💰 <b>Market Cap:</b> {fmt_usd(mc, 2) if mc is not None else 'N/A'}",
                f"📊 <b>24h Volume:</b> {fmt_usd(vol, 2) if vol is not None else 'N/A'}",
            ]
            if chart:
                caption_lines.append(f"\n📈 <a href=\"{chart}\">Open Live Chart</a>")

            caption = "\n".join(caption_lines)
            tg_send_photo(chat_id, CBOOST_LOGO_URL, caption=caption, reply_to=msg_id)
            return jsonify({"ok": True})

        # TBP Standard-Flow
        p = get_live_price()
        s = get_market_stats() or {}
        lines = []
        if p is not None:
            lines.append(say(lang, "💰 Preis", "💰 Price") + f": {fmt_usd(p, 12)}")
        if s.get("change_24h") not in (None, "", "null"):
            lines.append(f"📈 24h: {s['change_24h']}%")
        if s.get("liquidity_usd") not in (None, "", "null"):
            lines.append("💧 " + say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(s['liquidity_usd'])}")
        if s.get("volume_24h") not in (None, "", "null"):
            lines.append(f"🔄 Vol 24h: {fmt_usd(s['volume_24h'])}")

        caption = "\n".join(lines) if lines else say(lang, "Keine Daten.", "No data.")

        # 🐸 TBP-Logo anzeigen
        if TBP_LOGO_URL:
            tg_send_photo(
                chat_id,
                TBP_LOGO_URL,
                caption=caption,
                reply_to=msg_id
            )
        else:
            tg_buttons(
                chat_id,
                caption,
                [("Chart", LINKS["dexscreener"]), ("Sushi", LINKS["buy"])]
            )

        return jsonify({"ok": True})
    

    if low.startswith("/stats"):
        if is_cboost_chat:
            data = get_cboost_live_data()
            if not data:
                tg_send(
                    chat_id,
                    say(
                        lang,
                        "C-Boost-Statistiken konnten gerade nicht geladen werden. Bitte später nochmals versuchen.",
                        "Could not load C-Boost stats right now. Please try again later.",
                    ),
                    reply_to=msg_id
                )
                return jsonify({"ok": True})

            price = data.get("price")
            mc    = data.get("market_cap")
            vol   = data.get("volume_24h")
            lines = [
                "⚡ C-Boost Stats:",
                f"• Price: {fmt_usd(price, 10) if price is not None else 'N/A'}",
                f"• Market Cap: {fmt_usd(mc, 2) if mc is not None else 'N/A'}",
                f"• Vol 24h: {fmt_usd(vol, 2) if vol is not None else 'N/A'}",
            ]
            tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
            return jsonify({"ok": True})

        s = get_market_stats() or {}
        lines = [say(lang, "TBP-Stats:", "TBP Stats:")]
        if s.get("change_24h") not in (None, "", "null"):
            lines.append(f"• 24h: {s['change_24h']}%")
        if s.get("volume_24h") not in (None, "", "null"):
            lines.append(f"• Vol 24h: {fmt_usd(s['volume_24h'])}")
        if s.get("liquidity_usd") not in (None, "", "null"):
            lines.append(f"• Liq: {fmt_usd(s['liquidity_usd'])}")
        if s.get("market_cap") not in (None, "", "null"):
            lines.append(f"• MC: {fmt_usd(s['market_cap'])}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        if is_cboost_chat:
            data = get_cboost_live_data()
            chart = data.get("chart_url") if data else None
            if not chart:
                tg_send(
                    chat_id,
                    say(
                        lang,
                        "Der C-Boost-Chart ist aktuell nicht verfügbar. Bitte später erneut versuchen.",
                        "C-Boost chart is not available right now. Please try again later.",
                    ),
                    reply_to=msg_id
                )
            else:
                txt = say(
                    lang,
                    f"📊 C-Boost Live-Chart:\n{chart}",
                    f"📊 C-Boost live chart:\n{chart}",
                )
                tg_send(chat_id, txt, reply_to=msg_id)
            return jsonify({"ok": True})

        tg_buttons(
            chat_id,
            say(lang, "📊 Live-Chart:", "📊 Live chart:"),
            [("DexScreener", LINKS["dexscreener"]), ("DEXTools", LINKS["dextools"])]
        )
        return jsonify({"ok": True})

    # RAID Trigger nur auf "raid."
    if text.strip().lower() == "raid.":
        if is_cboost_chat:
            reply_txt = say(
                lang,
                "⚡ RAID! C-Boost Community bereit! 🚀",
                "⚡ RAID! C-Boost community ready! 🚀"
            )
        else:
            reply_txt = say(
                lang,
                "🚀 RAID! TBP army bereit! 🐸",
                "🚀 RAID! TBP army ready! 🐸"
            )
        tg_send(chat_id, reply_txt, reply_to=msg_id)
        return jsonify({"ok": True})

    # Autopost
    try:
        if MEM["chat_count"] >= 25 and not is_cboost_chat:
            tg_send(chat_id, autopost_text("en"))
            MEM["chat_count"] = 0
            MEM["last_autopost"] = datetime.utcnow()
    except Exception:
        pass

    # AI Security Filter
    if not low.startswith("/") and not is_admin(user_id):
        if is_illegal_offer(low):
            tg_delete_message(chat_id, msg_id)
            if is_cboost_chat:
                warn = say(
                    lang,
                    "⚠️ Illegale Angebote (Fake-Pässe, Drogen, Hacking-Services, gestohlene Daten usw.) sind in dieser C-Boost Gruppe strikt verboten. Deine Nachricht wurde entfernt.",
                    "⚠️ Illegal offers (fake IDs, drugs, hacking services, stolen data, etc.) are strictly forbidden in this C-Boost group. Your message has been removed."
                )
            else:
                warn = say(
                    lang,
                    "⚠️ Illegale Angebote (Fake-Pässe, Drogen, Hacking-Services, gestohlene Daten usw.) sind in diesem TBP-Chat strikt verboten. Deine Nachricht wurde entfernt.",
                    "⚠️ Illegal offers (fake IDs, drugs, hacking services, stolen data, etc.) are strictly forbidden in this TBP chat. Your message has been removed."
                )
            tg_send(chat_id, warn)
            return jsonify({"ok": True})

        if is_listing_scam(low):
            tg_delete_message(chat_id, msg_id)
            if is_cboost_chat:
                warn = say(
                    lang,
                    "⚠️ Angebote für bezahlte Listings / Fast-Track auf CMC sind in dieser C-Boost Gruppe nicht erlaubt. C-Boost setzt auf Transparenz und organisches Wachstum.",
                    "⚠️ Paid listing / fast-track offers for CMC are not allowed in this C-Boost group. C-Boost focuses on transparency and organic growth."
                )
            else:
                warn = say(
                    lang,
                    "⚠️ Angebote für bezahlte Listings / Fast-Track auf CMC sind in diesem Chat nicht erlaubt. TBP setzt auf Transparenz und organisches Wachstum.",
                    "⚠️ Paid listing / fast-track offers for CMC are not allowed in this chat. TBP focuses on transparency and organic growth."
                )
            tg_send(chat_id, warn)
            return jsonify({"ok": True})

        if is_external_promo(low):
            tg_delete_message(chat_id, msg_id)
            if is_cboost_chat:
                warn = say(
                    lang,
                    "⚠️ Externe Marketing- oder Promo-Angebote für andere Tokens / Projekte sind hier nicht erlaubt. Dieser Chat ist nur für C-Boost.",
                    "⚠️ External marketing or promo offers for other tokens / projects are not allowed here. This chat is only for C-Boost."
                )
            else:
                warn = say(
                    lang,
                    "⚠️ Externe Marketing- oder Promo-Angebote für andere Tokens / Projekte sind hier nicht erlaubt. Dieser Chat ist nur für TurboPepe-AI (TBP).",
                    "⚠️ External marketing or promo offers for other tokens / projects are not allowed here. This chat is only for TurboPepe-AI (TBP)."
                )
            tg_send(chat_id, warn)
            return jsonify({"ok": True})

    # Throttle nur auf freie Messages
    if not low.startswith("/"):
        if not should_reply(chat_id):
            return jsonify({"ok": True})

    mode = "cboost" if is_cboost_chat else "tbp"
    raw = call_openai(text, MEM["ctx"], mode=mode)
    if not raw:
        raw = say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")

    wants_links = re.search(r"\b(link|links|buy|kaufen|chart|scan)\b", low)
    if wants_links and mode == "tbp":
        tg_buttons(
            chat_id,
            clean_answer(raw),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
        )
    else:
        tg_send(chat_id, clean_answer(raw), reply_to=msg_id, preview=False)

    MEM["ctx"].append(f"You: {text}")
    MEM["ctx"].append(f"BOT: {raw}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"ok": True})

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
