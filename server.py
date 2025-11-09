import os, re, json, time, requests, threading
from flask import Flask, request, jsonify
from flask_cors import CORS

# ==============================
# CONFIG
# ==============================
BOT_NAME = "TBP-AI"
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# TBP Konstanten
PAIR = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi Pair (Polygon)
MAX_SUPPLY = 190_000_000_000
BURNED     = 10_000_000_000
OWNER      = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# Links
LINKS = {
    "website":   "https://quantumpepe.github.io/TurboPepe/",
    "buy":       "https://www.sushi.com/polygon/swap?token0=NATIVE&token1=0x50c40e03552A42fbE41b2507d522F56d7325D1F2",
    "contract":  "https://polygonscan.com/token/0x50c40e03552A42fbE41b2507d522F56d7325D1F2",
    "pool_dext": "https://www.dextools.io/app/en/polygon/pair-explorer/0x945c73101e11cc9e529c839d1d75648d04047b0b",
    "pool_ds":   "https://dexscreener.com/polygon/0x945c73101e11cc9e529c839d1d75648d04047b0b",
    "gecko":     "https://www.geckoterminal.com/en/polygon_pos/pools/0x945c73101e11cc9e529c839d1d75648d04047b0b?embed=1",
    "telegram":  "https://t.me/turbopepe25",
    "x":         "https://x.com/TurboPepe2025",
}

# Speicher/Memory
KB_PATH = "kb.json"
MEM = {"ctx": []}  # Rolling Web-Kontext

app = Flask(__name__)
CORS(app)

# ==============================
# HELPERS
# ==============================

def load_kb():
    try:
        with open(KB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"tg": []}

def save_kb(kb):
    try:
        with open(KB_PATH, "w", encoding="utf-8") as f:
            json.dump(kb, f, ensure_ascii=False, indent=2)
    except:
        pass

def is_de(text):
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum|kurs|preis|kaufen|vertrag)\b", text.lower()))

def sanitize_persona(ans):
    if not ans: return ""
    # Keine NFT-Claims (dein Wunsch)
    if re.search(r"\bNFT\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "", ans, flags=re.I).strip()
    return ans

# ==============================
# Live-Metriken (Dexscreener)
# ==============================

def get_live_metrics():
    """
    Holt Live-Daten von Dexscreener.
    Rückgabe: dict { price, change24h, volume24h, liquidityUsd, marketCap, fdv }
    """
    url = f"https://api.dexscreener.com/latest/dex/pairs/polygon/{PAIR}"
    try:
        r = requests.get(url, timeout=6)
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [None])[0]
        if not pair:
            raise RuntimeError("pair missing")

        price = float(pair.get("priceUsd") or 0)
        change24h = float(pair.get("priceChange", {}).get("h24") or 0)
        volume24h = float(pair.get("volume", {}).get("h24") or 0)
        liq = pair.get("liquidity") or {}
        liquidityUsd = float(liq.get("usd") or 0)
        fdv = float(pair.get("fdv") or 0)

        # Eigene MC-Berechnung (circulating)
        marketCap = price * CIRC_SUPPLY if price > 0 else 0.0

        return {
            "price": price,
            "change24h": change24h,
            "volume24h": volume24h,
            "liquidityUsd": liquidityUsd,
            "fdv": fdv,
            "marketCap": marketCap,
            "supply": {
                "max": MAX_SUPPLY,
                "burned": BURNED,
                "owner": OWNER,
                "circulating": CIRC_SUPPLY,
            }
        }
    except Exception as e:
        return {"error": str(e)}

@app.route("/metrics", methods=["GET"])
def metrics():
    """Frontend holt hier 24h Change / MC / Liquidity / Volume / Price."""
    data = get_live_metrics()
    return jsonify(data), 200

# ==============================
# OpenAI (einfacher Messages-Call)
# ==============================

def build_system():
    return (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Answer bilingual (DE/EN) depending on user.\n"
        "Be bold, confident, slightly competitive — but no insults.\n"
        "No financial advice. No promises.\n"
        "If asked about links, include website/buy/contract/chart/telegram/X.\n"
        f"Links: website={LINKS['website']} | buy={LINKS['buy']} | contract={LINKS['contract']} "
        f"| chart={LINKS['pool_ds']} | telegram={LINKS['telegram']} | x={LINKS['x']}\n"
        "If asked about price/market data, keep it short and remind that live numbers are shown on the page."
    )

def call_openai(question, context):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    msgs = [{"role":"system","content":build_system()}]
    for item in context[-6:]:
        role = "user" if item.startswith("You:") else "assistant"
        msgs.append({"role":role, "content": item.split(":",1)[1].strip()})
    msgs.append({"role":"user","content":question})

    data = {
        "model": MODEL_NAME,
        "messages": msgs,
        "max_tokens": 500,
        "temperature": 0.4,
    }
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions",
                          headers=headers, json=data, timeout=40)
        j = r.json()
        return j["choices"][0]["message"]["content"].strip()
    except Exception:
        return None

def linkify(q, ans):
    need = []
    ql = q.lower()
    if re.search(r"(website|seite)", ql): need += ["website"]
    if re.search(r"(buy|kauf|sushi)", ql): need += ["buy"]
    if re.search(r"(contract|vertrag|scan)", ql): need += ["contract"]
    if re.search(r"(chart|kurs|price)", ql): need += ["chart"]
    if re.search(r"\btelegram\b", ql): need += ["telegram"]
    if re.search(r"\bx\b|\btwitter\b", ql): need += ["x"]

    if not need: 
        return ans

    lines = ["\n\nQuick Links:"]
    if "website" in need:  lines.append(f"• Website: {LINKS['website']}")
    if "buy" in need:      lines.append(f"• Buy: {LINKS['buy']}")
    if "contract" in need: lines.append(f"• Contract: {LINKS['contract']}")
    if "chart" in need:    lines.append(f"• Chart: {LINKS['pool_ds']}")
    if "telegram" in need: lines.append(f"• Telegram: {LINKS['telegram']}")
    if "x" in need:        lines.append(f"• X: {LINKS['x']}")
    return ans + "\n" + "\n".join(lines)

def ai_answer(question):
    ans = call_openai(question, MEM["ctx"]) or "Network glitch. try again 🐸"
    ans = sanitize_persona(ans)
    ans = linkify(question, ans)
    return ans

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer":"empty question"}), 200

    ans = ai_answer(q)
    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]
    return jsonify({"answer": ans}), 200

# ==============================
# Telegram Webhook + Auto-Learning
# ==============================

def tg_send(chat_id, text, reply_to=None):
    try:
        payload = {"chat_id":chat_id, "text":text, "parse_mode":"HTML"}
        if reply_to: payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN')}/sendMessage",
            json=payload, timeout=10
        )
    except:
        pass

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json or {}
    msg = update.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")

    if not (chat_id and text):
        return jsonify({"ok": True})

    low = text.lower()

    # Kommandos
    if low.startswith("/start"):
        tg_send(chat_id, f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP (DE/EN).", msg_id)
        return jsonify({"ok": True})

    # Auto-Learning: Speichere in kb.json
    kb = load_kb()
    kb.setdefault("tg", []).append({"ts": int(time.time()), "chat": chat_id, "text": text})
    kb["tg"] = kb["tg"][-500:]  # Deckel
    save_kb(kb)

    # Antwort mit OpenAI (+ Kontextrückfluss)
    ans = ai_answer(text)
    tg_send(chat_id, ans, msg_id)

    # Lern-Kontext auch für Web
    MEM["ctx"].append(f"You: {text}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"ok": True})

# ==============================
# MAIN
# ==============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
