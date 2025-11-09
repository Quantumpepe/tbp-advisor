# server.py — TBP-AI v5.0 (objective-by-default, lang/tone prefs, clean links)
# -*- coding: utf-8 -*-

import os, re, requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# =========================
# CONFIG
# =========================
BOT_NAME       = "TBP-AI"
MODEL_NAME     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# TBP (Polygon)
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
    "contract":     f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

app = Flask(__name__)
CORS(app)

# =========================
# PREFERENCES (per chat)
# =========================
# Default: objective (pro), language auto
PREFS = {}  # chat_id -> {"lang": "auto|de|en", "tone":"pro|fun"}

def get_prefs(chat_id):
    d = PREFS.get(chat_id) or {"lang": "auto", "tone": "pro"}
    PREFS[chat_id] = d
    return d

def set_lang(chat_id, lang):
    p = get_prefs(chat_id); p["lang"] = lang

def set_tone(chat_id, tone):
    p = get_prefs(chat_id); p["tone"] = tone

# =========================
# LIVE DATA
# =========================
def _dexs_pair_obj():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=7,
        )
        j = r.json() if r.ok else {}
        if isinstance(j.get("pair"), dict):
            return j["pair"]
        pairs = j.get("pairs")
        if isinstance(pairs, list) and pairs:
            return pairs[0]
    except Exception:
        pass
    return {}

def get_live_price():
    # 1) GeckoTerminal
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}",
            timeout=7,
        )
        if r.ok:
            attrs = (r.json().get("data") or {}).get("attributes") or {}
            p = attrs.get("base_token_price_usd")
            if p is not None:
                p = float(p)
                if p > 0:
                    return p
    except Exception:
        pass
    # 2) Dexscreener fallback
    try:
        po = _dexs_pair_obj()
        p = po.get("priceUsd")
        if p is not None:
            p = float(p)
            if p > 0:
                return p
    except Exception:
        pass
    return None

def get_market_stats():
    po = _dexs_pair_obj()
    try: change = po.get("priceChange24h")
    except: change = None
    try: liq = (po.get("liquidity") or {}).get("usd")
    except: liq = None
    try: vol = (po.get("volume") or {}).get("h24") or po.get("volume24h")
    except: vol = None
    return {"change_24h": change, "liquidity": liq, "volume": vol}

def format_price_block():
    p = get_live_price()
    s = get_market_stats()
    if not p:
        return "💰 Price unavailable"
    out = [f"💰 Price: ${p:0.12f}"]
    if s.get("change_24h") not in (None, "null", ""):
        out.append(f"📈 24h: {s['change_24h']}%")
    if s.get("liquidity") not in (None, "null", ""):
        try: out.append(f"💧 Liquidity: ${int(float(s['liquidity'])):,}")
        except: pass
    if s.get("volume") not in (None, "null", ""):
        try: out.append(f"🔄 Volume 24h: ${int(float(s['volume'])):,}")
        except: pass
    return "\n".join(out)

# =========================
# AI
# =========================
def detect_lang(text):
    t = (text or "").lower()
    if re.search(r"\b(der|die|das|was|wie|warum|kann|preis|kurs|kaufen|chart|tokenomics)\b", t):
        return "de"
    return "en"

SYSTEM_BASE = (
    "You are TBP-AI, the official assistant of TurboPepe-AI (TBP).\n"
    "Never give financial advice or promises. No price predictions.\n"
    "Do NOT include links unless explicitly asked with words like buy, chart, links, website, x, telegram, contract.\n"
)

def system_prompt(lang, tone):
    if lang == "de":
        style = "Schreibe klar, kurz, sachlich."
        if tone == "fun":
            style = "Schreibe kurz, witzig, aber sachlich korrekt."
        return (
            SYSTEM_BASE +
            style + "\nAntworte ausschließlich auf Deutsch.\n"
            "Wenn die Frage allgemein 'was ist TBP' lautet, gib eine kurze, objektive Ein-Satz-Erklärung."
        )
    # en
    style = "Write clearly, concisely, objectively."
    if tone == "fun":
        style = "Write short, witty, but factual."
    return (
        SYSTEM_BASE +
        style + "\nAnswer in English only.\n"
        "If the question is 'what is tbp', provide a short, objective one-sentence description."
    )

def ai_call(q, lang="en", tone="pro", ctx=None):
    if not OPENAI_API_KEY:
        # minimal fallback
        return "API key missing 🐸"
    messages = [
        {"role": "system", "content": system_prompt(lang, tone)},
    ]
    if ctx:
        messages.extend(ctx[-4:])
    messages.append({"role": "user", "content": q})
    data = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": 350,
        "temperature": 0.5 if tone=="pro" else 0.8,
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

# =========================
# WEB ENDPOINT
# =========================
@app.route("/ask", methods=["POST"])
def ask():
    j = request.json or {}
    q = (j.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200
    # simple auto language; tone default pro
    lang = detect_lang(q)
    ans = route_intent(q, lang, "pro", web=True, chat_id=None, mid=None)
    return jsonify({"answer": ans})

# =========================
# INTENT ROUTER (shared web + TG)
# =========================
WHAT_IS_RE = re.compile(r"^(what\s+is\s+tbp|was\s+ist\s+tbp)\b", re.I)
PRICE_RE   = re.compile(r"\b(preis|price|kurs)\b", re.I)
BUY_RE     = re.compile(r"\b(buy|kauf|kaufen)\b", re.I)
CHART_RE   = re.compile(r"\b(chart|charts)\b", re.I)
LINKS_RE   = re.compile(r"\b(links?)\b", re.I)

def one_liner(lang="en"):
    if lang == "de":
        return ("TBP (TurboPepe-AI) ist ein Meme-Token auf Polygon mit verifizierter "
                "Transparenz (LP geburnt, 0 % Tax) und einem Bot, der Live-Daten liefert.")
    return ("TBP (TurboPepe-AI) is a Polygon meme token with transparent setup "
            "(burned LP, 0% tax) and an assistant that reports live on-chain data.")

def route_intent(q, lang, tone, web=False, chat_id=None, mid=None):
    low = (q or "").lower()

    # force short, neutral description
    if WHAT_IS_RE.search(low):
        return one_liner(lang)

    # price
    if PRICE_RE.search(low) or low.startswith("/price"):
        return format_price_block()

    # on web we just return text; on TG we send buttons via tg_send from the caller
    if (BUY_RE.search(low) or low.startswith("/buy")) and web:
        return "Open SushiSwap:\n" + LINKS["buy"]
    if (CHART_RE.search(low) or low.startswith("/chart")) and web:
        return "Charts:\n" + LINKS["dexscreener"]

    # default -> AI (objective by default)
    return ai_call(q, lang=lang, tone=tone)

# =========================
# TELEGRAM
# =========================
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
            json=payload, timeout=12
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

    if not chat_id or not txt:
        return jsonify({"ok": True})

    prefs = get_prefs(chat_id)

    low = txt.lower()

    # quick language switches
    if low in ("english", "englisch", "/lang en"):
        set_lang(chat_id, "en"); tg_send(chat_id, "Okay, English only. 🇬🇧", reply_to=mid); return jsonify({"ok": True})
    if low in ("deutsch", "german", "/lang de"):
        set_lang(chat_id, "de"); tg_send(chat_id, "Alles klar, nur Deutsch. 🇩🇪", reply_to=mid); return jsonify({"ok": True})

    # tone
    if low.startswith("/tone"):
        if "fun" in low: set_tone(chat_id, "fun");  tg_send(chat_id, "Humor an. 🐸✨", reply_to=mid)
        else:            set_tone(chat_id, "pro");  tg_send(chat_id, "Sachlich aktiv. ✅", reply_to=mid)
        return jsonify({"ok": True})

    # commands
    if low.startswith("/start"):
        tg_send(chat_id,
            f"Hi, ich bin {BOT_NAME}. Standard: sachlich & kurz. "
            "Befehle: /price /chart /buy /links /lang de|en /tone pro|fun",
            reply_to=mid
        )
        return jsonify({"ok": True})

    if low.startswith("/links") or LINKS_RE.search(low):
        tg_send(
            chat_id, "Quick Links:",
            buttons=[
                [{"text":"🌐 Website", "url": LINKS["website"]}],
                [{"text":"🍣 Buy on Sushi", "url": LINKS["buy"]}],
                [{"text":"📜 Polygonscan", "url": LINKS["contract"]}],
                [{"text":"📊 DexScreener", "url": LINKS["dexscreener"]}],
                [{"text":"📈 DEXTools", "url": LINKS["dextools"]}],
                [{"text":"🟢 GeckoTerminal", "url": LINKS["gecko"]}],
                [{"text":"💬 Telegram", "url": LINKS["telegram"]}],
                [{"text":"🐦 X (Twitter)", "url": LINKS["x"]}],
            ],
            reply_to=mid
        )
        return jsonify({"ok": True})

    if low.startswith("/buy") or BUY_RE.search(low):
        tg_send(
            chat_id, "Buy TBP:",
            buttons=[[{"text": "SushiSwap (Polygon)", "url": LINKS["buy"]}]],
            reply_to=mid
        )
        return jsonify({"ok": True})

    if low.startswith("/chart") or CHART_RE.search(low):
        tg_send(
            chat_id, "Charts:",
            buttons=[
                [{"text":"DexScreener","url": LINKS["dexscreener"]}],
                [{"text":"DEXTools","url": LINKS["dextools"]}],
                [{"text":"GeckoTerminal","url": LINKS["gecko"]}],
            ],
            reply_to=mid
        )
        return jsonify({"ok": True})

    if low.startswith("/price") or PRICE_RE.search(low):
        tg_send(chat_id, format_price_block(), reply_to=mid)
        return jsonify({"ok": True})

    # figure language
    lang = prefs["lang"] if prefs["lang"] != "auto" else detect_lang(txt)

    # main route (no auto-links)
    ans = route_intent(txt, lang=lang, tone=prefs["tone"], web=False, chat_id=chat_id, mid=mid)
    tg_send(chat_id, ans, reply_to=mid)
    return jsonify({"ok": True})

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] running on :{port}")
    app.run(host="0.0.0.0", port=port)
