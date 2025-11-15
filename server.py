# server.py — TBP-AI + C-Boost-AI unified backend (Web + Telegram)
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
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_SECRET    = os.environ.get("ADMIN_SECRET", "").strip()
ADMIN_USER_IDS  = [x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()]

# Chat-IDs für unterschiedliche Persönlichkeiten
_cboost_chat_raw = os.environ.get("CBOOST_CHAT_ID", "").strip()
_tbp_chat_raw    = os.environ.get("TBP_CHAT_ID", "").strip()
CBOOST_CHAT_ID   = int(_cboost_chat_raw) if _cboost_chat_raw else None
TBP_CHAT_ID      = int(_tbp_chat_raw)    if _tbp_chat_raw else None

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

# Supply für grobe MC-Schätzung
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

# Vordefinierte Antworten für typische C-Boost Fragen
CBOOST_FAQ_RULES = [
    {
        "keywords": ["was ist cboost", "what is cboost", "c-boost", "cboost"],
        "answer_de": (
            "⚡ <b>Was ist C-Boost?</b>\n\n"
            "C-Boost ist ein Micro-Supply Token auf der Polygon-Chain mit nur 5.000.000 CBOOST im Umlauf.\n"
            "Fokus liegt auf einem fairen Launch, klaren Limits pro Kauf/Wallet und einer sauberen Liquidity ohne versteckte Team-Wallets.\n\n"
            "Es ist ein experimentelles Projekt – kein Finanzrat, immer selbst recherchieren (DYOR)."
        ),
        "answer_en": (
            "⚡ <b>What is C-Boost?</b>\n\n"
            "C-Boost is a micro-supply token on the Polygon chain with only 5,000,000 CBOOST in total.\n"
            "The focus is on a fair launch, clear max buy / max wallet limits and clean liquidity with no hidden team wallets.\n\n"
            "It’s an experimental project – not financial advice, always do your own research (DYOR)."
        )
    },
    {
        "keywords": ["micro supply", "micro-supply", "5m", "5 m", "wenig supply", "low supply"],
        "answer_de": (
            "📉 <b>Warum nur 5 Mio Supply?</b>\n\n"
            "Ein sehr kleiner Supply sorgt dafür, dass pro Token ein höherer Preis möglich ist – schon bei wenig Marketcap.\n"
            "Gleichzeitig bleiben die Käufe klein, weil Max-Buy und Max-Wallet begrenzen, wie viel eine einzelne Wallet halten kann.\n"
            "Damit soll verhindert werden, dass ein paar wenige früh alles dominieren."
        ),
        "answer_en": (
            "📉 <b>Why only 5M supply?</b>\n\n"
            "A very small supply allows the price per token to move faster even at a low market cap.\n"
            "At the same time, max buy and max wallet limits are used so one wallet cannot dominate the whole supply.\n"
            "The idea is to keep distribution fair instead of letting a few early wallets own everything."
        )
    },
    {
        "keywords": ["max wallet", "maxwallet", "max buy", "maxbuy", "limit", "limite"],
        "answer_de": (
            "🛡️ <b>Warum Max-Buy / Max-Wallet?</b>\n\n"
            "Die Limits sind da, um zu verhindern, dass eine einzelne Wallet einen riesigen Anteil der Supply einsammelt.\n"
            "Ziel ist, dass viele kleinere Holder entstehen und keine Whale-Wallet den Chart alleine kontrolliert.\n"
            "Später können die Limits angepasst oder aufgehoben werden, wenn der Markt stabiler ist."
        ),
        "answer_en": (
            "🛡️ <b>Why max buy / max wallet?</b>\n\n"
            "The limits are there to stop a single wallet from scooping up a huge portion of the supply.\n"
            "The goal is to create many smaller holders and avoid one whale controlling the chart.\n"
            "Later the limits can be adjusted or removed once the market is more stable."
        )
    },
    {
        "keywords": ["sicher", "risiko", "risk", "gefährlich", "scam"],
        "answer_de": (
            "⚠️ <b>Wie hoch ist das Risiko?</b>\n\n"
            "C-Boost ist ein experimenteller Microcap-Token – also High Risk.\n"
            "Es gibt keinen garantierten Erfolg, keine Rendite-Zusage und keinen Schutz vor Volatilität.\n"
            "Du solltest nur Geld riskieren, das du im schlimmsten Fall komplett verlieren kannst. Immer DYOR."
        ),
        "answer_en": (
            "⚠️ <b>How high is the risk?</b>\n\n"
            "C-Boost is an experimental microcap token – so it’s high risk.\n"
            "There is no guaranteed success, no promised returns and no protection from volatility.\n"
            "You should only risk money you can afford to lose completely. Always DYOR."
        )
    },
    {
        "keywords": ["fair launch", "fairlaunch", "presale", "private", "vorverkauf"],
        "answer_de": (
            "🚀 <b>Fair Launch & Verkauf</b>\n\n"
            "Die Idee hinter C-Boost ist ein möglichst fairer Start: kleine Limits pro Kauf/Wallet und Fokus auf on-chain Liquidity.\n"
            "Es gibt keine versteckte, große Team-Allocation, die später heimlich gedumpt wird.\n"
            "Alle Details zum Launch (Pool, Startzeit, Limits) werden transparent im offiziellen Channel angekündigt."
        ),
        "answer_en": (
            "🚀 <b>Fair launch & selling</b>\n\n"
            "The idea behind C-Boost is a fair start: small limits per buy/wallet and a focus on on-chain liquidity.\n"
            "There is no hidden, oversized team allocation that can be dumped secretly later.\n"
            "All launch details (pool, start time, limits) will be announced transparently in the official channel."
        )
    },
    {
        "keywords": ["anderen token", "andere token", "andere projekte", "deine anderen projekte", "zusammenhang"],
        "answer_de": (
            "🌐 <b>Hat C-Boost was mit anderen Projekten zu tun?</b>\n\n"
            "C-Boost ist ein eigenständiges Projekt. Auch wenn der gleiche Dev an mehreren Ideen arbeitet, "
            "wird C-Boost für sich betrachtet – mit eigener Community, eigener Supply und eigener Strategie.\n"
            "Hier im Chat geht es nur um C-Boost."
        ),
        "answer_en": (
            "🌐 <b>Is C-Boost connected to other tokens?</b>\n\n"
            "C-Boost is a standalone project. Even if the same dev experiments with several ideas, "
            "C-Boost is treated separately – with its own community, supply and strategy.\n"
            "In this chat we focus only on C-Boost."
        )
    },
]

# App
app = Flask(__name__)
CORS(app)

# =========================
# HELPERS
# =========================

def get_chat_mode(chat_id):
    """
    Bestimmt, in welchem Modus der Bot antworten soll:
    - 'cboost' : C-Boost-Gruppe (nie TBP erwähnen, kein TBP-Price/Stats)
    - 'tbp'    : TBP-Gruppe / Default
    """
    if CBOOST_CHAT_ID is not None and chat_id == CBOOST_CHAT_ID:
        return "cboost"
    if TBP_CHAT_ID is not None and chat_id == TBP_CHAT_ID:
        return "tbp"
    return "tbp"

def is_de(text):
    return bool(GER_DET.search((text or "").lower()))

def say(lang, de, en):
    return de if lang == "de" else en

def tg_api(path):
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{path}"

def fmt_usd(x, max_digits=2):
    try:
        return f"${float(x):,.{max_digits}f}"
    except Exception:
        return "N/A"

def is_admin(user_id):
    try:
        return str(user_id) in ADMIN_USER_IDS if ADMIN_USER_IDS else True
    except Exception:
        return False

def should_reply(chat_id):
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

def cboost_faq_answer(low, lang):
    for rule in CBOOST_FAQ_RULES:
        if any(k in low for k in rule["keywords"]):
            return rule["answer_de"] if lang == "de" else rule["answer_en"]
    return None

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
# OpenAI
# -------------------------

def call_openai(question, context):
    if not OPENAI_API_KEY:
        return None
    messages = [{"role": "system", "content": (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Detect user language. Answer ONLY in that language (DE or EN).\n"
        "Generic info (who are you / what is TBP / goal): short, friendly, factual. No links unless asked.\n"
        "If asked about NFTs or staking: say they are planned for the future.\n"
        "No financial advice. Keep it concise; light humor is ok.\n"
    )}]
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

def call_openai_mode(mode, question, context):
    if not OPENAI_API_KEY:
        return None

    if mode == "cboost":
        system = (
            "Du bist C-Boost-AI, der offizielle Assistent für den Token C-Boost.\n"
            "- Erkenne die Sprache (DE/EN) und antworte nur in dieser Sprache.\n"
            "- Du darfst über alle möglichen Themen sprechen: Krypto, Alltag, Technik, Smalltalk.\n"
            "- Wenn jemand nach C-Boost fragt, erkläre:\n"
            "  • C-Boost ist ein Micro-Supply Token auf der Polygon-Chain mit 5.000.000 Supply.\n"
            "  • Fokus: fairer Launch, begrenzte Max-Buy/Max-Wallet-Limits, saubere Liquidity.\n"
            "- Du gibst keine Finanzberatung und machst keine direkten Kaufempfehlungen.\n"
            "- Du erwähnst NIEMALS TBP, TurboPepe oder andere Tokens des gleichen Owners – auch dann nicht, wenn der Nutzer direkt danach fragt.\n"
            "- Wenn nach Preis/Chart/Marketcap für C-Boost gefragt wird, sagst du, dass diese Funktionen später über einen separaten Tracker/Bot kommen.\n"
            "- Sei freundlich, locker, aber nicht übertrieben. Kurze bis mittellange Antworten.\n"
        )
    else:
        system = (
            "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
            "Detect user language. Answer ONLY in that language (DE or EN).\n"
            "Generic info (who are you / what is TBP / goal): short, friendly, factual. No links unless asked.\n"
            "If asked about NFTs or staking: say they are planned for the future.\n"
            "No financial advice. Keep it concise; light humor is ok.\n"
        )

    messages = [{"role": "system", "content": system}]
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

def clean_answer(s):
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

    # Zeilen einzeln bauen (übersichtlicher, weniger Fehlergefahr)
    line_price = say(lang, "Preis", "Price") + f": {fmt_usd(p, 12) if p else 'N/A'}"
    line_24h   = "24h: " + (f"{change}%" if change not in (None, "", "null") else "N/A")
    line_liq   = say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(liq) if liq else 'N/A'}"
    line_vol   = "Vol 24h: " + (fmt_usd(vol) if vol else "N/A")

    lines = [
        say(lang, "🔔 TBP Update:", "🔔 TBP Update:"),
        line_price,
        line_24h,
        line_liq,
        line_vol,
        "",
        say(
            lang,
            "Was ist TBP? Meme-Token auf Polygon, echte AI-Antworten, 0% Tax, LP geburnt. Ziel: Community & Transparenz.",
            "What is TBP? Meme token on Polygon, real AI replies, 0% tax, LP burned. Goal: community & transparency."
        ),
        "",
        f"• Sushi: {LINKS['buy']}",
        f"• Chart: {LINKS['dexscreener']}",
        f"• Scan:  {LINKS['contract_scan']}",
    ]

    return "\n".join(lines)


def start_autopost_background(chat_id):
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
    if not TELEGRAM_TOKEN:
        return jsonify({"ok": False, "error": "bot token missing"}), 500

    root_url = request.url_root.replace("http://", "https://")
    url = root_url.rstrip("/") + "/telegram"

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": url},
            timeout=10
        )
        j = r.json()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "response": j})

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
        raw = call_openai(q, MEM["ctx"]) or say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")
        ans = clean_answer(raw)

    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans})

# =========================
# TELEGRAM
# =========================

def tg_send(chat_id, text, reply_to=None, preview=True):
    if not TELEGRAM_TOKEN: return
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(tg_api("sendMessage"), json=payload, timeout=10)
    except Exception:
        pass

def tg_buttons(chat_id, text, buttons):
    kb = {"inline_keyboard": [[{"text": t, "url": u} for (t,u) in buttons]]}
    try:
        requests.post(
            tg_api("sendMessage"),
            json={"chat_id": chat_id, "text": text, "reply_markup": kb, "disable_web_page_preview": True},
            timeout=10
        )
    except Exception:
        pass

MEME_CAPTIONS = [
    "Nice photo! Want me to spin a meme from it? 🐸✨",
    "Fresh pixels detected. Should I add meme power? ⚡",
    "Clean drop. Caption it, or shall I? 😎",
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
    text    = (msg.get("text") or "").strip()
    msg_id  = msg.get("message_id")

    if not chat_id:
        return jsonify({"ok": True})

    mode = get_chat_mode(chat_id)
    is_cboost = (mode == "cboost")

    # Autopost nur für TBP, nicht für C-Boost
    try:
        if not is_cboost and MEM.get("_autopost_started") != True:
            start_autopost_background(chat_id)
            MEM["_autopost_started"] = True
    except Exception:
        pass

    if "photo" in msg:
        tg_send(chat_id, random.choice(MEME_CAPTIONS), reply_to=msg_id)
        MEM["chat_count"] += 1
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    low  = text.lower()
    lang = "de" if is_de(text) else "en"
    MEM["chat_count"] += 1

    # Reply-Mode /0 /1 /2 /mode
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

    # Commands
    if low.startswith("/start"):
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Hi, ich bin der C-Boost AI Bot. Frag mich alles zu C-Boost oder allgemein zu Krypto. ⚡",
                    "Hi, I'm the C-Boost AI bot. Ask me anything about C-Boost or crypto in general. ⚡"
                ),
                reply_to=msg_id
            )
        else:
            tg_buttons(
                chat_id,
                say(lang,
                    f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP. 🚀",
                    f"Hi, I'm {BOT_NAME}. Ask me anything about TBP. 🚀"),
                [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
            )
        return jsonify({"ok": True})

    if low.startswith("/help"):
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Ich bin der C-Boost Bot. Schreib einfach deine Frage oder Nachricht – ich antworte wie ein normaler Chat-AI.\n"
                    "Preis- und Chartfunktionen für C-Boost werden später hinzugefügt.",
                    "I'm the C-Boost bot. Just write your question or message – I'll reply like a normal chat AI.\n"
                    "Price and chart features for C-Boost will be added later."
                ),
                reply_to=msg_id,
                preview=False
            )
        else:
            tg_send(chat_id, "/price • /stats • /chart • /links • /raid start|stop|status", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/cboost"):
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    (
                        "⚡ <b>C-Boost – Micro Supply Token</b>\n\n"
                        "• Chain: Polygon (PoS)\n"
                        "• Gesamtmenge: 5.000.000 CBOOST\n"
                        "• Fokus: fairer Launch, begrenzte Max-Buy/Max-Wallet-Limits\n"
                        "• Keine versteckten Team-Wallets, saubere Liquidity-Struktur\n\n"
                        "C-Boost ist ein experimenteller Microcap – kein Finanzrat, immer selbst recherchieren (DYOR)."
                    ),
                    (
                        "⚡ <b>C-Boost – Micro Supply Token</b>\n\n"
                        "• Chain: Polygon (PoS)\n"
                        "• Total supply: 5,000,000 CBOOST\n"
                        "• Focus: fair launch, limited max buy / max wallet\n"
                        "• No hidden team wallets, clean liquidity structure\n\n"
                        "C-Boost is an experimental microcap – not financial advice, always DYOR."
                    )
                ),
                reply_to=msg_id,
                preview=False
            )
        else:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Dieser Chat ist für TBP. Für Fragen zu C-Boost nutze bitte die C-Boost-Gruppe. 😊",
                    "This chat is for TBP. For C-Boost questions please use the C-Boost group. 😊"
                ),
                reply_to=msg_id,
                preview=False
            )
        return jsonify({"ok": True})

    if low.startswith("/links"):
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Links für C-Boost werden später ergänzt. Frag mich gerne, was C-Boost ist oder wie Micro-Supply Tokens funktionieren.",
                    "Links for C-Boost will be added later. Feel free to ask what C-Boost is or how micro supply tokens work."
                ),
                reply_to=msg_id,
                preview=False
            )
        else:
            tg_buttons(
                chat_id,
                say(lang, "Schnelle Links:", "Quick Links:"),
                [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"]), ("Website", LINKS["website"])]
            )
        return jsonify({"ok": True})

    if low.startswith("/price") or WORD_PRICE.search(low):
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Preis- und Chartabfragen für C-Boost werden später über einen separaten Tracker/Bot eingebunden. "
                    "Für jetzt kann ich dir nur allgemein etwas zu C-Boost und Risiko bei Microcaps erklären.",
                    "Price and chart queries for C-Boost will be added later via a separate tracker/bot. "
                    "For now I can only explain C-Boost in general and the risks of microcaps."
                ),
                reply_to=msg_id,
                preview=False
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
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Detaillierte Stats für C-Boost (MC, Liquidity usw.) kommen später dazu. Aktuell kann ich dir nur allgemeine Infos geben.",
                    "Detailed stats for C-Boost (MC, liquidity etc.) will be added later. Right now I can only give general info."
                ),
                reply_to=msg_id,
                preview=False
            )
        else:
            s = get_market_stats() or {}
            lines = [say(lang,"TBP-Stats:","TBP Stats:")]
            if s.get("change_24h") not in (None,"","null"): lines.append(f"• 24h: {s['change_24h']}%")
            if s.get("volume_24h") not in (None,"","null"): lines.append(f"• Vol 24h: {fmt_usd(s['volume_24h'])}")
            if s.get("liquidity_usd") not in (None,"","null"): lines.append(f"• Liq: {fmt_usd(s['liquidity_usd'])}")
            tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        if is_cboost:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Chartlinks für C-Boost werden später ergänzt, sobald Launch und Pool final stehen.",
                    "Chart links for C-Boost will be added later once launch and pool are finalized."
                ),
                reply_to=msg_id,
                preview=False
            )
        else:
            tg_buttons(chat_id, say(lang,"📊 Live-Chart:","📊 Live chart:"), [("DexScreener", LINKS["dexscreener"]), ("DEXTools", LINKS["dextools"])])
        return jsonify({"ok": True})

    # RAID FLOW für TBP + C-Boost
    if low.startswith("/raid"):
        parts = low.split()
        sub = parts[1] if len(parts) > 1 else ""

        if not is_admin(user_id):
            tg_send(chat_id, "⛔ Only admins can start/stop raids.", reply_to=msg_id)
            return jsonify({"ok": True})

        if sub == "start":
            RAID[chat_id] = {"active": False, "await_link": True, "tweet_url": ""}
            if is_cboost:
                tg_send(chat_id, "🐸 C-Boost RAID SETUP: Bitte den Tweet-Link (X/Twitter) senden.", reply_to=msg_id)
            else:
                tg_send(chat_id, "🐸 RAID SETUP: Please send the **tweet link** (X/Twitter).", reply_to=msg_id)
            return jsonify({"ok": True})

        if sub == "stop":
            RAID.pop(chat_id, None)
            if is_cboost:
                tg_send(chat_id, "🧯 C-Boost Raid gestoppt. Danke für den Support! ⚡", reply_to=msg_id)
            else:
                tg_send(chat_id, "🧯 Raid stopped. Thanks for the energy!", reply_to=msg_id)
            return jsonify({"ok": True})

        if sub == "status":
            st = RAID.get(chat_id)
            if not st:
                tg_send(chat_id, "ℹ️ No raid is configured.", reply_to=msg_id)
            else:
                tg_send(chat_id,
                        f"🔎 Raid status:\n• active: {st['active']}\n• await_link: {st['await_link']}\n• tweet: {st['tweet_url'] or '-'}",
                        reply_to=msg_id)
            return jsonify({"ok": True})

        tg_send(chat_id, "Usage: /raid start | /raid stop | /raid status", reply_to=msg_id)
        return jsonify({"ok": True})

    # Raid-Tweet-Link verarbeiten
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

        if is_cboost:
            tg_buttons(
                chat_id,
                "🐸 C-Boost RAID MODE ON!\nOpen the tweet, then **Like + Repost + Comment**.\nReply here with **done** or drop a screenshot. ⚡",
                [("Open Tweet", url)]
            )
        else:
            tg_buttons(
                chat_id,
                "🐸 RAID MODE ON!\nOpen the tweet, then **Like + Repost + Comment**.\nReply here with **done** or drop a screenshot. Let’s pump the vibes! 🚀",
                [("Open Tweet", url), ("Chart", LINKS["dexscreener"]), ("Sushi", LINKS["buy"])]
            )

        def remind():
            time.sleep(300)
            if RAID.get(chat_id, {}).get("active"):
                if is_cboost:
                    tg_buttons(
                        chat_id,
                        "⚡ C-Boost RAID REMINDER\nLike • Repost • Comment → dann hier **done** schreiben.",
                        [("Open Tweet", url)]
                    )
                else:
                    tg_buttons(
                        chat_id,
                        "⚡ RAID REMINDER\nLike • Repost • Comment → then write **done** here.",
                        [("Open Tweet", url)]
                    )
        threading.Thread(target=remind, daemon=True).start()
        return jsonify({"ok": True})

    # Teilnehmer melden "done"
    st = RAID.get(chat_id)
    if st and st.get("active") and text.strip().lower() == "done":
        tg_send(chat_id, "✅ Logged! Thanks for boosting. Next frog up! 🐸⚡", reply_to=msg_id)
        return jsonify({"ok": True})

    # Autopost nur TBP
    try:
        if not is_cboost and MEM["chat_count"] >= 25:
            tg_send(chat_id, autopost_text("en"))
            MEM["chat_count"] = 0
            MEM["last_autopost"] = datetime.utcnow()
    except Exception:
        pass

    # C-Boost FAQ vor KI
    if is_cboost:
        faq = cboost_faq_answer(low, lang)
        if faq:
            tg_send(chat_id, faq, reply_to=msg_id, preview=False)
            return jsonify({"ok": True})

    # Throttle
    if not low.startswith("/"):
        if not should_reply(chat_id):
            return jsonify({"ok": True})

    # AI Flow mit Modus
    raw = call_openai_mode(mode, text, MEM["ctx"])
    if not raw:
        raw = say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")

    wants_links = (not is_cboost) and re.search(r"\b(link|links|buy|kaufen|chart|scan)\b", low)
    if wants_links:
        tg_buttons(
            chat_id,
            clean_answer(raw),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
        )
    else:
        tg_send(chat_id, clean_answer(raw), reply_to=msg_id, preview=False)

    MEM["ctx"].append(f"You: {text}")
    speaker = "CBOOST" if is_cboost else "TBP"
    MEM["ctx"].append(f"{speaker}: {raw}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"ok": True})

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
