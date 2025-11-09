# server.py  — TBP-AI unified backend (Web + Telegram) with Memory
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import sqlite3
from datetime import datetime
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ================================================================
# == CONFIG / LINKS / CONSTANTS ==
# ================================================================

BOT_NAME       = "TBP-AI"
MODEL_NAME     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

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

# Supply (für einfache MC-Schätzung – AI erwähnt vorsichtig)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# Gesprächskontext (kurz)
MEM = {"ctx": []}

# DB (persistentes Mini-Memory)
DB_PATH = os.environ.get("MEMORY_DB", "memory.db")

app = Flask(__name__)
CORS(app)

# ================================================================
# == SQLITE MEMORY ==
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
    if not k or not v:
        return False
    try:
        conn = db()
        conn.execute("INSERT INTO facts(k,v,source,ts) VALUES(?,?,?,?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts, source=excluded.source",
                     (k, v, source, datetime.utcnow().isoformat()+"Z"))
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

def list_facts(limit=10):
    try:
        conn = db()
        cur = conn.execute("SELECT k,v FROM facts ORDER BY ts DESC LIMIT ?", (limit,))
        return cur.fetchall()
    except Exception:
        return []

# ================================================================
# == PRICE & MARKET STATS ==
# ================================================================

def get_live_price():
    """Primär GeckoTerminal, Fallback Dexscreener. Rückgabe float|None (USD)."""
    # 1) GeckoTerminal
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}"
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {})
        v = attrs.get("base_token_price_usd")
        price = float(v) if v not in (None, "null", "") else None
        if price and price > 0:
            return price
    except Exception:
        pass
    # 2) Dexscreener
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=5)
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
    """Dexscreener: 24h Change, 24h Vol, Liquidity USD. Rückgabe dict|None."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=5)
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


# ================================================================
# == UTILITIES ==
# ================================================================

WORD_RE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)

def is_de(text: str) -> bool:
    text = (text or "").lower()
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum|kann|tokenomics|listung)\b", text))

def sanitize_persona(ans: str) -> str:
    if not ans:
        return ""
    # NFT-Erwähnungen kappen
    if re.search(r"\bNFT\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "", ans, flags=re.I).strip()
    # Keine Finanzberatung
    ans = re.sub(r"(?i)(financial advice|finanzberatung)", "information", ans)
    return ans

def build_system():
    facts = list_facts(20)
    facts_block = ""
    if facts:
        pairs = [f"{k}: {v}" for (k,v) in facts]
        facts_block = "Known TBP facts (user-taught):\n- " + "\n- ".join(pairs) + "\n\n"

    return (
        facts_block +
        "You are TBP-AI, the official meme-assistant of TurboPepe-AI (TBP) 🐸.\n"
        "Answer bilingually (DE/EN) with humor, emoji and meme-style tone.\n"
        "Be witty, friendly, slightly chaotic, but factual when asked.\n"
        "Focus on TBP's AI autonomy, transparency, burned liquidity and no taxes.\n"
        "Never give financial advice. No promises.\n"
        "Use bullet points, emojis, and short sections for clarity.\n"
        "If asked about NFTs, say TBP Gold NFTs are offline.\n"
    )

def build_links(lang: str, needs):
    """Clickable markdown-style links (work in Telegram + Web)"""
    L = {
        "website":  "🌐 Website" if lang=="en" else "🌐 Webseite",
        "telegram": "💬 Telegram",
        "x":        "🐦 X (Twitter)",
        "buy":      "💸 Buy on Sushi" if lang=="en" else "💸 Auf Sushi kaufen",
        "contract": "📜 Polygonscan",
        "pool":     "📊 Charts",
    }

    out = []
    if "website"  in needs: out.append(f"[{L['website']}]({LINKS['website']})")
    if "buy"      in needs: out.append(f"[{L['buy']}]({LINKS['buy']})")
    if "contract" in needs: out.append(f"[{L['contract']}]({LINKS['contract_scan']})")
    if "pool"     in needs:
        out += [
            f"[GeckoTerminal]({LINKS['gecko']})",
            f"[DEXTools]({LINKS['dextools']})",
            f"[DexScreener]({LINKS['dexscreener']})",
        ]
    if "telegram" in needs: out.append(f"[{L['telegram']}]({LINKS['telegram']})")
    if "x"        in needs: out.append(f"[{L['x']}]({LINKS['x']})")

    return "" if not out else ("\n\n— Quick Links —\n" + "\n".join(out))


def linkify(user_q: str, ans: str) -> str:
    """
    Live-Preis/Stats nur bei Preisabsicht (Wortgrenzen!) und ohne Doppelung.
    """
    low = (user_q or "").lower()
    lang = "de" if is_de(user_q) else "en"

    if WORD_RE.search(low):
        p = get_live_price()
        stats = get_market_stats()
        lines = []
        if p is not None:
            lines.append(("Aktueller TBP-Preis" if lang=="de" else "Current TBP price") + f": ${p:0.12f}")
        if stats:
            if stats.get("change_24h") not in (None, "null", ""):
                lines.append(("24h Veränderung" if lang=="de" else "24h Change") + f": {stats['change_24h']}%")
            if stats.get("liquidity_usd") not in (None, "null", ""):
                try:
                    liq = int(float(stats["liquidity_usd"]))
                    lines.append(("Liquidität" if lang=="de" else "Liquidity") + f": ${liq:,}")
                except Exception:
                    pass
            if stats.get("volume_24h") not in (None, "null", ""):
                try:
                    vol = int(float(stats["volume_24h"]))
                    lines.append(("Volumen 24h" if lang=="de" else "Volume 24h") + f": ${vol:,}")
                except Exception:
                    pass
        if lines and ("TBP-Preis" not in ans and "Current TBP price" not in ans):
            ans = "\n".join(lines) + "\n\n" + ans

    need = []
    if re.search(r"(what is|was ist|tokenomics|buy|kaufen|chart|preis|price|kurs)", low, re.I):
        need += ["website", "buy", "contract", "pool", "telegram", "x"]
    need = list(dict.fromkeys(need))
    if need:
        ans += "\n" + build_links(lang, need)
    return ans


# ================================================================
# == CORE AI ==
# ================================================================

def call_openai(question, context):
    if not OPENAI_API_KEY:
        return None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    messages = [{"role": "system", "content": build_system()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        messages.append({"role": role, "content": item.split(": ", 1)[1] if ": " in item else item})
    messages.append({"role": "user", "content": question})
    data = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.4
    }
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data, timeout=40)
        if not r.ok:
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def ai_answer(user_q: str) -> str:
    resp = call_openai(user_q, MEM["ctx"])
    if not resp:
        resp = "Network glitch. try again 🐸"
    resp = sanitize_persona(resp)
    resp = linkify(user_q, resp)
    return resp


# ================================================================
# == WEB ENDPOINT ==
# ================================================================

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


# ================================================================
# == TELEGRAM WEBHOOK ==
# ================================================================

def tg_send(chat_id, text, reply_to=None):
    if not TELEGRAM_TOKEN:
        return
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            # wichtig: Link-Vorschau **aktiv** lassen → Chart-Preview im Chat
            "disable_web_page_preview": False,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception:
        pass


TEACH_RE = re.compile(r"^/teach\s+(.+?)\s*=\s*(.+)$", re.I)
FORGET_RE = re.compile(r"^/forget\s+(.+)$", re.I)

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}
    msg = update.get("message", {}) or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")
    low = text.lower()

    if not chat_id or not text:
        return jsonify({"ok": True})

    # Log jede Message (für späteres Train/Analyse)
    log_msg(chat_id, "user", text)

    # Teach / Forget / Memory
    m = TEACH_RE.match(text)
    if m:
        k, v = m.group(1).strip(), m.group(2).strip()
        ok = set_fact(k, v, source="telegram")
        tg_send(chat_id, ("✅ Gespeichert: " if ok else "❌ Konnte nicht speichern: ") + f"{k} = {v}", reply_to=msg_id)
        return jsonify({"ok": True})

    m = FORGET_RE.match(text)
    if m:
        k = m.group(1).strip()
        ok = del_fact(k)
        tg_send(chat_id, ("🧹 Gelöscht: " if ok else "❌ Nicht gefunden: ") + k, reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/mem"):
        rows = list_facts(20)
        if not rows:
            tg_send(chat_id, "🗒️ Noch keine Fakten gespeichert. Beispiel: /teach goal = 800M MC in 3y", reply_to=msg_id)
        else:
            lines = ["🧠 TBP-Memory:"]
            for k, v in rows:
                lines.append(f"• {k}: {v}")
            tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    # Commands
    if low.startswith("/start"):
        tg_send(chat_id,
                f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP (DE/EN). "
                f"Tipp /links • /price • /chart • /stats • /mem • /teach key = value • /forget key 🚀",
                reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id,
                "/price • /chart • /stats • /links • /mem • /teach key = value • /forget key",
                reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        block = build_links("de" if is_de(low) else "en",
                            ["website","buy","contract","pool","telegram","x"])
        tg_send(chat_id, block or "Links ready.", reply_to=msg_id)
        return jsonify({"ok": True})

    # Preis/Stats nur bei klarer Absicht oder /price
    if low.startswith("/price") or WORD_RE.search(low):
        p = get_live_price()
        stats = get_market_stats() or {}
        lines = []
        if p is not None: lines.append(f"💰 Price: ${p:0.12f}")
        if stats.get("change_24h") not in (None, "null", ""):  lines.append(f"📈 24h: {stats['change_24h']}%")
        if stats.get("liquidity_usd") not in (None, "null", ""):
            try: lines.append(f"💧 Liquidity: ${int(float(stats['liquidity_usd'])):,}")
            except: pass
        if stats.get("volume_24h") not in (None, "null", ""):
            try: lines.append(f"🔄 Volume 24h: ${int(float(stats['volume_24h'])):,}")
            except: pass
        if not lines: lines.append("Price currently unavailable.")
        # Chart-Preview via Link-Vorschau
        lines.append(f"📊 Charts: {LINKS['dexscreener']}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        tg_send(chat_id, f"📊 Live Chart: {LINKS['dexscreener']}\nAlt: {LINKS['dextools']}", reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/stats"):
        stats = get_market_stats() or {}
        lines = ["TBP Stats (Dexscreener):"]
        if stats.get("change_24h") not in (None, "null", ""):  lines.append(f"• 24h Change: {stats['change_24h']}%")
        if stats.get("volume_24h") not in (None, "null", ""):
            try: lines.append(f"• Volume 24h: ${int(float(stats['volume_24h'])):,}")
            except: pass
        if stats.get("liquidity_usd") not in (None, "null", ""):
            try: lines.append(f"• Liquidity: ${int(float(stats['liquidity_usd'])):,}")
            except: pass
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    # normal flow (keine Preisabsicht -> keine Zahlen voranstellen)
    ans = ai_answer(text)
    tg_send(chat_id, ans, reply_to=msg_id)

    MEM["ctx"].append(f"You: {text}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"ok": True})


# ================================================================
# == MAIN ==
# ================================================================
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
