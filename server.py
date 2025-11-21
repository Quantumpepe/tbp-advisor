# server.py — TBP-AI + C-BoostAI unified backend (Web + Telegram) — dual-token version
# -*- coding: utf-8 -*-

import os, re, json, time, threading, random
from datetime import datetime, timedelta
from collections import defaultdict
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

# C-Boost Gruppen-ID (wird später per /id ermittelt und als ENV CBOOST_CHAT_ID gesetzt)
CBOOST_CHAT_ID = int(os.environ.get("CBOOST_CHAT_ID", "0") or "0")

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi pair

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

# Nur diese Links sind im Chat erlaubt – alles andere gilt als Fremdwerbung
ALLOWED_LINK_PREFIXES = list(LINKS.values())

# TBP Supply für grobe MC-Schätzung (nur Info, nicht kritisch)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# Memory / State
MEM = {
    "ctx": [],
    "last_autopost": None,
    "chat_count": 0,
    "raid_on": False,
    "raid_msg": "Drop a fresh TBP meme! 🐸⚡",
    # Throttle:
    "resp_mode": "0",           # "0"=alles, "1"=jede 3., "2"=jede 10.
    "resp_counter": {}          # pro chat_id Zähler
}

# Raid-State pro Chat
RAID = {}  # chat_id -> {"active": bool, "await_link": bool, "tweet_url": str}

# Regexe
WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)
GER_DET    = re.compile(r"\b(der|die|das|und|nicht|warum|wie|kann|preis|kurs|listung|tokenomics)\b", re.I)
TWEET_RE   = re.compile(r"https?://(x\.com|twitter\.com)/\S+", re.I)
URL_RE     = re.compile(r"https?://\S+", re.I)

# Anti-Scam / Anti-Promo Patterns
SCAM_PATTERNS = [
    r"fast[-\s]?track.*cmc",
    r"fast[-\s]?track.*listing",
    r"i can get you listed",
    r"i can list your token",
    r"i work(ed)? with top projects",
    r"send.*fee",
    r"pay.*fee",
    r"listing.*fee",
    r"cmc agent",
    r"verify.*token",
    r"priority listing",
    r"guarantee.*cmc",
    r"marketing team",
    r"promotion service",
    r"signal group",
    r"pump group",
    r"bring investors",
    r"we can promote",
]

PERMA_MUTE = set()              # dauerhaft stumm geschaltete User
SCAM_COUNT = defaultdict(int)   # user_id -> Anzahl Verstöße

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

def is_admin(user_id) -> bool:
    try:
        return str(user_id) in ADMIN_USER_IDS if ADMIN_USER_IDS else True  # ohne ENV dürfen alle (zum Testen)
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


def is_scam_message(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    if "coinmarketcap" in t and ("fee" in t or "fast track" in t or "pay" in t):
        return True
    if "promotion" in t and "service" in t:
        return True
    if "investors" in t and "bring" in t:
        return True
    for p in SCAM_PATTERNS:
        if re.search(p, t):
            return True
    return False


def is_external_promo(text: str) -> bool:
    if not text:
        return False
    urls = URL_RE.findall(text)
    if not urls:
        return False
    for u in urls:
        allowed = False
        for pref in ALLOWED_LINK_PREFIXES:
            if u.startswith(pref):
                allowed = True
                break
        if not allowed:
            return True
    return False

# -------------------------
# Market Data (TBP)
# -------------------------

def get_live_price():
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
        }
    except Exception:
        return None

# -------------------------
# OpenAI (optional)
# -------------------------

def call_openai(question: str, context, mode: str = "tbp"):
    if not OPENAI_API_KEY:
        return None

    if mode == "cboost":
        system_msg = (
            "You are C-BoostAI, the official assistant of the C-Boost micro supply token on Polygon.\n"
            "Core facts:\n"
            "- C-Boost is a next-generation MICRO SUPPLY token on Polygon with a total supply of 5,000,000 tokens.\n"
            "- Fair launch mechanics, no complex taxes, transparent supply.\n"
            "- Focus on:\n"
            "  • small supply + strong community\n"
            "  • Boost Raids on X/Twitter and social engagement\n"
            "  • AI-assisted marketing and meme creation in the future\n"
            "- Long-term vision: build an ecosystem where C-Boost acts as the 'energy token' for raids, community quests, "
            "and future utilities like whitelists, rewards, and possible tools.\n"
            "\n"
            "Style & rules:\n"
            "- First, detect the user's language (German or English) and ALWAYS answer only in that language.\n"
            "- When users ask \"what is C-Boost\" or \"future\" or \"utility\", explain the above clearly and simply.\n"
            "- Make it clear that C-Boost is NOT financial advice, just an experimental community project.\n"
            "- Never mix C-Boost with TurboPepe or TBP. If someone asks about TBP, say you are only responsible for C-Boost "
            "and they should ask the TBP bot in the TBP group.\n"
            "- Keep answers short, friendly, and a bit hyped, but do not overpromise.\n"
        )
    else:
        system_msg = (
            "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
            "Detect user language. Answer ONLY in that language (DE or EN).\n"
            "Generic info (who are you / what is TBP / goal): short, friendly, factual. No links unless asked.\n"
            "If asked about NFTs or staking: say they are planned for the future.\n"
            "No financial advice. Keep it concise; light humor is ok.\n"
        )

    messages = [{"role": "system", "content": system_msg}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        messages.append({"role": role, "content": item.split(": ", 1)[1] if ": " in item else item})
    messages.append({"role": "user", "content": question})

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": OPENAI_MODEL, "messages": messages, "max_tokens": 320, "temperature": 0.4},
            timeout=40
        )
        if not r.ok:
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
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

    root = request.url_root.replace("http://", "https://")
    url = root.rstrip("/") + "/telegram"

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
        if p is not None: lines.append(say(lang, "💰 Preis", "💰 Price") + f": {fmt_usd(p, 12)}")
        if stats.get("change_24h") not in (None, "", "null"): lines.append(f"📈 24h: {stats['change_24h']}%")
        if stats.get("liquidity_usd") not in (None, "", "null"): lines.append("💧 " + say(lang,"Liquidität","Liquidity") + f": {fmt_usd(stats['liquidity_usd'])}")
        if stats.get("volume_24h") not in (None, "", "null"): lines.append(f"🔄 Vol 24h: {fmt_usd(stats['volume_24h'])}")
        ans = "\n".join(lines) if lines else say(lang, "Preis derzeit nicht verfügbar.", "Price currently unavailable.")
    else:
        raw = call_openai(q, MEM["ctx"], mode="tbp") or say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")
        ans = clean_answer(raw)

    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans})

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

@app.route("/telegram", methods=["GET","POST"])
def telegram_webhook():
    if request.method == "GET":
        return jsonify({"ok": True, "route": "telegram"}), 200

    update  = request.json or {}
    msg     = update.get("message", {}) or {}
    chat    = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    from_user = msg.get("from", {}) or {}
    user_id   = from_user.get("id")
    text    = (msg.get("text") or msg.get("caption") or "").strip()
    msg_id  = msg.get("message_id")

    # === Welcome Message bei neuen Membern ===
    if chat_id and "new_chat_members" in msg:
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
            welcome = (
                "👋 Welcome to the official <b>C-Boost</b> community!\n\n"
                "This chat is protected by an AI-based security system.\n"
                "• No paid listing / CMC offers\n"
                "• No promotion of other tokens / groups\n"
                "• Focus: C-Boost, raids & community 🚀\n\n"
                "Use <code>/rules</code> to see all security rules."
            )
        else:
            welcome = (
                "👋 Welcome to the official <b>TurboPepe-AI (TBP)</b> community!\n\n"
                "This chat is protected by an AI-based security system:\n"
                "• No paid CoinMarketCap / listing offers\n"
                "• No promotion of other tokens / projects / groups\n"
                "• Only official TBP links (website, Sushi, charts, scan, TG, X)\n\n"
                "Use <code>/rules</code> to see all security rules.\n"
                "Willkommen! /rules zeigt dir die Sicherheitsregeln auch auf Deutsch 🐸"
            )
        tg_send(chat_id, welcome, reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    # ==============================
    # ANTI-SCAM / ANTI-PROMO SHIELD
    # ==============================
    if chat_id and user_id and text:
        if not is_admin(user_id):
            if user_id in PERMA_MUTE:
                token = _choose_token_for_chat(chat_id)
                if token:
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{token}/deleteMessage",
                            json={"chat_id": chat_id, "message_id": msg_id},
                            timeout=10
                        )
                    except Exception:
                        pass
                return jsonify({"ok": True})

            scam_flag = is_scam_message(text)
            promo_flag = is_external_promo(text)

            if scam_flag or promo_flag:
                token = _choose_token_for_chat(chat_id)
                if token:
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{token}/deleteMessage",
                            json={"chat_id": chat_id, "message_id": msg_id},
                            timeout=10
                        )
                    except Exception:
                        pass

                SCAM_COUNT[user_id] += 1

                if SCAM_COUNT[user_id] == 1:
                    if scam_flag:
                        reason = (
                            "It matches known CoinMarketCap / listing scam patterns.\n"
                            "CoinMarketCap does NOT fast-track listings via Telegram or private agents."
                        )
                    else:
                        reason = (
                            "It contains external links to other tokens / projects.\n"
                            "This group is reserved for TurboPepe-AI (TBP) only."
                        )

                    warning = (
                        "⚠️ <b>TurboPepe-AI Security Notice</b>\n"
                        "A message was removed by the AI security filter.\n"
                        f"{reason}\n"
                        "Unsolicited promotion / listing offers are not allowed here.\n"
                        "This is your only warning."
                    )
                    tg_send(chat_id, warning, preview=False)
                    return jsonify({"ok": True})

                if SCAM_COUNT[user_id] >= 2 and token:
                    PERMA_MUTE.add(user_id)
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{token}/restrictChatMember",
                            json={
                                "chat_id": chat_id,
                                "user_id": user_id,
                                "permissions": {"can_send_messages": False}
                            },
                            timeout=10
                        )
                    except Exception:
                        pass

                    muted_msg = (
                        "🚫 <b>User permanently muted</b>\n"
                        "Repeated suspected listing / scam / external promo behavior detected.\n"
                        "TurboPepe-AI security has restricted this user."
                    )
                    tg_send(chat_id, muted_msg, preview=False)
                    return jsonify({"ok": True})

    if not chat_id:
        return jsonify({"ok": True})

    try:
        if MEM.get("_autopost_started") != True and (not CBOOST_CHAT_ID or chat_id != CBOOST_CHAT_ID):
            start_autopost_background(chat_id)
            MEM["_autopost_started"] = True
    except Exception:
        pass

    if "photo" in msg:
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
            caption = random.choice(MEME_CAPTIONS_CBOOST)
        else:
            caption = random.choice(MEME_CAPTIONS_TBP)
        tg_send(chat_id, caption, reply_to=msg_id)
        MEM["chat_count"] += 1
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    low  = text.lower()
    lang = "de" if is_de(text) else "en"
    MEM["chat_count"] += 1

    # ---- Admin-Response-Rate /0 /1 /2 /mode ----
    if low.startswith("/0") or low.startswith("/1") or low.startswith("/2") or low.startswith("/mode"):
        if low.startswith("/mode"):
            mode_label = {"0":"all", "1":"every 3rd", "2":"every 10th"}.get(MEM.get("resp_mode","0"), "all")
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

    # ----- /rules /security -----
    if low.startswith("/rules") or low.startswith("/security"):
        rules_text = say(
            lang,
            (
                "📜 <b>TBP-AI Security & Chat-Regeln</b>\n\n"
                "• Keine bezahlten CoinMarketCap- oder Listing-Angebote.\n"
                "• CoinMarketCap kontaktiert NIEMALS über Telegram für Fast-Track-Deals.\n"
                "• Keine Werbung für andere Token, Projekte oder fremde Gruppen.\n"
                "• Nur offizielle TBP-Links sind erlaubt (Website, Sushi, Charts, Scan, offizielles TG & X).\n"
                "• Wiederholte Verstöße werden automatisch stumm geschaltet.\n\n"
                "TBP-AI überwacht den Chat mit einem AI-Security-System, um Scam & Spam zu filtern und die Community zu schützen 🛡🐸"
            ),
            (
                "📜 <b>TBP-AI Security & Chat Rules</b>\n\n"
                "• No paid CoinMarketCap or listing offers.\n"
                "• CoinMarketCap will NEVER contact you on Telegram for fast-track deals.\n"
                "• No promotion of other tokens, projects or external groups.\n"
                "• Only official TBP links are allowed (website, Sushi, charts, scan, official TG & X).\n"
                "• Repeated violations will be automatically muted.\n\n"
                "TBP-AI runs an AI-based security system to filter scam & spam and protect the community 🛡🐸"
            )
        )
        tg_send(chat_id, rules_text, reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    # ----- Commands -----
    if low.startswith("/start"):
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
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
        tg_send(chat_id, "/price • /stats • /chart • /links • /rules • /raid start|stop|status • /id", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/id"):
        tg_send_any(chat_id, f"Chat ID: <code>{chat_id}</code>", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
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

    if low.startswith("/price") or WORD_PRICE.search(low):
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
            tg_send(
                chat_id,
                say(lang,
                    "Für C-Boost gibt es noch keinen Live-Preis – der Launch steht noch bevor. Fokus aktuell: Aufbau der Community und Boost-Raids.",
                    "There is no live price for C-Boost yet – launch is still upcoming. Focus for now: building the community and boost raids.",
                ),
                reply_to=msg_id
            )
            return jsonify({"ok": True})

        p = get_live_price()
        s = get_market_stats() or {}
        lines = []
        if p is not None: lines.append(say(lang, "💰 Preis", "💰 Price") + f": {fmt_usd(p, 12)}")
        if s.get("change_24h") not in (None,"","null"): lines.append(f"📈 24h: {s['change_24h']}%")
        if s.get("liquidity_usd") not in (None,"","null"): lines.append("💧 " + say(lang,"Liquidität","Liquidity") + f": {fmt_usd(s['liquidity_usd'])}")
        if s.get("volume_24h") not in (None,"","null"): lines.append(f"🔄 Vol 24h: {fmt_usd(s['volume_24h'])}")
        tg_buttons(chat_id, "\n".join(lines) if lines else say(lang,"Keine Daten.","No data."),
                   [("Chart", LINKS["dexscreener"]), ("Sushi", LINKS["buy"])])
        return jsonify({"ok": True})

    if low.startswith("/stats"):
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
            tg_send(
                chat_id,
                say(lang,
                    "C-Boost ist noch im Aufbau – offizielle On-Chain-Stats folgen ab Launch. Bis dahin steht die Community- und Raid-Power im Fokus.",
                    "C-Boost is still in preparation – official on-chain stats will follow after launch. Until then, focus is on community and raid power.",
                ),
                reply_to=msg_id
            )
            return jsonify({"ok": True})

        s = get_market_stats() or {}
        lines = [say(lang,"TBP-Stats:","TBP Stats:")]
        if s.get("change_24h") not in (None,"","null"): lines.append(f"• 24h: {s['change_24h']}%")
        if s.get("volume_24h") not in (None,"","null"): lines.append(f"• Vol 24h: {fmt_usd(s['volume_24h'])}")
        if s.get("liquidity_usd") not in (None,"","null"): lines.append(f"• Liq: {fmt_usd(s['liquidity_usd'])}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
            tg_send(
                chat_id,
                say(lang,
                    "Der C-Boost-Chart wird nach dem Launch verlinkt. Bis dahin: Memes, Raids und Community-Aufbau. ⚡",
                    "C-Boost chart will be linked after launch. Until then: memes, raids and community building. ⚡",
                ),
                reply_to=msg_id
            )
            return jsonify({"ok": True})

        tg_buttons(chat_id, say(lang,"📊 Live-Chart:","📊 Live chart:"), [("DexScreener", LINKS["dexscreener"]), ("DEXTools", LINKS["dextools"])])
        return jsonify({"ok": True})

    # ----- RAID FLOW -----
    if low.startswith("/raid"):
        parts = low.split()
        sub = parts[1] if len(parts) > 1 else ""

        if not is_admin(user_id):
            tg_send(chat_id, "⛔ Only admins can start/stop raids.", reply_to=msg_id)
            return jsonify({"ok": True})

        if sub == "start":
            RAID[chat_id] = {"active": False, "await_link": True, "tweet_url": ""}
            tg_send(chat_id, "🐸 RAID SETUP: Please send the **tweet link** (X/Twitter).", reply_to=msg_id)
            return jsonify({"ok": True})

        if sub == "stop":
            RAID.pop(chat_id, None)
            tg_send(chat_id, "🧯 Raid stopped. Thanks for the energy!", reply_to=msg_id)
            return jsonify({"ok": True})

        if sub == "status":
            st = RAID.get(chat_id)
            if not st:
                tg_send(chat_id, "ℹ️ No raid is configured.", reply_to=msg_id)
            else:
                tg_send(chat_id, f"🔎 Raid status:\n• active: {st['active']}\n• await_link: {st['await_link']}\n• tweet: {st['tweet_url'] or '-'}", reply_to=msg_id)
            return jsonify({"ok": True})

        tg_send(chat_id, "Usage: /raid start | /raid stop | /raid status", reply_to=msg_id)
        return jsonify({"ok": True})

    st = RAID.get(chat_id)
    if st and st.get("await_link"):
        m = TWEET_RE.search(text)
        if not m:
            tg_send(chat_id, "❗That doesn't look like a tweet link. Please send a valid X/Twitter URL.", reply_to=msg_id)
            return jsonify({"ok": True})

        url = m.group(0)
        st["tweet_url"] = url
        st["await_link"] = False
        st["active"] = True

        tg_buttons(
            chat_id,
            "🐸 RAID MODE ON!\nOpen the tweet, then **Like + Repost + Comment**.\nReply here with **done** or drop a screenshot. Let’s pump the vibes! 🚀",
            [("Open Tweet", url)]
        )

        def remind():
            time.sleep(300)
            if RAID.get(chat_id, {}).get("active"):
                tg_buttons(chat_id, "⚡ RAID REMINDER\nLike • Repost • Comment → then write **done** here.",
                           [("Open Tweet", url)])
        threading.Thread(target=remind, daemon=True).start()
        return jsonify({"ok": True})

    if st and st.get("active") and text.strip().lower() == "done":
        tg_send(chat_id, "✅ Logged! Thanks for boosting. Next frog up! 🐸⚡", reply_to=msg_id)
        return jsonify({"ok": True})

    try:
        if MEM["chat_count"] >= 25 and (not CBOOST_CHAT_ID or chat_id != CBOOST_CHAT_ID):
            tg_send(chat_id, autopost_text("en"))
            MEM["chat_count"] = 0
            MEM["last_autopost"] = datetime.utcnow()
    except Exception:
        pass

    if not low.startswith("/"):
        if not should_reply(chat_id):
            return jsonify({"ok": True})

    mode = "cboost" if (CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID) else "tbp"
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
