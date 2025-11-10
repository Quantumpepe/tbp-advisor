# server.py — TBP-AI unified backend (Web + Telegram)
# -*- coding: utf-8 -*-

import os
import re
import json
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

# ================================================================
# CONFIG / CONSTANTS
# ================================================================

BOT_NAME       = "TBP-AI"
MODEL_NAME     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# TBP (Polygon)
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi pair

LINKS = {
    "website":       "https://quantumpepe.github.io/TurboPepe/",
    "buy":           f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dextools":      f"https://www.dextools.io/app/en/polygon/pair-explorer/{TBP_PAIR}",
    "dexscreener":   f"https://dexscreener.com/polygon/{TBP_PAIR}",
    "gecko":         f"https://www.geckoterminal.com/en/polygon_pos/pools/{TBP_PAIR}?embed=1",
    "contract_scan": f"https://polygonscan.com/token/{TBP_CONTRACT}",
    "telegram":      "https://t.me/turbopepe25",
    "x":             "https://x.com/TurboPepe2025",
}

# Simple MC estimate (only for site; bot answers cautiously)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# rolling short memory for LLM
MEM = {"ctx": []}

app = Flask(__name__)
CORS(app)

# ================================================================
# HELPERS
# ================================================================

WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)

def is_de(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum|kann|preis|kurs|tokenomics|listung)\b", t))

def fmt_usd_int(x):
    try:
        return f"${int(float(x)):,}"
    except Exception:
        return "N/A"

def fmt_price12(x):
    try:
        return f"${float(x):0.12f}"
    except Exception:
        return "N/A"

def sanitize_persona(ans: str) -> str:
    if not ans:
        return ""
    # cut NFT chatter
    if re.search(r"\bNFT\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "", ans, flags=re.I).strip()
    # no financial advice wording
    ans = re.sub(r"(?i)(financial advice|finanzberatung)", "information", ans)
    return ans

def build_links_html(lang: str, needs):
    L = {
        "website":  "🌐 Website" if lang == "en" else "🌐 Webseite",
        "buy":      "💸 Buy on Sushi" if lang == "en" else "💸 Auf Sushi kaufen",
        "contract": "📜 Polygonscan",
        "pool":     "📊 Charts",
        "telegram": "💬 Telegram",
        "x":        "🐦 X (Twitter)",
    }
    items = []
    if "website"  in needs: items.append(f'<a href="{LINKS["website"]}">{L["website"]}</a>')
    if "buy"      in needs: items.append(f'<a href="{LINKS["buy"]}">{L["buy"]}</a>')
    if "contract" in needs: items.append(f'<a href="{LINKS["contract_scan"]}">{L["contract"]}</a>')
    if "pool"     in needs:
        items.append(f'<a href="{LINKS["dexscreener"]}">DexScreener</a>')
        items.append(f'<a href="{LINKS["dextools"]}">DEXTools</a>')
        items.append(f'<a href="{LINKS["gecko"]}">GeckoTerminal</a>')
    if "telegram" in needs: items.append(f'<a href="{LINKS["telegram"]}">{L["telegram"]}</a>')
    if "x"        in needs: items.append(f'<a href="{LINKS["x"]}">{L["x"]}</a>')
    if not items:
        return ""
    return "\n\n— Quick Links —\n" + "\n".join(items)

# ================================================================
# PRICE / STATS
# ================================================================

def get_live_price():
    # 1) GeckoTerminal
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {})
        v = attrs.get("base_token_price_usd")
        p = float(v) if v not in (None, "", "null") else None
        if p and p > 0:
            return p
    except Exception:
        pass

    # 2) Dexscreener
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        v = pair.get("priceUsd")
        p = float(v) if v not in (None, "", "null") else None
        if p and p > 0:
            return p
    except Exception:
        pass

    return None

def get_market_stats():
    """Dexscreener: 24h change, volume, liquidity (USD)."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
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

# ================================================================
# LLM
# ================================================================

def build_system():
    # keep concise; links only on explicit ask
    return (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Detect user language (DE/EN) and reply only in that language (no mixing).\n"
        "Tone: witty, friendly, meme-savvy — but concise. Avoid long rambles.\n"
        "No financial advice or promises. Do not invent numbers.\n"
        "Only include links if the user explicitly asks (e.g., buy, chart, links, website, contract).\n"
        "If the message includes a photo, reply in English and offer a funny meme take.\n"
        "If user asks price/stats, keep it short; the tool wrapper will prepend live numbers.\n"
    )

def call_openai(question, context):
    if not OPENAI_API_KEY:
        return None
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {OPENAI_API_KEY}"}
    msgs = [{"role": "system", "content": build_system()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        msgs.append({"role": role, "content": item.split(": ",1)[1] if ": " in item else item})
    msgs.append({"role": "user", "content": question})
    payload = {"model": MODEL_NAME, "messages": msgs, "max_tokens": 500, "temperature": 0.45}

    try:
        r = requests.post("https://api.openai.com/v1/chat/completions",
                          headers=headers, json=payload, timeout=40)
        if not r.ok:
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

def linkify(user_q: str, ans: str) -> str:
    """Add live numbers only for price intent; add links only on explicit ask."""
    low = (user_q or "").lower()
    lang = "de" if is_de(user_q) else "en"

    if WORD_PRICE.search(low):
        p = get_live_price()
        stats = get_market_stats()
        head = []
        if p is not None:
            head.append(("Aktueller TBP-Preis" if lang=="de" else "Current TBP price") + f": {fmt_price12(p)}")
        if stats:
            if stats.get("change_24h") not in (None, "", "null"):
                head.append(("24h Veränderung" if lang=="de" else "24h Change") + f": {stats['change_24h']}%")
            if stats.get("liquidity_usd") not in (None, "", "null"):
                head.append(("Liquidität" if lang=="de" else "Liquidity") + f": {fmt_usd_int(stats['liquidity_usd'])}")
            if stats.get("volume_24h") not in (None, "", "null"):
                head.append(("Volumen 24h" if lang=="de" else "Volume 24h") + f": {fmt_usd_int(stats['volume_24h'])}")
        if head:
            ans = "\n".join(head) + "\n\n" + ans

    needs = []
    if re.search(r"\b(buy|kaufen)\b", low):         needs += ["buy"]
    if re.search(r"\b(chart|charts|dex|gecko)\b", low): needs += ["pool"]
    if re.search(r"\b(contract|scan)\b", low):      needs += ["contract"]
    if re.search(r"\b(website|webseite|links)\b", low): needs += ["website","telegram","x"]
    if needs:
        ans += "\n" + build_links_html(lang, list(dict.fromkeys(needs)))
    return ans

def ai_answer(user_q: str) -> str:
    resp = call_openai(user_q, MEM["ctx"]) or ("Network glitch. try again 🐸" if is_de(user_q) else "Network glitch. Try again 🐸")
    resp = sanitize_persona(resp)
    return linkify(user_q, resp)

# ================================================================
# WEB API
# ================================================================

@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()+"Z"})

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
# TELEGRAM
# ================================================================

def tg_send(chat_id, text, reply_to=None):
    if not TELEGRAM_TOKEN:
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,  # allow chart preview
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=12)
    except Exception:
        pass

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}

    msg = update.get("message", {}) or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    msg_id  = msg.get("message_id")
    text    = (msg.get("text") or "").strip()
    photos  = msg.get("photo")  # list if image message

    if not chat_id:
        return jsonify({"ok": True})

    # --- Photo handling: always reply in EN with meme prompt hint
    if photos and not text:
        tg_send(chat_id,
                "Nice photo! Want me to spin a meme from it?\n\nTip: <b>/meme</b> your prompt (e.g., <i>/meme pepe laser eyes</i>).",
                reply_to=msg_id)
        return jsonify({"ok": True})

    low = (text or "").lower()

    # Commands / quick intents
    if low.startswith("/start"):
        tg_send(chat_id,
                f"Hi, I'm {BOT_NAME}. Ask me anything about TBP (DE/EN).\n"
                "Shortcuts: /price • /chart • /buy • /links • /stats",
                reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id,
                "/price — live price\n"
                "/chart — chart links\n"
                "/buy — Sushi link\n"
                "/links — website, socials, contract\n"
                "/stats — 24h change • liquidity • volume",
                reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/buy") or re.fullmatch(r"(buy|kaufen)", low):
        tg_send(chat_id, f'💸 <a href="{LINKS["buy"]}">Buy on Sushi (Polygon)</a>', reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/contract") or "contract" in low or "scan" in low:
        tg_send(chat_id, f'📜 <a href="{LINKS["contract_scan"]}">Polygonscan (TBP)</a>', reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart") or re.fullmatch(r"(chart|charts)", low):
        tg_send(chat_id,
                f'📊 <a href="{LINKS["dexscreener"]}">DexScreener</a>\n'
                f'↗️ <a href="{LINKS["dextools"]}">DEXTools</a>\n'
                f'🟢 <a href="{LINKS["gecko"]}">GeckoTerminal</a>',
                reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        lang = "de" if is_de(low) else "en"
        block = build_links_html(lang, ["website","buy","contract","pool","telegram","x"])
        tg_send(chat_id, block, reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/price") or WORD_PRICE.search(low):
        p = get_live_price()
        stats = get_market_stats() or {}
        out = []
        out.append(f"💰 Price: {fmt_price12(p)}")
        chg = stats.get("change_24h")
        if chg not in (None, "", "null"):
            out.append(f"📈 24h: {chg}%")
        liq = stats.get("liquidity_usd")
        if liq not in (None, "", "null"):
            out.append(f"💧 Liquidity: {fmt_usd_int(liq)}")
        vol = stats.get("volume_24h")
        if vol not in (None, "", "null"):
            out.append(f"🔄 Volume 24h: {fmt_usd_int(vol)}")
        out.append(f'📊 Charts: {LINKS["dexscreener"]}')
        tg_send(chat_id, "\n".join(out), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/stats"):
        stats = get_market_stats() or {}
        lines = ["TBP Stats:"]
        chg = stats.get("change_24h")
        if chg not in (None, "", "null"): lines.append(f"• 24h Change: {chg}%")
        vol = stats.get("volume_24h")
        if vol not in (None, "", "null"): lines.append(f"• Volume 24h: {fmt_usd_int(vol)}")
        liq = stats.get("liquidity_usd")
        if liq not in (None, "", "null"): lines.append(f"• Liquidity: {fmt_usd_int(liq)}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    # normal LLM flow (language detected; no links unless asked)
    ans = ai_answer(text)
    tg_send(chat_id, ans, reply_to=msg_id)

    MEM["ctx"].append(f"You: {text}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"ok": True})

# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
