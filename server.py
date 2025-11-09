# server.py — TBP-AI v4.2 (Web + Telegram + Inline Buttons + Humor)
# -*- coding: utf-8 -*-

import os, re, requests, json
from flask import Flask, request, jsonify
from flask_cors import CORS

# =========================================================
# CONFIG
# =========================================================
BOT_NAME       = "TBP-AI"
MODEL_NAME     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# TBP Data
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"

LINKS = {
    "website": "https://quantumpepe.github.io/TurboPepe/",
    "buy": f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dextools": f"https://www.dextools.io/app/en/polygon/pair-explorer/{TBP_PAIR}",
    "dexscreener": f"https://dexscreener.com/polygon/{TBP_PAIR}",
    "gecko": f"https://www.geckoterminal.com/en/polygon_pos/pools/{TBP_PAIR}?embed=1",
    "telegram": "https://t.me/turbopepe25",
    "x": "https://x.com/TurboPepe2025",
    "contract": f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

app = Flask(__name__)
CORS(app)

# =========================================================
# LIVE DATA
# =========================================================
def get_live_price():
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}",
            timeout=5,
        )
        j = r.json().get("data", {}).get("attributes", {})
        p = j.get("base_token_price_usd")
        return float(p) if p else None
    except Exception:
        try:
            r = requests.get(
                f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
                timeout=5,
            )
            d = r.json().get("pair", {})
            p = d.get("priceUsd")
            return float(p) if p else None
        except Exception:
            return None


def get_market_stats():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=5,
        )
        p = r.json().get("pair", {})
        return {
            "change_24h": p.get("priceChange24h"),
            "liquidity": (p.get("liquidity") or {}).get("usd"),
            "volume": (p.get("volume") or {}).get("h24") or p.get("volume24h"),
        }
    except Exception:
        return {}

# =========================================================
# UTILITIES
# =========================================================
def is_de(txt):
    return bool(re.search(r"\b(der|die|das|ich|du|preis|kurs|chart|hilfe)\b", txt.lower()))

def ai_call(q, ctx=[]):
    if not OPENAI_API_KEY:
        return "API key missing 🐸"
    data = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) 🐸.\n"
                    "Speak German if the user speaks German, else English.\n"
                    "Tone: humorous, confident, meme-like, but smart.\n"
                    "No financial advice, no promises.\n"
                    "Be short, funny, and slightly chaotic — like a meme lord.\n"
                    "Only include links if the user explicitly asks for them.\n"
                    "If asked 'wer bist du' or 'who are you', describe yourself humorously "
                    "without sending links.\n"
                ),
            },
            *ctx[-4:],
            {"role": "user", "content": q},
        ],
        "max_tokens": 400,
        "temperature": 0.7,
    }
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json=data,
            timeout=40,
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Network issue 🐸 ({e})"


def format_price_block():
    p = get_live_price()
    s = get_market_stats()
    if not p:
        return "💰 Price unavailable"
    txt = f"💰 Price: ${p:0.12f}"
    if s.get("change_24h"):
        txt += f"\n📈 24h Change: {s['change_24h']}%"
    if s.get("liquidity"):
        txt += f"\n💧 Liquidity: ${int(float(s['liquidity'])):,}"
    if s.get("volume"):
        txt += f"\n🔄 Volume 24h: ${int(float(s['volume'])):,}"
    return txt

# =========================================================
# WEB ENDPOINT
# =========================================================
@app.route("/ask", methods=["POST"])
def ask():
    q = (request.json or {}).get("question", "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200
    ans = ai_call(q)
    return jsonify({"answer": ans})

# =========================================================
# TELEGRAM BOT
# =========================================================
def tg_send(chat_id, text, buttons=None, reply_to=None):
    if not TELEGRAM_TOKEN:
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


@app.route("/telegram", methods=["POST"])
def telegram():
    u = request.json or {}
    m = u.get("message", {}) or {}
    txt = (m.get("text") or "").strip()
    chat_id = m.get("chat", {}).get("id")
    mid = m.get("message_id")
    low = txt.lower()

    if not chat_id or not txt:
        return jsonify({"ok": True})

    # === Commands ===
    if low.startswith("/start"):
        tg_send(
            chat_id,
            f"Hey 🐸 Ich bin {BOT_NAME} – dein Meme-Assistent!\n"
            "Frag mich über Preis, Chart oder wo du TBP kaufen kannst.\n"
            "Befehle: /price /chart /links /about 🚀",
            reply_to=mid,
        )
        return jsonify({"ok": True})

    if low.startswith("/about") or "wer bist" in low or "who are" in low:
        tg_send(
            chat_id,
            "Ich bin TBP-AI 🧠🐸 – halb Meme, halb KI!\n"
            "Ich bin hier, um Chaos in Krypto zu bringen (aber mit Stil). 💅\n"
            "Kein Steuer, kein Stress, nur Turbo.",
            reply_to=mid,
        )
        return jsonify({"ok": True})

    if low.startswith("/price") or "preis" in low or "price" in low:
        tg_send(chat_id, format_price_block(), reply_to=mid)
        return jsonify({"ok": True})

    if "buy" in low or "kauf" in low or low.startswith("/buy"):
        tg_send(
            chat_id,
            "💸 You can grab TBP here:",
            buttons=[[{"text": "Buy on Sushi 🍣", "url": LINKS["buy"]}]],
            reply_to=mid,
        )
        return jsonify({"ok": True})

    if "chart" in low or low.startswith("/chart"):
        tg_send(
            chat_id,
            "📊 Charts incoming:",
            buttons=[
                [{"text": "DexScreener", "url": LINKS["dexscreener"]}],
                [{"text": "DEXTools", "url": LINKS["dextools"]}],
                [{"text": "GeckoTerminal", "url": LINKS["gecko"]}],
            ],
            reply_to=mid,
        )
        return jsonify({"ok": True})

    if "telegram" in low:
        tg_send(
            chat_id,
            "Join the swamp 💬🐸",
            buttons=[[{"text": "TurboPepe Telegram", "url": LINKS["telegram"]}]],
            reply_to=mid,
        )
        return jsonify({"ok": True})

    if "twitter" in low or "x" in low:
        tg_send(
            chat_id,
            "Follow me on X 🐦🔥",
            buttons=[[{"text": "TurboPepe on X", "url": LINKS["x"]}]],
            reply_to=mid,
        )
        return jsonify({"ok": True})

    # === General chat ===
    ans = ai_call(txt)
    tg_send(chat_id, ans, reply_to=mid)
    return jsonify({"ok": True})

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] running on port {port}")
    app.run(host="0.0.0.0", port=port)
