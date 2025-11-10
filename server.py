# server.py — TBP-AI unified backend (Web + Telegram) — v5
# -*- coding: utf-8 -*-

import os, re, json, time, requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

# =========================
# CONFIG / LINKS / CONSTANTS
# =========================
BOT_NAME        = "TBP-AI"
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "").strip()  # required for /setwebhook

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

# Rough supply for MC calc on website (AI mentions cautiously)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# short rolling memory
MEM = {"ctx": []}

app = Flask(__name__)
CORS(app)

# =========================
# PRICE & MARKET STATS
# =========================
def get_live_price():
    """Primary: GeckoTerminal, Fallback: Dexscreener. Return float|None (USD)."""
    # 1) GeckoTerminal
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}"
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {}) or {}
        v = attrs.get("base_token_price_usd")
        price = float(v) if v not in (None, "null", "") else None
        if price and price > 0:
            return price
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
        price = float(v) if v not in (None, "null", "") else None
        if price and price > 0:
            return price
    except Exception:
        pass
    return None

def get_market_stats():
    """Dexscreener: change 24h, volume 24h, liquidity USD."""
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

# =========================
# UTILITIES
# =========================
WORD_RE = re.compile(r"\b(preis|price|kurs|chart|charts)\b", re.I)

def is_de(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum|kann|preis|kurs|tokenomics|listung)\b", t))

def sanitize_persona(ans: str) -> str:
    if not ans:
        return ""
    # remove NFT promises
    if re.search(r"\bNFT\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "", ans, flags=re.I).strip()
    ans = re.sub(r"(?i)(financial advice|finanzberatung)", "information", ans)
    return ans

def build_system():
    return (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Answer in the user's language (German or English). Keep it concise, friendly, slightly witty.\n"
        "Do not give financial advice. Be factual on token info. Avoid overpromising.\n"
        "If users ask for NFTs or staking, say: 'planned for the future'.\n"
    )

def build_links(lang: str, needs):
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
        need += ["website","buy","contract","pool","telegram","x"]
    need = list(dict.fromkeys(need))
    if need:
        ans += "\n" + build_links(lang, need)
    return ans

# =========================
# CORE AI
# =========================
def call_openai(question, context):
    if not OPENAI_API_KEY:
        return None
    headers = {"Content-Type":"application/json","Authorization":f"Bearer {OPENAI_API_KEY}"}
    messages = [{"role":"system","content":build_system()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        messages.append({"role":role, "content": item.split(": ",1)[1] if ": " in item else item})
    messages.append({"role":"user","content":question})
    data = {"model":OPENAI_MODEL,"messages":messages,"max_tokens":450,"temperature":0.5}
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data, timeout=40)
        if not r.ok:
            return None
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

def ai_answer(user_q: str) -> str:
    resp = call_openai(user_q, MEM["ctx"]) or ("Ich sammle kurz Daten…" if is_de(user_q) else "Collecting data…")
    resp = sanitize_persona(resp)
    resp = linkify(user_q, resp)
    return resp

# =========================
# WEB ENDPOINTS (Health, Ask)
# =========================
@app.route("/")
def root():
    return jsonify({"ok": True, "service": "tbp-advisor", "time": datetime.utcnow().isoformat()+"Z"})

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

# =========================
# TELEGRAM
# =========================
def tg_send(chat_id, text, reply_to=None):
    if not TELEGRAM_TOKEN:
        return
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,  # allow chart preview
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception:
        pass

# GET handler for quick browser test; POST for Telegram webhook
@app.route("/telegram", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        # helps when you open the URL in browser; Telegram itself uses POST
        return jsonify({"ok": True, "hint": "POST updates here (Telegram webhook)."}), 200

    update = request.json or {}
    msg = update.get("message", {}) or {}
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")
    low = text.lower()

    if not chat_id or not text:
        return jsonify({"ok": True})

    # Commands
    if low.startswith("/start"):
        tg_send(chat_id,
                f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP (DE/EN). "
                f"Tipps: /price • /chart • /stats • /links",
                reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id, "/price • /chart • /stats • /links", reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        block = build_links("de" if is_de(low) else "en",
                            ["website","buy","contract","pool","telegram","x"])
        tg_send(chat_id, block or "Links ready.", reply_to=msg_id)
        return jsonify({"ok": True})

    # Price/Stats if clearly asked or /price
    if low.startswith("/price") or WORD_RE.search(low):
        p = get_live_price()
        stats = get_market_stats() or {}
        lines = []
        if p is not None: lines.append(f"💰 Price: ${p:0.12f}")
        if stats.get("change_24h") not in (None, "null", ""):  lines.append(f"📈 24h: {stats['change_24h']}%")
        if stats.get("liquidity_usd") not in (None, "null", ""):
            try: lines.append(f"💧 Liquidity: ${int(float(stats['liquidity_usd'])):,}")
            except Exception: pass
        if stats.get("volume_24h") not in (None, "null", ""):
            try: lines.append(f"🔄 Volume 24h: ${int(float(stats['volume_24h'])):,}")
            except Exception: pass
        if not lines: lines.append("Price currently unavailable.")
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
            except Exception: pass
        if stats.get("liquidity_usd") not in (None, "null", ""):
            try: lines.append(f"• Liquidity: ${int(float(stats['liquidity_usd'])):,}")
            except Exception: pass
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    # Normal flow
    ans = ai_answer(text)
    tg_send(chat_id, ans, reply_to=msg_id)
    MEM["ctx"].append(f"You: {text}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"ok": True})

# Optional: set webhook from the app (protect with WEBHOOK_SECRET)
@app.route("/setwebhook", methods=["POST","GET"])
def setwebhook():
    if not TELEGRAM_TOKEN:
        return jsonify({"ok": False, "error": "missing TELEGRAM_BOT_TOKEN"}), 400
    if not WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "missing WEBHOOK_SECRET env"}), 400
    # require ?key=<WEBHOOK_SECRET>
    key = request.args.get("key","")
    if key != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    url = request.host_url.rstrip("/") + "/telegram"
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": url},
            timeout=10
        )
        return jsonify({"ok": True, "telegram": resp.json(), "webhook_url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ================
# MAIN
# ================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
