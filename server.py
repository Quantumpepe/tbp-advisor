# server.py — TBP-AI (Web + Telegram) — clean DE/EN, price/stats, buttons, scheduler
# -*- coding: utf-8 -*-

import os, re, json, time, threading, sqlite3
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ==============================
# Config / Constants
# ==============================

BOT_NAME        = "TBP-AI"
MODEL_NAME      = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_ID      = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()  # optional, for 10h posts

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"

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

# Supply for rough MC on web side (we do NOT push this in TG unless asked)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

WORD_PRICE  = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)
WORD_LINKS  = re.compile(r"\b(link|links|kaufen|buy|chart|scan)\b", re.I)

DB_PATH = os.environ.get("MEMORY_DB", "memory.db")

app = Flask(__name__)
CORS(app)

# ==============================
# SQLite mini memory
# ==============================

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT, sender TEXT, text TEXT, ts TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS facts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        k TEXT UNIQUE, v TEXT, source TEXT, ts TEXT
    )""")
    conn.commit()

def log_msg(chat_id, sender, text):
    try:
        conn = db()
        conn.execute(
            "INSERT INTO messages(chat_id,sender,text,ts) VALUES(?,?,?,?)",
            (str(chat_id), sender, text, datetime.utcnow().isoformat()+"Z")
        )
        conn.commit()
    except Exception:
        pass

def list_facts(limit=20):
    try:
        cur = db().execute("SELECT k,v FROM facts ORDER BY ts DESC LIMIT ?", (limit,))
        return cur.fetchall()
    except Exception:
        return []

# ==============================
# Price & market stats
# ==============================

def get_live_price():
    # 1) GeckoTerminal
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {})
        v = attrs.get("base_token_price_usd")
        price = float(v) if v not in (None, "null", "") else None
        if price and price > 0:
            return price
    except Exception:
        pass
    # 2) Dexscreener fallback
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        v = pair.get("priceUsd")
        price = float(v) if v not in (None, "null", "") else None
        if price and price > 0:
            return price
    except Exception:
        pass
    return None

def get_market_stats():
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        data = r.json()
        pair = data.get("pair") or (data.get("pairs") or [{}])[0]
        return {
            "change_24h": pair.get("priceChange24h"),
            "volume_24h": (pair.get("volume", {}) or {}).get("h24") or pair.get("volume24h"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
        }
    except Exception:
        return None

# ==============================
# Language & text helpers
# ==============================

def is_de(s: str) -> bool:
    s = (s or "").lower()
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum|kann|tokenomics|listung|kaufen|preis|kurs)\b", s))

def sys_prompt():
    facts = list_facts()
    facts_blk = ""
    if facts:
        facts_blk = "Known TBP facts:\n- " + "\n- ".join([f"{k}: {v}" for k,v in facts]) + "\n\n"
    return (
        facts_blk +
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Write in the user's language (German or English) — never mix.\n"
        "Tone: concise, friendly, a little witty, but factual. No long monologues.\n"
        "Do not include links unless the user explicitly asks for links (or uses /links, /buy, /chart, /scan).\n"
        "If asked about NFTs, say TBP Gold is currently offline.\n"
        "Never give financial advice or promises.\n"
    )

def call_openai(question, context):
    if not OPENAI_API_KEY:
        return None
    headers = {"Content-Type": "application/json","Authorization": f"Bearer {OPENAI_API_KEY}"}
    messages = [{"role":"system","content": sys_prompt()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        messages.append({"role": role, "content": item.split(": ",1)[1] if ": " in item else item})
    messages.append({"role":"user","content": question})
    payload = {"model": MODEL_NAME, "messages": messages, "max_tokens": 450, "temperature": 0.35}
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=40)
        if not r.ok: return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

def briefify(s: str) -> str:
    if not s: return s
    s = re.sub(r"\n{3,}", "\n\n", s.strip())
    return s[:1800]  # keep TG-friendly

# ==============================
# Link blocks (optional)
# ==============================

def build_links_block(lang: str, wants):
    """Return markdown with only requested links."""
    def md(label, url): return f"[{label}]({url})"
    L = {
        "website":  "Website" if lang=="en" else "Webseite",
        "buy":      "Buy on Sushi" if lang=="en" else "Auf Sushi kaufen",
        "scan":     "Polygonscan",
        "gecko":    "GeckoTerminal",
        "dextools": "DEXTools",
        "dexscr":   "DexScreener",
        "tg":       "Telegram",
        "x":        "X (Twitter)",
    }
    out = []
    if "website" in wants: out.append(md(f"🌐 {L['website']}", LINKS["website"]))
    if "buy"     in wants: out.append(md(f"💸 {L['buy']}", LINKS["buy"]))
    if "scan"    in wants: out.append(md(f"📜 {L['scan']}", LINKS["contract_scan"]))
    if "pool"    in wants:
        out += [md(L["gecko"], LINKS["gecko"]), md(L["dextools"], LINKS["dextools"]), md(L["dexscr"], LINKS["dexscreener"])]
    if "tg"      in wants: out.append(md(f"💬 {L['tg']}", LINKS["telegram"]))
    if "x"       in wants: out.append(md(f"🐦 {L['x']}", LINKS["x"]))
    return "" if not out else ("\n\n— Quick Links —\n" + "\n".join(out))

# ==============================
# AI wrapper (web)
# ==============================

MEM = {"ctx": []}

def ai_answer(user_q: str) -> str:
    # Do NOT auto-add links. Only price if asked.
    low = (user_q or "").lower()
    out = call_openai(user_q, MEM["ctx"]) or ("Bitte erneut versuchen." if is_de(low) else "Please try again.")
    out = briefify(out)

    if WORD_PRICE.search(low):
        p = get_live_price()
        s = get_market_stats() or {}
        lines = []
        if p is not None: lines.append(f"💰 Price: ${p:0.12f}")
        if s.get("change_24h") not in (None, "null", ""): lines.append(f"📈 24h: {s['change_24h']}%")
        if s.get("liquidity_usd") not in (None, "null", ""):
            try: lines.append(f"💧 Liquidity: ${int(float(s['liquidity_usd'])):,}")
            except: pass
        if s.get("volume_24h") not in (None, "null", ""):
            try: lines.append(f"🔄 Volume 24h: ${int(float(s['volume_24h'])):,}")
            except: pass
        if lines:
            out = "\n".join(lines) + "\n\n" + out
    return out

# ==============================
# Flask Web API
# ==============================

@app.route("/health")
def health(): return jsonify({"ok": True})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q: return jsonify({"answer":"empty question"})
    ans = ai_answer(q)
    MEM["ctx"].append(f"You: {q}"); MEM["ctx"].append(f"TBP: {ans}"); MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans})

# ==============================
# Telegram utils
# ==============================

def tg_send(chat_id, text, reply_to=None, buttons=None, preview=True):
    if not TELEGRAM_TOKEN: return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": (not preview)
    }
    if reply_to: payload["reply_to_message_id"] = reply_to
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=10)
    except Exception:
        pass

# ==============================
# Telegram webhook
# ==============================

# message counter + last explainer
LAST_EXPLAINER = {"ts": datetime.utcnow() - timedelta(hours=12)}
MSG_COUNT      = {"n": 0}

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}
    msg     = update.get("message") or {}
    chat    = msg.get("chat") or {}
    chat_id = chat.get("id")
    text    = msg.get("text") or ""
    msg_id  = msg.get("message_id")
    photos  = msg.get("photo")  # list when user sends an image

    if not chat_id: return jsonify({"ok": True})

    # log
    if text: log_msg(chat_id, "user", text)

    # Image messages → always English, short, varied
    if photos:
        choices = [
            "Nice photo! Want me to spin a meme from it? Type `/meme your prompt` (e.g., `/meme pepe laser eyes`).",
            "Fresh pic! Should I roast it gently or make it legendary? Try `/meme ...`",
            "Crispy image 👀 — I can turn it into a meme. Use `/meme your idea`.",
        ]
        tg_send(chat_id, choices[int(time.time()) % len(choices)], reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    low  = text.lower().strip()
    lang = "de" if is_de(low) else "en"

    # Commands
    if low.startswith("/start"):
        msg = ("Hi, ich bin TBP-AI. Frag mich alles zu TBP (DE/EN). "
               "Befehle: /price • /chart • /stats • /buy • /links")
        if lang=="en":
            msg = ("Hi, I'm TBP-AI. Ask me anything about TBP (EN/DE). "
                   "Commands: /price • /chart • /stats • /buy • /links")
        tg_send(chat_id, msg, reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id,
                "/price • /chart • /stats • /buy • /links",
                reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/buy"):
        label = "Buy on Sushi" if lang=="en" else "Auf Sushi kaufen"
        tg_send(chat_id,
                " " if lang=="en" else " ",
                reply_to=msg_id,
                buttons=[[{"text": f"💸 {label}", "url": LINKS["buy"]}]],
                preview=False)
        return jsonify({"ok": True})

    if low.startswith("/links") or WORD_LINKS.search(low):
        wants = ["website","buy","scan","pool","tg","x"]
        block = build_links_block("en" if lang=="en" else "de", wants)
        tg_send(chat_id, block or ("Links ready." if lang=="en" else "Links bereit."), reply_to=msg_id, preview=True)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        tg_send(chat_id, f"📊 {LINKS['dexscreener']}\nAlt: {LINKS['dextools']}", reply_to=msg_id, preview=True)
        return jsonify({"ok": True})

    if low.startswith("/price") or WORD_PRICE.search(low):
        p = get_live_price()
        s = get_market_stats() or {}
        lines = []
        if p is not None: lines.append(f"💰 Price: ${p:0.12f}")
        if s.get("change_24h") not in (None, "null", ""): lines.append(f"📈 24h: {s['change_24h']}%")
        if s.get("liquidity_usd") not in (None, "null", ""):
            try: lines.append(f"💧 Liquidity: ${int(float(s['liquidity_usd'])):,}"); except: pass
        if s.get("volume_24h") not in (None, "null", ""):
            try: lines.append(f"🔄 Volume 24h: ${int(float(s['volume_24h'])):,}"); except: pass
        if not lines: lines.append("Price currently unavailable." if lang=="en" else "Preis derzeit nicht verfügbar.")
        lines.append(f"📊 {LINKS['dexscreener']}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id, preview=True)
        return jsonify({"ok": True})

    if low.startswith("/stats"):
        s = get_market_stats() or {}
        lines = ["TBP stats:" if lang=="en" else "TBP-Statistik:"]
        if s.get("change_24h") not in (None,"null",""): lines.append(f"• 24h: {s['change_24h']}%")
        if s.get("volume_24h") not in (None,"null",""):
            try: lines.append(f"• Volume 24h: ${int(float(s['volume_24h'])):,}"); except: pass
        if s.get("liquidity_usd") not in (None,"null",""):
            try: lines.append(f"• Liquidity: ${int(float(s['liquidity_usd'])):,}"); except: pass
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    # Natural Q&A (no links unless asked)
    ans = call_openai(text, MEM["ctx"]) or ("Bitte erneut versuchen." if lang=="de" else "Please try again.")
    ans = briefify(ans)
    tg_send(chat_id, ans, reply_to=msg_id, preview=False)

    # context + counters
    MEM["ctx"].append(f"You: {text}"); MEM["ctx"].append(f"TBP: {ans}"); MEM["ctx"] = MEM["ctx"][-10:]
    MSG_COUNT["n"] += 1
    maybe_auto_explain(chat_id, lang)
    return jsonify({"ok": True})

# ==============================
# Scheduled explainers
# ==============================

EXPLAIN_EN = (
    "What is TBP (TurboPepe-AI)?\n"
    "• Meme token on Polygon (POL)\n"
    "• Burned LP, 0% tax, transparent token split\n"
    "• Live stats & answers via the bot\n"
    "• Goal: grow community + tooling (auto posts, stat bots, later X-bots)\n"
)

EXPLAIN_DE = (
    "Was ist TBP (TurboPepe-AI)?\n"
    "• Meme-Token auf Polygon (POL)\n"
    "• LP verbrannt, 0% Tax, transparente Verteilung\n"
    "• Live-Stats & Antworten über den Bot\n"
    "• Ziel: Community & Tools ausbauen (Auto-Posts, Stat-Bots, später X-Bots)\n"
)

def post_explainer(lang="en", target_chat_id=None):
    text = EXPLAIN_EN if lang=="en" else EXPLAIN_DE
    where = target_chat_id or CHANNEL_ID
    if not TELEGRAM_TOKEN or not where: return
    tg_send(where, text, preview=True)

def maybe_auto_explain(chat_id, lang):
    # after ~25 messages, but not more often than every 8h
    now = datetime.utcnow()
    if MSG_COUNT["n"] >= 25 and (now - LAST_EXPLAINER["ts"]) >= timedelta(hours=8):
        post_explainer(lang, chat_id)
        LAST_EXPLAINER["ts"] = now
        MSG_COUNT["n"] = 0

def scheduler_loop():
    # every 10h a channel explainer
    while True:
        try:
            if CHANNEL_ID:
                post_explainer("en")  # channel post in EN by default
                LAST_EXPLAINER["ts"] = datetime.utcnow()
        except Exception:
            pass
        time.sleep(10 * 60 * 60)  # 10h

# ==============================
# Main
# ==============================

if __name__ == "__main__":
    init_db()
    if CHANNEL_ID and TELEGRAM_TOKEN:
        threading.Thread(target=scheduler_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
