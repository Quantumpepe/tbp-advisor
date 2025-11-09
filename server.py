# server.py — TBP-AI V5 (Web + Telegram) with Humor, Auto-Posts, Live Stats, and Memory
# -*- coding: utf-8 -*-

import os, re, json, time, sqlite3, random, threading
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ================================================================
# CONFIG / CONSTANTS
# ================================================================
BOT_NAME        = "TBP-AI"
MODEL_NAME      = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH         = os.environ.get("MEMORY_DB", "memory.db")

# TBP (Polygon)
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"   # Sushi Pair (Polygon)

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

# Token supply (für einfache MC/FDV-Rechnung)
MAX_SUPPLY   = 190_000_000_000
BURNED       = 10_000_000_000
OWNER        = 14_000_000_000
CIRC_SUPPLY  = MAX_SUPPLY - BURNED - OWNER

# Auto-post Regeln
AUTOPOST_EVERY_HOURS   = 10
AUTOPOST_EVERY_MESSAGES= 25

# App
app = Flask(__name__)
CORS(app)

# ================================================================
# SQLite (Memory)
# ================================================================
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
    conn.execute("""CREATE TABLE IF NOT EXISTS state(
        k TEXT PRIMARY KEY, v TEXT
    )""")
    conn.commit()

def log_msg(chat_id, sender, text):
    try:
        conn = db()
        conn.execute("INSERT INTO messages(chat_id,sender,text,ts) VALUES(?,?,?,?)",
                     (str(chat_id), sender, text, datetime.utcnow().isoformat()+"Z"))
        conn.commit()
    except Exception:
        pass

def set_fact(k, v, source="telegram"):
    k = (k or "").strip().lower()
    v = (v or "").strip()
    if not k or not v: return False
    try:
        conn = db()
        conn.execute(
            "INSERT INTO facts(k,v,source,ts) VALUES(?,?,?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts, source=excluded.source",
            (k, v, source, datetime.utcnow().isoformat()+"Z")
        )
        conn.commit()
        return True
    except Exception:
        return False

def del_fact(k):
    try:
        conn = db()
        conn.execute("DELETE FROM facts WHERE k=?", ((k or "").strip().lower(),))
        conn.commit()
        return conn.total_changes > 0
    except Exception:
        return False

def list_facts(limit=20):
    try:
        cur = db().execute("SELECT k,v FROM facts ORDER BY ts DESC LIMIT ?", (limit,))
        return cur.fetchall()
    except Exception:
        return []

def get_state(key, default=None):
    try:
        cur = db().execute("SELECT v FROM state WHERE k=?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        return default

def set_state(key, value):
    try:
        conn = db()
        conn.execute(
            "INSERT INTO state(k,v) VALUES(?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(value))
        )
        conn.commit()
    except Exception:
        pass

# ================================================================
# Helpers: Language, Sanitizing, Buttons
# ================================================================
WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)
GER_DET    = re.compile(r"\b(der|die|das|wie|was|warum|kann|preis|kurs|tokenomics|listung|hilfe|kaufen)\b", re.I)

def is_de(text: str) -> bool:
    return bool(GER_DET.search((text or "").lower()))

def sanitize_persona(ans: str) -> str:
    if not ans: return ""
    if re.search(r"\bNFT\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "", ans, flags=re.I).strip()
    ans = re.sub(r"(?i)(financial advice|finanzberatung)", "information", ans)
    return ans

def inline_buttons(lang: str):
    lab = {
        "buy":  "Sushi",
        "chart":"Chart",
        "scan": "Scan",
        "site": "Site",
    }
    kb = {
        "inline_keyboard": [[
            {"text": lab["buy"],  "url": LINKS["buy"]},
            {"text": lab["chart"],"url": LINKS["dexscreener"]},
            {"text": lab["scan"], "url": LINKS["contract_scan"]},
            {"text": lab["site"], "url": LINKS["website"]},
        ]]
    }
    return kb

# ================================================================
# Live Price + Market Stats
# ================================================================
def get_live_price():
    # GeckoTerminal
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}"
        r = requests.get(url, timeout=6); r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {})
        v = attrs.get("base_token_price_usd")
        p = float(v) if v not in (None,"","null") else None
        if p and p > 0: return p
    except Exception:
        pass
    # Dexscreener
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=6); r.raise_for_status()
        d = r.json()
        pair = d.get("pair") or (d.get("pairs") or [{}])[0]
        v = pair.get("priceUsd")
        p = float(v) if v not in (None,"","null") else None
        if p and p > 0: return p
    except Exception:
        pass
    return None

def get_market_stats():
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=7); r.raise_for_status()
        d = r.json()
        pair = d.get("pair") or (d.get("pairs") or [{}])[0]
        return {
            "change_24h": pair.get("priceChange24h"),
            "volume_24h": (pair.get("volume", {}) or {}).get("h24") or pair.get("volume24h"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
        }
    except Exception:
        return None

# ================================================================
# OpenAI
# ================================================================
def build_system():
    facts = list_facts(20)
    facts_block = ""
    if facts:
        pairs = [f"{k}: {v}" for (k,v) in facts]
        facts_block = "Known TBP facts (user-taught):\n- " + "\n- ".join(pairs) + "\n\n"
    return (
        facts_block +
        "You are TBP-AI, the meme-savvy assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Always answer in the user's language (German or English). Do NOT mix languages.\n"
        "Tone: witty, friendly, concise. When asked for facts (price/stats/tokenomics/security), be objective and short.\n"
        "No financial advice. No promises. No NFT pitches. If NFTs are requested, say info is offline.\n"
        "Prefer bullet points. Keep messages compact for Telegram.\n"
    )

def call_openai(question, context):
    if not OPENAI_API_KEY: return None
    headers = {"Content-Type":"application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    msgs = [{"role":"system","content": build_system()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        msgs.append({"role": role, "content": item.split(": ",1)[1] if ": " in item else item})
    msgs.append({"role":"user","content": question})
    data = {"model": MODEL_NAME, "messages": msgs, "max_tokens": 500, "temperature": 0.4}
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data, timeout=40)
        if not r.ok: return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

# ================================================================
# Link builder (nur bei Absicht)
# ================================================================
def build_links(lang: str, needs):
    T = {
        "website": "🌐 Website" if lang=="en" else "🌐 Webseite",
        "buy":     "💸 Buy on Sushi" if lang=="en" else "💸 Auf Sushi kaufen",
        "contract":"📜 Polygonscan",
        "pool":    "📊 Charts",
        "telegram":"💬 Telegram",
        "x":       "🐦 X (Twitter)",
    }
    out = []
    if "website" in needs:  out.append(f"[{T['website']}]({LINKS['website']})")
    if "buy" in needs:      out.append(f"[{T['buy']}]({LINKS['buy']})")
    if "contract" in needs: out.append(f"[{T['contract']}]({LINKS['contract_scan']})")
    if "pool" in needs:
        out += [
            f"[GeckoTerminal]({LINKS['gecko']})",
            f"[DEXTools]({LINKS['dextools']})",
            f"[DexScreener]({LINKS['dexscreener']})",
        ]
    if "telegram" in needs: out.append(f"[{T['telegram']}]({LINKS['telegram']})")
    if "x" in needs:        out.append(f"[{T['x']}]({LINKS['x']})")
    return "" if not out else ("\n\n— Quick Links —\n" + "\n".join(out))

def linkify(user_q: str, ans: str) -> str:
    low = (user_q or "").lower()
    lang = "de" if is_de(low) else "en"

    # Preis/Stats nur bei Absicht
    if WORD_PRICE.search(low):
        p = get_live_price()
        st= get_market_stats() or {}
        lines = []
        if p is not None: lines.append(("Aktueller Preis" if lang=="de" else "Price") + f": ${p:0.12f}")
        if st.get("change_24h") not in (None,"","null"): lines.append(("24h Änderung" if lang=="de" else "24h Change") + f": {st['change_24h']}%")
        if st.get("liquidity_usd") not in (None,"","null"):
            try: lines.append(("Liquidität" if lang=="de" else "Liquidity") + f": ${int(float(st['liquidity_usd'])):,}")
            except: pass
        if st.get("volume_24h") not in (None,"","null"):
            try: lines.append(("Volumen 24h" if lang=="de" else "Volume 24h") + f": ${int(float(st['volume_24h'])):,}")
            except: pass
        if lines: ans = "\n".join(lines) + "\n\n" + ans

    needs = []
    if re.search(r"(what is|was ist|tokenomics|buy|kaufen|chart|preis|price|kurs)", low, re.I):
        needs += ["website","buy","contract","pool","telegram","x"]
    if needs:
        ans += "\n" + build_links(lang, needs)
    return ans

# ================================================================
# AI answer
# ================================================================
def ai_answer(user_q: str) -> str:
    resp = call_openai(user_q, get_state("ctx", []))
    if not resp:
        resp = "Network glitch. Try again 🐸" if not is_de(user_q) else "Netzwerk-Glitch. Bitte nochmal! 🐸"
    resp = sanitize_persona(resp)
    resp = linkify(user_q, resp)
    return resp

# ================================================================
# Telegram utils
# ================================================================
def tg_send(chat_id, text, reply_to=None, buttons=None, disable_preview=False):
    if not TELEGRAM_TOKEN: return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": disable_preview
    }
    if reply_to: payload["reply_to_message_id"] = reply_to
    if buttons:  payload["reply_markup"] = buttons
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception:
        pass

# Image reaction pool
IMAGE_RESPONSES = [
   "🔥 TBP vibes at maximum."
   "Bro... who gave you permission to drop such a meme-level flick? 😂",
   "That's iconic. Want laser eyes? 😎🔫",
   "Ufff... this belongs in the TBP Museum. 🖼️🐸",
   "Legendary drop! My AI is giggling. 🤖😆",
   "Damn! Pepe in turbo mode! 🚀🐸",
   "Sick picture — meme power! ⚡",
   "This slaps. Absolute art. 🎨🐸",
   "Stable AF. Keep it up. 😎",
]

# Autopost text
def autopost_text(lang="en"):
    if lang=="de":
        return (
            "🐸 **Was ist TBP (TurboPepe-AI)?**\n"
            "• Meme-Token auf Polygon (POL)\n"
            "• 0% Tax, transparente Token-Verteilung\n"
            "• LP burned/locked (on-chain Beweis)\n"
            "• Live-Stats & Antworten via Bot\n"
            "• Ziel: aktive Community + AI-Tooling (Auto-Posts, Preis-Prompts, Mini-Quizzes)\n\n"
            "Charts: " + LINKS["dexscreener"]
        )
    return (
        "🐸 **What is TBP (TurboPepe-AI)?**\n"
        "• Meme token on Polygon (POL)\n"
        "• 0% tax, transparent token split\n"
        "• LP burned/locked (on-chain proof)\n"
        "• Live stats & answers via this bot\n"
        "• Goal: active community + AI tooling (auto posts, price prompts, mini quizzes)\n\n"
        "Charts: " + LINKS["dexscreener"]
    )

# Message counters for autopost
def inc_message_count():
    st = get_state("meta", {"count":0,"last_post": datetime.utcnow().isoformat()+"Z"})
    st["count"] = st.get("count",0) + 1
    set_state("meta", st)
    return st["count"]

def should_autopost():
    st = get_state("meta", {"count":0,"last_post": datetime.utcnow().isoformat()+"Z"})
    last_iso = st.get("last_post")
    try:
        last = datetime.fromisoformat(last_iso.replace("Z",""))
    except Exception:
        last = datetime.utcnow() - timedelta(hours=AUTOPOST_EVERY_HOURS+1)
    due_time = datetime.utcnow() - last >= timedelta(hours=AUTOPOST_EVERY_HOURS)
    due_msgs = st.get("count",0) >= AUTOPOST_EVERY_MESSAGES
    return due_time or due_msgs

def mark_autopost_done():
    set_state("meta", {"count":0, "last_post": datetime.utcnow().isoformat()+"Z"})

# ================================================================
# Flask endpoints
# ================================================================
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q: return jsonify({"answer":"empty question"}), 200
    ans = ai_answer(q)

    ctx = get_state("ctx", [])
    ctx += [f"You: {q}", f"TBP: {ans}"]
    set_state("ctx", ctx[-10:])
    return jsonify({"answer": ans})

# ================================================================
# Telegram webhook
# ================================================================
TEACH_RE  = re.compile(r"^/teach\s+(.+?)\s*=\s*(.+)$", re.I)
FORGET_RE = re.compile(r"^/forget\s+(.+)$", re.I)

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    upd = request.json or {}
    msg = upd.get("message", {}) or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    text   = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")

    if not chat_id:
        return jsonify({"ok": True})

    # Image / Sticker reactions
    if "photo" in msg or "sticker" in msg:
        resp = random.choice(IMAGE_RESPONSES)
        tg_send(chat_id, resp, reply_to=msg_id)
        inc_message_count()
        if should_autopost():
            tg_send(chat_id, autopost_text("de"), buttons=inline_buttons("de"))
            mark_autopost_done()
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    low = text.lower()
    lang= "de" if is_de(low) else "en"

    # Log
    log_msg(chat_id, "user", text)

    # /start
    if low.startswith("/start"):
        hello = ("Hi, ich bin TBP-AI. Frag mich alles zu TBP (DE/EN). "
                 "Tipp: /price • /chart • /stats • /links • /mem • /teach key = value • /forget key"
                 ) if lang=="de" else (
                 "Hi, I’m TBP-AI. Ask me anything about TBP (EN/DE). "
                 "Try: /price • /chart • /stats • /links • /mem • /teach key = value • /forget key")
        tg_send(chat_id, hello, reply_to=msg_id, buttons=inline_buttons(lang))
        return jsonify({"ok": True})

    # /help
    if low.startswith("/help"):
        helptext = ("/price • /chart • /stats • /links • /mem • /teach key = value • /forget key") if lang=="de" \
            else ("/price • /chart • /stats • /links • /mem • /teach key = value • /forget key")
        tg_send(chat_id, helptext, reply_to=msg_id, buttons=inline_buttons(lang))
        return jsonify({"ok": True})

    # /links
    if low.startswith("/links"):
        block = build_links(lang, ["website","buy","contract","pool","telegram","x"])
        tg_send(chat_id, block or ("Links bereit." if lang=="de" else "Links ready."),
                reply_to=msg_id, buttons=inline_buttons(lang), disable_preview=True)
        return jsonify({"ok": True})

    # memory ops
    m = TEACH_RE.match(text)
    if m:
        ok = set_fact(m.group(1), m.group(2), source="telegram")
        tg_send(chat_id, ("✅ Gespeichert: " if ok else "❌ Konnte nicht speichern: ") + f"{m.group(1)} = {m.group(2)}",
                reply_to=msg_id)
        return jsonify({"ok": True})

    m = FORGET_RE.match(text)
    if m:
        ok = del_fact(m.group(1))
        tg_send(chat_id, ("🧹 Gelöscht: " if ok else "❌ Nicht gefunden: ") + m.group(1), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/mem"):
        rows = list_facts()
        if not rows:
            tg_send(chat_id, "🗒️ Noch keine Fakten gespeichert. Beispiel: /teach goal = 800M MC in 3y"
                              if lang=="de" else
                              "🗒️ No facts stored yet. Example: /teach goal = 800M MC in 3y",
                    reply_to=msg_id)
        else:
            lines = ["🧠 TBP Memory:"]
            for k,v in rows: lines.append(f"• {k}: {v}")
            tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    # price / stats intent or /price
    if low.startswith("/price") or WORD_PRICE.search(low):
        p  = get_live_price()
        st = get_market_stats() or {}
        lines = []
        if p is not None: lines.append(("💰 Preis" if lang=="de" else "💰 Price") + f": ${p:0.12f}")
        if st.get("liquidity_usd") not in (None,"","null"):
            try: lines.append(("💧 Liquidität" if lang=="de" else "💧 Liquidity") + f": ${int(float(st['liquidity_usd'])):,}")
            except: pass
        if st.get("volume_24h") not in (None,"","null"):
            try: lines.append(("🔄 Volumen 24h" if lang=="de" else "🔄 Volume 24h") + f": ${int(float(st['volume_24h'])):,}")
            except: pass
        if st.get("change_24h") not in (None,"","null"):
            lines.append(("📈 24h Änderung" if lang=="de" else "📈 24h Change") + f": {st['change_24h']}%")
        if not lines:
            lines = [ "Preis derzeit nicht verfügbar." if lang=="de" else "Price currently unavailable." ]
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id, buttons=inline_buttons(lang))
        inc_message_count()
        if should_autopost():
            tg_send(chat_id, autopost_text(lang), buttons=inline_buttons(lang))
            mark_autopost_done()
        return jsonify({"ok": True})

    # /chart
    if low.startswith("/chart"):
        tg_send(chat_id,
                ("📊 Live Chart: " if lang=="de" else "📊 Live chart: ") + LINKS["dexscreener"] +
                ("\nAlt: " if lang=="de" else "\nAlt: ") + LINKS["dextools"],
                reply_to=msg_id, buttons=inline_buttons(lang))
        return jsonify({"ok": True})

    # /stats
    if low.startswith("/stats"):
        st = get_market_stats() or {}
        lines = ["TBP Stats:"]
        if st.get("change_24h") not in (None,"","null"):  lines.append(f"• 24h: {st['change_24h']}%")
        if st.get("volume_24h") not in (None,"","null"):
            try: lines.append(f"• Vol 24h: ${int(float(st['volume_24h'])):,}")
            except: pass
        if st.get("liquidity_usd") not in (None,"","null"):
            try: lines.append(f"• Liq: ${int(float(st['liquidity_usd'])):,}")
            except: pass
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id, buttons=inline_buttons(lang))
        return jsonify({"ok": True})

    # Normal AI flow (ohne Link-Spam)
    ans = ai_answer(text)
    tg_send(chat_id, ans, reply_to=msg_id)

    # Update context + autopost
    ctx = get_state("ctx", [])
    ctx += [f"You: {text}", f"TBP: {ans}"]
    set_state("ctx", ctx[-10:])
    inc_message_count()
    if should_autopost():
        tg_send(chat_id, autopost_text(lang), buttons=inline_buttons(lang))
        mark_autopost_done()

    return jsonify({"ok": True})

# ================================================================
# Main
# ================================================================
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
