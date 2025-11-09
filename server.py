# server.py — TBP-AI v5.3
# Adds: Auto-posting scheduler + engagement (quizzes, questions), keeps DE/EN + humor
# -*- coding: utf-8 -*-

import os, re, base64, time, random, threading, requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# =========================
# CONFIG
# =========================
BOT_NAME       = "TBP-AI"
MODEL_NAME     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
IMG_MODEL      = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
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
# PREFERENCES & AUTOPOST
# =========================
PREFS = {}     # chat_id -> {"lang": "auto|de|en", "tone":"pro|fun"}
AUTOP = {}     # chat_id -> {"on": bool, "interval": int (sec), "next": ts, "cycle": int}

def now(): return int(time.time())

def get_prefs(chat_id):
    d = PREFS.get(chat_id) or {"lang": "auto", "tone": "pro"}
    PREFS[chat_id] = d
    return d

def start_autopost(chat_id, minutes=60):
    sec = max(300, int(minutes) * 60)  # min 5 min
    AUTOP[chat_id] = {"on": True, "interval": sec, "next": now()+sec, "cycle": 0}

def stop_autopost(chat_id):
    AUTOP[chat_id] = {"on": False, "interval": 0, "next": 0, "cycle": 0}

# =========================
# LIVE DATA (price/stats)
# =========================
def _dexs_pair_obj():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=7,
        )
        j = r.json() if r.ok else {}
        if isinstance(j.get("pair"), dict): return j["pair"]
        pairs = j.get("pairs")
        if isinstance(pairs, list) and pairs: return pairs[0]
    except Exception:
        pass
    return {}

def get_live_price():
    # GeckoTerminal first
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
                if p > 0: return p
    except Exception:
        pass
    # Dexscreener fallback
    try:
        po = _dexs_pair_obj()
        p = po.get("priceUsd")
        if p is not None:
            p = float(p)
            if p > 0: return p
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

def format_price_block(lang="en"):
    p = get_live_price()
    s = get_market_stats()
    if not p:
        return "💰 Price unavailable" if lang=="en" else "💰 Preis derzeit nicht verfügbar"
    out = [("💰 Price" if lang=="en" else "💰 Preis") + f": ${p:0.12f}"]
    if s.get("change_24h") not in (None, "null", ""):
        out.append(("📈 24h" if lang=="en" else "📈 24h") + f": {s['change_24h']}%")
    if s.get("liquidity") not in (None, "null", ""):
        try: out.append(("💧 Liquidity" if lang=="en" else "💧 Liquidität") + f": ${int(float(s['liquidity'])):,}")
        except: pass
    if s.get("volume") not in (None, "null", ""):
        try: out.append(("🔄 Volume 24h" if lang=="en" else "🔄 Volumen 24h") + f": ${int(float(s['volume'])):,}")
        except: pass
    return "\n".join(out)

# =========================
# AI (chat text)
# =========================
def detect_lang(text):
    t = (text or "").lower()
    if re.search(r"\b(der|die|das|was|wie|warum|kann|preis|kurs|kaufen|chart|tokenomics)\b", t):
        return "de"
    return "en"

SYSTEM_BASE = (
    "You are TBP-AI, the official assistant of TurboPepe-AI (TBP).\n"
    "Never give financial advice or promises. No price predictions.\n"
    "Do NOT include links unless explicitly asked with keywords (buy, chart, links, website, x, telegram, contract).\n"
)

def system_prompt(lang, tone):
    if lang == "de":
        style = "Schreibe klar und knapp; wenn 'fun': witzig aber korrekt."
        if tone == "fun": style = "Schreibe kurz, witzig, aber korrekt."
        return SYSTEM_BASE + style + "\nAntworte ausschließlich auf Deutsch."
    style = "Write concise and clear; if 'fun': short, witty, but factual."
    if tone == "fun": style = "Write short, witty, but factual."
    return SYSTEM_BASE + style + "\nAnswer in English only."

def ai_call(q, lang="en", tone="pro", ctx=None):
    if not OPENAI_API_KEY:
        return "API key missing 🐸"
    messages = [{"role":"system","content":system_prompt(lang,tone)}]
    if ctx: messages.extend(ctx[-4:])
    messages.append({"role":"user","content":q})
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": MODEL_NAME, "messages": messages, "max_tokens": 350,
                  "temperature": 0.5 if tone=="pro" else 0.8},
            timeout=40,
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Network issue 🐸 ({e})"

# =========================
# IMAGE GENERATION
# =========================
NSFW_RE = re.compile(r"(nude|nsfw|sexual|porno|kill|terror|blood|gore)", re.I)

def safe_image_prompt(p):
    if not p or NSFW_RE.search(p):
        return None
    return p.strip()

def openai_image_b64(prompt, size="1024x1024"):
    if not OPENAI_API_KEY: return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": IMG_MODEL, "prompt": prompt, "size": size, "response_format":"b64_json"},
            timeout=60,
        )
        j = r.json()
        b64 = j["data"][0]["b64_json"]
        return base64.b64decode(b64)
    except Exception:
        return None

# =========================
# INTENT ROUTER
# =========================
WHAT_IS_RE = re.compile(r"^(what\s+is\s+tbp|was\s+ist\s+tbp)\b", re.I)
PRICE_RE   = re.compile(r"\b(preis|price|kurs)\b", re.I)
BUY_RE     = re.compile(r"\b(buy|kauf|kaufen)\b", re.I)
CHART_RE   = re.compile(r"\b(chart|charts)\b", re.I)
LINKS_RE   = re.compile(r"\b(links?)\b", re.I)
IMG_CMD_RE = re.compile(r"^/(img|image)\b", re.I)

def one_liner(lang="en"):
    if lang == "de":
        return ("TBP (TurboPepe-AI) ist ein Meme-Token auf Polygon: LP geburnt, 0 % Tax, "
                "mit einem Bot, der Live-Daten & Antworten liefert.")
    return ("TBP (TurboPepe-AI) is a Polygon meme token: burned LP, 0% tax, "
            "with a bot that answers and posts live stats.")

def route_intent(q, lang, tone, web=False):
    low = (q or "").lower()
    if WHAT_IS_RE.search(low): return one_liner(lang)
    if PRICE_RE.search(low) or low.startswith("/price"): return format_price_block(lang)
    if (BUY_RE.search(low) or low.startswith("/buy")) and web:   return "Open SushiSwap:\n" + LINKS["buy"]
    if (CHART_RE.search(low) or low.startswith("/chart")) and web: return "Charts:\n" + LINKS["dexscreener"]
    return ai_call(q, lang=lang, tone=tone)

# =========================
# WEB ENDPOINTS
# =========================
@app.route("/ask", methods=["POST"])
def ask():
    j = request.json or {}
    q = (j.get("question") or "").strip()
    if not q: return jsonify({"answer":"empty question"}), 200
    lang = detect_lang(q)
    ans = route_intent(q, lang, "pro", web=True)
    return jsonify({"answer": ans})

@app.route("/image", methods=["POST"])
def image_api():
    j = request.json or {}
    prompt = safe_image_prompt(j.get("prompt"))
    size   = j.get("size") or "1024x1024"
    if not prompt: return jsonify({"error":"prompt blocked or empty"}), 400
    png = openai_image_b64(prompt, size=size)
    if not png: return jsonify({"error":"generation failed"}), 500
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return jsonify({"image": data_url, "size": size})

# =========================
# TELEGRAM SENDERS
# =========================
def tg_send(chat_id, text, buttons=None, reply_to=None, markdown=True):
    if not TELEGRAM_TOKEN: return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown" if markdown else "HTML",
        "disable_web_page_preview": False,
    }
    if buttons:  payload["reply_markup"] = {"inline_keyboard": buttons}
    if reply_to: payload["reply_to_message_id"] = reply_to
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=12)
    except Exception:
        pass

def tg_send_photo(chat_id, png_bytes, caption=None, reply_to=None):
    if not TELEGRAM_TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        files = {"photo": ("tbp.png", png_bytes, "image/png")}
        data = {"chat_id": chat_id}
        if caption: data["caption"] = caption
        if reply_to: data["reply_to_message_id"] = reply_to
        requests.post(url, data=data, files=files, timeout=30)
    except Exception:
        pass

def tg_send_dice(chat_id, emoji="🏀"):
    if not TELEGRAM_TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDice"
        requests.post(url, data={"chat_id": chat_id, "emoji": emoji}, timeout=10)
    except Exception:
        pass

# =========================
# TELEGRAM WEBHOOK
# =========================
@app.route("/telegram", methods=["POST"])
def telegram():
    u = request.json or {}
    m = u.get("message", {}) or {}
    txt = (m.get("text") or "").strip()
    chat_id = m.get("chat", {}).get("id")
    mid = m.get("message_id")
    if not chat_id or not txt: return jsonify({"ok": True})

    prefs = get_prefs(chat_id)
    low = txt.lower()

    # language quick switches
    if low in ("english","englisch","/lang en"):
        prefs["lang"] = "en"; tg_send(chat_id,"Okay, English only. 🇬🇧",reply_to=mid); return jsonify({"ok":True})
    if low in ("deutsch","german","/lang de"):
        prefs["lang"] = "de"; tg_send(chat_id,"Alles klar, nur Deutsch. 🇩🇪",reply_to=mid);  return jsonify({"ok":True})

    # tone
    if low.startswith("/tone"):
        if "fun" in low: prefs["tone"]="fun"; tg_send(chat_id,"Humor an. 🐸✨",reply_to=mid)
        else:            prefs["tone"]="pro"; tg_send(chat_id,"Sachlich aktiv. ✅",reply_to=mid)
        return jsonify({"ok":True})

    # autopost controls
    if low.startswith("/autopost"):
        parts = low.split()
        if len(parts)>=2 and parts[1]=="on":
            minutes = int(parts[2]) if len(parts)>=3 and parts[2].isdigit() else 60
            start_autopost(chat_id, minutes)
            tg_send(chat_id, f"🔁 Auto-Posting ON, alle {minutes} min.", reply_to=mid)
        else:
            stop_autopost(chat_id)
            tg_send(chat_id, "⏹ Auto-Posting OFF.", reply_to=mid)
        return jsonify({"ok":True})

    # quick commands
    if low.startswith("/start"):
        tg_send(chat_id,
                f"Hi, ich bin {BOT_NAME}. Befehle: /price /chart /buy /links /img <prompt> /lang de|en /tone pro|fun /autopost on [min]|off",
                reply_to=mid)
        return jsonify({"ok":True})

    if low.startswith("/links") or LINKS_RE.search(low):
        tg_send(chat_id, "Quick Links:",
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
        return jsonify({"ok":True})

    if low.startswith("/buy") or BUY_RE.search(low):
        tg_send(chat_id, "Buy TBP:",
            buttons=[[{"text":"SushiSwap (Polygon)", "url": LINKS["buy"]}]],
            reply_to=mid
        )
        return jsonify({"ok":True})

    if low.startswith("/chart") or CHART_RE.search(low):
        tg_send(chat_id, "Charts:",
            buttons=[
                [{"text":"DexScreener","url": LINKS["dexscreener"]}],
                [{"text":"DEXTools","url": LINKS["dextools"]}],
                [{"text":"GeckoTerminal","url": LINKS["gecko"]}],
            ],
            reply_to=mid
        )
        return jsonify({"ok":True})

    if low.startswith("/price") or PRICE_RE.search(low):
        lang = prefs["lang"] if prefs["lang"]!="auto" else detect_lang(txt)
        tg_send(chat_id, format_price_block(lang), reply_to=mid)
        return jsonify({"ok":True})

    # /img prompt …
    m_img = IMG_CMD_RE.match(txt)
    if m_img:
        prompt = txt.split(" ", 1)[1].strip() if " " in txt else ""
        safe = safe_image_prompt(prompt)
        if not safe:
            tg_send(chat_id, "❌ Prompt blockiert oder leer (NSFW/illegal).", reply_to=mid)
            return jsonify({"ok":True})
        tg_send(chat_id, "🎨 Erzeuge Bild…", reply_to=mid)
        png = openai_image_b64(safe, size="1024x1024")
        if not png:
            tg_send(chat_id, "⚠️ Bildgenerierung fehlgeschlagen.", reply_to=mid)
        else:
            tg_send_photo(chat_id, png, caption="✅ Fertig.", reply_to=mid)
        return jsonify({"ok":True})

    # main answer (no auto-links)
    lang = prefs["lang"] if prefs["lang"]!="auto" else detect_lang(txt)
    ans = route_intent(txt, lang=lang, tone=prefs["tone"], web=False)
    tg_send(chat_id, ans, reply_to=mid)
    return jsonify({"ok":True})

# =========================
# AUTOPOST SCHEDULER
# =========================
def compose_autopost(chat_id):
    """Rotate through 4 lightweight content types."""
    prefs = get_prefs(chat_id)
    lang  = prefs["lang"] if prefs["lang"]!="auto" else "en"
    state = AUTOP.get(chat_id) or {}
    cycle = state.get("cycle", 0) % 4

    buttons = [
        [{"text":"🍣 Buy", "url": LINKS["buy"]},
         {"text":"📊 Chart", "url": LINKS["dexscreener"]}],
        [{"text":"📜 Scan", "url": LINKS["contract"]},
         {"text":"🌐 Site", "url": LINKS["website"]}],
    ]

    if cycle == 0:
        # Live price block
        text = ("📡 Live Update\n" + format_price_block(lang))
    elif cycle == 1:
        # One-liner + CTA
        text = ("🐸 " + one_liner(lang) + ("\nLet’s hop! 🚀" if lang=="en" else "\nLos geht’s! 🚀"))
    elif cycle == 2:
        # Community question
        text = ("Question: What should TBP post next — memes or analytics?" if lang=="en"
                else "Frage: Was soll TBP als Nächstes posten — Memes oder Analysen?")
    else:
        # Mini quiz A/B
        if lang=="en":
            text = "Quick poll: Which DEX do you use more for TBP?\nA) SushiSwap  B) QuickSwap  C) Both"
        else:
            text = "Mini-Umfrage: Welchen DEX nutzt du öfter für TBP?\nA) SushiSwap  B) QuickSwap  C) Beides"

    return text, buttons

def autopost_loop():
    while True:
        try:
            t = now()
            for chat_id, cfg in list(AUTOP.items()):
                if not cfg.get("on"): continue
                if t >= cfg.get("next", 0):
                    text, buttons = compose_autopost(chat_id)
                    tg_send(chat_id, text, buttons=buttons)
                    # kleine Animation ~30% der Fälle
                    if random.random() < 0.3:
                        tg_send_dice(chat_id, emoji=random.choice(["🏀","🎯","🎲"]))
                    cfg["cycle"] = (cfg.get("cycle", 0) + 1) % 4
                    cfg["next"]  = t + cfg.get("interval", 3600)
        except Exception:
            pass
        time.sleep(30)

threading.Thread(target=autopost_loop, daemon=True).start()

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] running on :{port}")
    app.run(host="0.0.0.0", port=port)
