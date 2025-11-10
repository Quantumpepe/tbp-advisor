# server.py — TBP-AI unified backend (Web + Telegram) — v6
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

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi pair

# Optionales Logo für spätere Erweiterungen
LOGO_URL = "https://raw.githubusercontent.com/Quantumpepe/TurboPepe/main/turbopepe22.png"

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

# Memory (leichtgewichtig)
MEM = {
    "ctx": [],
    "last_autopost": None,
    "chat_count": 0,
    "raid_on": False,
    "raid_msg": "Drop a fresh TBP meme! 🐸⚡"
}

app = Flask(__name__)
CORS(app)

# =========================
# HELPERS
# =========================

WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart)\b", re.I)
GER_DET    = re.compile(r"\b(der|die|das|und|nicht|warum|wie|kann|preis|kurs|listung|tokenomics)\b", re.I)

def is_de(text: str) -> bool:
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

# -------------------------
# Market Data
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
        }
    except Exception:
        return None

# -------------------------
# OpenAI (optional)
# -------------------------

def call_openai(question: str, context):
    if not OPENAI_API_KEY:
        return None
    messages = [{"role": "system", "content": (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Detect user language. Answer ONLY in that language (DE or EN).\n"
        "When asked generic things (who are you, tell me about TBP, goal), keep it short and friendly.\n"
        "No financial advice. No promises. Keep links out unless explicitly asked.\n"
        "If asked about NFTs or staking: say they are planned for the future.\n"
        "Keep humor for smalltalk but stay concise.\n"
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

def clean_answer(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)(financial advice|finanzberatung)", "information", s)
    return s.strip()

# -------------------------
# Auto-Post / Raid scheduler
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
        say(lang,
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
    def loop():
        while True:
            try:
                if autopost_needed():
                    tg_send(chat_id, autopost_text("en"))
                    MEM["last_autopost"] = datetime.utcnow()
            except Exception:
                pass
            time.sleep(60)  # check minütlich
    t = threading.Thread(target=loop, daemon=True)
    t.start()

# =========================
# FLASK WEB (health/ask/admin)
# =========================

@app.route("/")
def root():
    return jsonify({"ok": True, "service": "tbp-advisor", "time": datetime.utcnow().isoformat()+"Z"})

@app.route("/health")
def health():
    return jsonify({"ok": True})

# Admin: Webhook setzen
@app.route("/admin/set_webhook")
def admin_set_webhook():
    key = request.args.get("key", "")
    if not ADMIN_SECRET or key != ADMIN_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 403
    if not TELEGRAM_TOKEN:
        return jsonify({"ok": False, "error": "bot token missing"}), 500

    url = request.url_root.rstrip("/") + "/telegram"
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": url}, timeout=10
        )
        j = r.json()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "response": j})

# Web-AI für deine Seite
@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200


    # Preis/Stats nur bei klarer Absicht
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
    """buttons = [(title, url), ...]"""
    kb = {"inline_keyboard": [[{"text": t, "url": u} for (t,u) in buttons]]}
    try:
        requests.post(
            tg_api("sendMessage"),
            json={"chat_id": chat_id, "text": text, "reply_markup": kb, "disable_web_page_preview": True},
            timeout=10
        )
    except Exception:
        pass

# Bild-Reply (englisch, variierend)
MEME_CAPTIONS = [
    "Nice photo! Want me to spin a meme from it? 🐸✨",
    "Fresh pixels detected. Should I add meme power? ⚡",
    "Clean drop. Caption it, or shall I? 😎",
]

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}
    msg = update.get("message", {}) or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")

    if not chat_id:  # nothing to do
        return jsonify({"ok": True})

    # Autopost-Thread starten (einmalig), nutze deine Gruppen-ID
    try:
        if MEM.get("_autopost_started") != True:
            start_autopost_background(chat_id)
            MEM["_autopost_started"] = True
    except Exception:
        pass

    # Bilder → nur Englische Caption (kostenlos)
    if "photo" in msg:
        tg_send(chat_id, random.choice(MEME_CAPTIONS), reply_to=msg_id)
        MEM["chat_count"] += 1
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    low = text.lower()
    lang = "de" if is_de(text) else "en"
    MEM["chat_count"] += 1

    # --- Commands ---
    if low.startswith("/start"):
        tg_buttons(
            chat_id,
            say(lang,
                f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP. 🚀",
                f"Hi, I'm {BOT_NAME}. Ask me anything about TBP. 🚀"),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
        )
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id, "/price • /stats • /chart • /links • /raid start|stop", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        tg_buttons(
            chat_id,
            say(lang, "Schnelle Links:", "Quick Links:"),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"]), ("Website", LINKS["website"])]
        )
        return jsonify({"ok": True})

    if low.startswith("/price") or WORD_PRICE.search(low):
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
        s = get_market_stats() or {}
        lines = [say(lang,"TBP-Stats:","TBP Stats:")]
        if s.get("change_24h") not in (None,"","null"): lines.append(f"• 24h: {s['change_24h']}%")
        if s.get("volume_24h") not in (None,"","null"): lines.append(f"• Vol 24h: {fmt_usd(s['volume_24h'])}")
        if s.get("liquidity_usd") not in (None,"","null"): lines.append(f"• Liq: {fmt_usd(s['liquidity_usd'])}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        tg_buttons(chat_id, say(lang,"📊 Live-Chart:","📊 Live chart:"), [("DexScreener", LINKS["dexscreener"]), ("DEXTools", LINKS["dextools"])])
        return jsonify({"ok": True})

    # Simple Raid Koordination (ohne X)
    if low.startswith("/raid"):
        if "start" in low:
            MEM["raid_on"] = True
            tg_send(chat_id, "🐸 RAID MODE: Post your best TBP memes & shill lines! Keep it fun, keep it clean. 🚀")
        elif "stop" in low:
            MEM["raid_on"] = False
            tg_send(chat_id, "🧯 Raid mode off. Thanks for the energy!")
        else:
            tg_send(chat_id, "Usage: /raid start | /raid stop")
        return jsonify({"ok": True})

    # Automatische Infomeldung: alle 10h oder nach 25 Chats
    try:
        if MEM["chat_count"] >= 25:
            tg_send(chat_id, autopost_text("en"))
            MEM["chat_count"] = 0
            MEM["last_autopost"] = datetime.utcnow()
    except Exception:
        pass

    # --- Normal AI Flow ---
    # Sachlich & kurz; keine Links pro Default; NFTs/Staking -> „kommt“
    raw = call_openai(text, MEM["ctx"])
    if not raw:
        raw = say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")

    # Falls Nutzer explizit Links will
    wants_links = re.search(r"\b(link|links|buy|kaufen|chart|scan)\b", low)
    if wants_links:
        tg_buttons(
            chat_id,
            clean_answer(raw),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
        )
    else:
        tg_send(chat_id, clean_answer(raw), reply_to=msg_id, preview=False)

    MEM["ctx"].append(f"You: {text}")
    MEM["ctx"].append(f"TBP: {raw}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"ok": True})

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
