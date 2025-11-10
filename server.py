# server.py — TBP-AI unified backend (Web + Telegram) — v5
# -*- coding: utf-8 -*-

import os, re, json, time, threading, random
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ================================
# CONFIG / LINKS / CONSTANTS
# ================================
BOT_NAME        = "TBP-AI"
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi pair

# Wenn du dein neues Bild im Repo hast, setze hier die RAW-URL oder hoste auf imgur.
LOGO_URL = "https://raw.githubusercontent.com/Quantumpepe/TurboPepe/main/turbopepe22.png"

LINKS = {
    "website":       "https://quantumpepe.github.io/TurboPepe/",
    "buy":           f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dextools":      f"https://www.dextools.io/app/en/polygon/pair-explorer/{TBP_PAIR}",
    "dexscreener":   f"https://dexscreener.com/polygon/{TBP_PAIR}",
    "gecko":         f"https://www.geckoterminal.com/en/polygon_pos/pools/{TBP_PAIR}?embed=1",
    "telegram":      "https://t.me/turbopepe25",
    "x":             "https://x.com/TurboPepe2025",
    "contract_scan": f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

# Supply (für grobe MC-Schätzung; AI nennt vorsichtig)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# Memory (kurz)
MEM = {"ctx": []}

# Auto-post Steuerung
LAST_AUTO_POST = datetime.utcnow() - timedelta(hours=12)
MSG_COUNTER    = 0
RAID_ACTIVE    = False
RAID_TOPIC     = ""
RAID_JOINERS   = set()

app = Flask(__name__)
CORS(app)

# ================================
# HELPERS
# ================================
WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)

def is_de(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum|kann|kaufen|preis|kurs|chart|tokenomics|listung)\b", t))

def fmt_usd(n: float) -> str:
    if n is None:
        return "N/A"
    try:
        if n < 0.01:
            s = f"{n:.12f}".rstrip("0").rstrip(".")
            return f"${s}"
        return "${:,.2f}".format(n)
    except Exception:
        return "N/A"

def sanitize_persona(ans: str) -> str:
    if not ans:
        return ""
    # Keine NFT-Versprechen
    if re.search(r"\bnft\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "NFTs will be explored in the future.", ans, flags=re.I)
    # Keine Finanzberatung
    ans = re.sub(r"(?i)(financial advice|finanzberatung)", "information", ans)
    return ans

# ================================
# PRICE & STATS
# ================================
def get_from_gecko():
    url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}"
    r = requests.get(url, timeout=6)
    r.raise_for_status()
    j = r.json()
    attrs = (j.get("data") or {}).get("attributes") or {}
    price = attrs.get("base_token_price_usd")
    return float(price) if price not in (None, "null", "") else None

def get_from_dexscreener():
    url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
    r = requests.get(url, timeout=6)
    r.raise_for_status()
    j = r.json()
    pair = j.get("pair") or (j.get("pairs") or [{}])[0]
    price = pair.get("priceUsd")
    out = {
        "price": float(price) if price not in (None, "null", "") else None,
        "change_24h": pair.get("priceChange24h"),
        "volume_24h": (pair.get("volume") or {}).get("h24") or pair.get("volume24h"),
        "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
    }
    return out

def get_live_price():
    # Primär Gecko, dann Dexscreener
    try:
        p = get_from_gecko()
        if p and p > 0:
            return p
    except Exception:
        pass
    try:
        ds = get_from_dexscreener()
        return ds.get("price")
    except Exception:
        return None

def get_market_stats():
    try:
        return get_from_dexscreener()
    except Exception:
        return None

# ================================
# OPENAI (optional)
# ================================
def system_prompt():
    return (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Keep answers short, clear, friendly. Use small humor and emojis, but stay informative.\n"
        "German if user speaks German; English otherwise. Do not mix languages in one answer.\n"
        "No financial advice, no promises. If asked about NFTs or staking: say it's planned for the future.\n"
        "Only add links when explicitly asked (e.g., buy, chart, links) or when answering /links.\n"
    )

def call_openai(question: str, context):
    if not OPENAI_API_KEY:
        return None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    msgs = [{"role": "system", "content": system_prompt()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        msgs.append({"role": role, "content": item.split(": ",1)[1] if ": " in item else item})
    msgs.append({"role": "user", "content": question})
    data = {"model": OPENAI_MODEL, "messages": msgs, "max_tokens": 400, "temperature": 0.5}
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data, timeout=40)
        if not r.ok:
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

def ai_answer(user_q: str) -> str:
    resp = call_openai(user_q, MEM["ctx"]) or ("Ich bin bereit. Frag mich zu TBP! 🐸" if is_de(user_q) else "Ready for TBP questions! 🐸")
    resp = sanitize_persona(resp)

    # Wenn klar nach Preis/Chart gefragt wird → prepend Live-Block
    if WORD_PRICE.search(user_q or ""):
        lang = "de" if is_de(user_q) else "en"
        p = get_live_price()
        s = get_market_stats() or {}
        lines = []
        if p is not None:
            lines.append(("💰 Preis" if lang=="de" else "💰 Price") + f": {fmt_usd(p)}")
        if s.get("liquidity_usd") not in (None, "null", ""):
            try:
                lines.append(("💧 Liquidität" if lang=="de" else "💧 Liquidity") + f": {fmt_usd(float(s['liquidity_usd']))}")
            except Exception:
                pass
        if s.get("volume_24h") not in (None, "null", ""):
            try:
                lines.append(("🔄 Volumen 24h" if lang=="de" else "🔄 Volume 24h") + f": {fmt_usd(float(s['volume_24h']))}")
            except Exception:
                pass
        if s.get("change_24h") not in (None, "null", ""):
            lines.append(("📈 24h" if lang=="de" else "📈 24h") + f": {s['change_24h']}%")
        if lines:
            resp = "\n".join(lines) + "\n\n" + resp
    return resp

# ================================
# TELEGRAM
# ================================
def tg_api(method, payload):
    if not TELEGRAM_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass

def tg_send(chat_id, text, reply_to=None, preview=True):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": not preview
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    tg_api("sendMessage", payload)

def tg_send_photo(chat_id, photo_url, caption=None):
    payload = {"chat_id": chat_id, "photo": photo_url}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "Markdown"
    tg_api("sendPhoto", payload)

def quick_links(lang="en", keys=None):
    keys = keys or []
    L = {
        "website":  "🌐 Website" if lang=="en" else "🌐 Webseite",
        "buy":      "💸 Buy on Sushi" if lang=="en" else "💸 Auf Sushi kaufen",
        "contract": "📜 Polygonscan",
        "pool":     "📊 Charts",
        "telegram": "💬 Telegram",
        "x":        "🐦 X (Twitter)"
    }
    out = []
    if "website" in keys:  out.append(f"[{L['website']}]({LINKS['website']})")
    if "buy" in keys:      out.append(f"[{L['buy']}]({LINKS['buy']})")
    if "contract" in keys: out.append(f"[{L['contract']}]({LINKS['contract_scan']})")
    if "pool" in keys:
        out += [f"[GeckoTerminal]({LINKS['gecko']})",
                f"[DEXTools]({LINKS['dextools']})",
                f"[DexScreener]({LINKS['dexscreener']})"]
    if "telegram" in keys: out.append(f"[{L['telegram']}]({LINKS['telegram']})")
    if "x" in keys:        out.append(f"[{L['x']}]({LINKS['x']})")
    return "\n".join(out)

ABOUT_EN = (
    "What is *TBP (TurboPepe-AI)*?\n"
    "• Meme token on Polygon (POL)\n"
    "• Burned LP, 0% tax, transparent token split\n"
    "• Live stats & answers via the bot\n"
    "• Goal: community + tooling (auto posts, stats, later X-bots)\n"
    "\nWhere is it going?\n"
    "• Listings (trackers), steady liquidity, active community\n"
    "• More automations (alerts, price prompts, mini-quizzes)\n"
    "• Collabs & memes — fun core, serious handling"
)
ABOUT_DE = (
    "Was ist *TBP (TurboPepe-AI)*?\n"
    "• Meme-Token auf Polygon (POL)\n"
    "• LP geburnt, 0% Tax, transparente Aufteilung\n"
    "• Live-Stats & Antworten über den Bot\n"
    "• Ziel: Community + Tools (Auto-Posts, Stats, später X-Bots)\n"
    "\nWohin geht's?\n"
    "• Listings (Tracker), stabile Liquidität, aktive Community\n"
    "• Mehr Automatisierung (Alerts, Preis-Prompts, Mini-Quiz)\n"
    "• Collabs & Memes — Spaß im Kern, seriöse Umsetzung"
)

PHOTO_REPLIES_EN = [
    "Nice photo! Want me to spin a meme from it? Try */meme pepe laser eyes*.",
    "Clean shot! Caption idea: *“Meme season never sleeps.”*",
    "I can riff on that! Send */meme neon cyber frog* and I’ll pitch lines.",
]
PHOTO_REPLIES_DE = [
    "Cooles Bild! Soll ich ein Meme daraus spinnen? Probier */meme pepe laser eyes*.",
    "Starkes Pic! Caption-Idee: *„Meme-Season schläft nie.“*",
    "Ich kann etwas draus machen! Sende */meme neon cyber frog* für Ideen.",
]

def handle_raid_command(low, chat_id):
    global RAID_ACTIVE, RAID_TOPIC, RAID_JOINERS
    if low.startswith("/raid start"):
        RAID_ACTIVE = True
        RAID_TOPIC = low.replace("/raid start", "", 1).strip() or "Community push"
        RAID_JOINERS = set()
        tg_send(chat_id, f"🟢 *Raid started:* {RAID_TOPIC}\nUse */raid join* to participate.\nUse */raid stop* to end.")
        return True
    if low.startswith("/raid join"):
        if not RAID_ACTIVE:
            tg_send(chat_id, "No active raid. Start with */raid start <topic>*.")
            return True
        # user name is not provided in webhook safely here; we just count
        RAID_JOINERS.add(str(time.time()))
        tg_send(chat_id, f"✅ Joined! Current raiders: {len(RAID_JOINERS)}")
        return True
    if low.startswith("/raid stop"):
        if not RAID_ACTIVE:
            tg_send(chat_id, "No active raid.")
            return True
        tg_send(chat_id, f"🔵 Raid finished: *{RAID_TOPIC}* — Participants: {len(RAID_JOINERS)}\nGood job, frogs! 🐸")
        RAID_ACTIVE, RAID_TOPIC, RAID_JOINERS = False, "", set()
        return True
    return False

# ================================
# WEB ENDPOINTS
# ================================
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200
    ans = ai_answer(q)
    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans})

# ================================
# TELEGRAM WEBHOOK
# ================================
@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    global LAST_AUTO_POST, MSG_COUNTER
    upd = request.json or {}
    msg = (upd.get("message") or {})
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    txt = (msg.get("text") or "").strip()
    low = txt.lower()
    mid = msg.get("message_id")

    if not chat_id:
        return jsonify({"ok": True})

    # Auto-explain: alle 10h oder nach 25 Nachrichten
    now = datetime.utcnow()
    MSG_COUNTER += 1
    if (now - LAST_AUTO_POST).total_seconds() > 36000 or MSG_COUNTER >= 25:
        LAST_AUTO_POST = now
        MSG_COUNTER = 0
        lang = "de" if is_de(txt) else "en"
        tg_send_photo(chat_id, LOGO_URL, caption=(ABOUT_DE if lang=="de" else ABOUT_EN))

    # Photos → kurze EN/DE Reaktion (EN bevorzugt, wie gewünscht)
    if "photo" in msg:
        reply = random.choice(PHOTO_REPLIES_EN)
        tg_send(chat_id, reply, reply_to=mid)
        return jsonify({"ok": True})

    # Commands
    if low.startswith("/start"):
        tg_send_photo(chat_id, LOGO_URL, caption="TBP-AI online. Ask me anything (DE/EN).")
        tg_send(chat_id, "Commands: /price • /chart • /stats • /buy • /links • /about • /raid start|join|stop", reply_to=mid)
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id, "Help: /price /chart /stats /buy /links /about • Raids: /raid start <topic>, /raid join, /raid stop", reply_to=mid)
        return jsonify({"ok": True})

    if handle_raid_command(low, chat_id):
        return jsonify({"ok": True})

    if low.startswith("/about"):
        tg_send(chat_id, ABOUT_DE if is_de(low) else ABOUT_EN, reply_to=mid)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        lang = "de" if is_de(low) else "en"
        block = quick_links(lang, ["website","buy","contract","pool","telegram","x"])
        tg_send(chat_id, "— Quick Links —\n" + block, reply_to=mid, preview=True)
        return jsonify({"ok": True})

    if low.startswith("/buy"):
        tg_send(chat_id, f"[Buy on Sushi]({LINKS['buy']})", reply_to=mid, preview=True)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        tg_send(chat_id, f"[DexScreener]({LINKS['dexscreener']})\n[DEXTools]({LINKS['dextools']})", reply_to=mid, preview=True)
        return jsonify({"ok": True})

    if low.startswith("/price") or WORD_PRICE.search(low):
        s = get_market_stats() or {}
        p = s.get("price") or get_live_price()
        lines = []
        lines.append(f"💰 Price: {fmt_usd(p) if p is not None else 'N/A'}")
        if s.get("liquidity_usd") not in (None, "null", ""):
            try:
                lines.append(f"💧 Liquidity: {fmt_usd(float(s['liquidity_usd']))}")
            except Exception:
                pass
        if s.get("volume_24h") not in (None, "null", ""):
            try:
                lines.append(f"🔄 Volume 24h: {fmt_usd(float(s['volume_24h']))}")
            except Exception:
                pass
        if s.get("change_24h") not in (None, "null", ""):
            lines.append(f"📈 24h: {s['change_24h']}%")
        tg_send(chat_id, "\n".join(lines) + f"\n\nCharts: {LINKS['dexscreener']}", reply_to=mid, preview=True)
        return jsonify({"ok": True})

    # Generische Frage → AI
    if txt:
        ans = ai_answer(txt)
        # Nur bei expliziter Nachfrage Links anhängen
        if re.search(r"\b(buy|kaufen)\b", low):
            ans += "\n\n" + quick_links("de" if is_de(low) else "en", ["buy"])
        if re.search(r"\b(chart|charts)\b", low):
            ans += "\n\n" + quick_links("de" if is_de(low) else "en", ["pool"])
        if re.search(r"\b(contract|adresse|address)\b", low):
            ans += "\n\n" + quick_links("de" if is_de(low) else "en", ["contract"])
        tg_send(chat_id, ans, reply_to=mid, preview=True)
        MEM["ctx"].append(f"You: {txt}")
        MEM["ctx"].append(f"TBP: {ans}")
        MEM["ctx"] = MEM["ctx"][-10:]
        return jsonify({"ok": True})

    return jsonify({"ok": True})

# ================================
# BACKGROUND: PERIODIC AUTO POST
# ================================
def periodic_poster():
    # Dummy: ohne Chat-ID wissen wir nicht wohin posten.
    # Wenn du eine feste Gruppen-ID hast, setze sie hier:
    CHAT_ID = os.environ.get("TPB_GROUP_ID", "").strip()
    if not CHAT_ID:
        return
    while True:
        try:
            tg_send_photo(CHAT_ID, LOGO_URL, caption=ABOUT_EN)
        except Exception:
            pass
        time.sleep(60 * 60 * 10)  # alle 10h

# ================================
# MAIN
# ================================
if __name__ == "__main__":
    # optional: Background-Poster starten, wenn Gruppen-ID gesetzt ist
    if os.environ.get("TPB_GROUP_ID"):
        threading.Thread(target=periodic_poster, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
