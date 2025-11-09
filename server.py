import os
import re
import json
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import threading

# ================================================================
# == CONFIG / LINKS / CONSTANTS ==
# ================================================================

BOT_NAME = "TBP-AI"
MODEL_NAME = "gpt-4o-mini"   # geändert: modern & schnell, funktioniert stabil auf Render
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"

LINKS = {
    "website":      "https://quantumpepe.github.io/TurboPepe/",
    "buy":          f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dextools":     "https://www.dextools.io/app/en/polygon/pair-explorer/0x945c73101e11cc9e529c839d1d75648d04047b0b",
    "dexscreener":  "https://dexscreener.com/polygon/0x945c73101e11cc9e529c839d1d75648d04047b0b",
    "gecko":        "https://www.geckoterminal.com/en/polygon_pos/pools/0x945c73101e11cc9e529c839d1d75648d04047b0b?embed=1",
    "telegram":     "https://t.me/turbopepe25",
    "x":            "https://x.com/TurboPepe2025",
    "contract_scan": f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

# Keyword matcher
KW = {
    "website":  re.compile(r"\b(website|webseite|homepage|site)\b", re.I),
    "telegram": re.compile(r"\b(telegram|tg|gruppe)\b", re.I),
    "x":        re.compile(r"\b(x\.com|twitter)\b", re.I),
    "buy":      re.compile(r"\b(buy|kauf|swap)\b", re.I),
    "contract": re.compile(r"\b(contract|adresse|address|polygonscan)\b", re.I),
    "pool":     re.compile(r"\b(pool|gecko|chart|charts|dextools|dexscreener|lp)\b", re.I),
    "nft":      re.compile(r"\b(nft|tbp\s*gold)\b", re.I)
}

# Memory facts
FACTS = {
    "symbol": "TBP",
    "name": "TurboPepe-AI",
    "chain": "Polygon (POL)",
    "total_supply": "190,000,000,000 TBP",
    "burned": "10,000,000,000 TBP",
    "lp": "130,000,000,000 TBP",
    "owner_tokens": "14,000,000,000 TBP",
    "nft_status": "offline",
    "nft_note": "TBP Gold NFTs sind aktuell offline/unavailable."
}

# ================================================================
# == FLASK SETUP ==
# ================================================================

app = Flask(__name__)
CORS(app)

# Session memory
MEM = {
    "ctx": []
}

# ================================================================
# == UTILITIES ==
# ================================================================

def is_de(text):
    return bool(re.search(r"\b(der|die|das|ich|du|wie|was|warum)\b", text.lower()))

def sanitize_persona(ans):
    if ans is None:
        return ""
    # remove hallucinated NFT mentions when not asked
    if re.search(r"\bNFT\b", ans, re.I):
        ans = re.sub(r"\bNFTs?.*", "", ans, flags=re.I | re.S).strip()
    return ans

def build_system():
    return (
        "You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.\n"
        "Answer bilingual depending on user language (DE/EN).\n"
        "Highlight TBP facts only from provided facts and telemetry.\n"
        "Strong, confident, competitive tone allowed — but no insults or naming competitors.\n"
        "No financial advice. No promises.\n"
        "Do NOT mention NFTs unless user explicitly asks.\n"
        "If user asks about NFTs, mention TBP Gold is offline/unavailable.\n"
        "If user asks about purpose/vision, emphasize AI-driven autonomy, transparency, utility.\n"
    )

def build_links(lang, needs):
    L = {
        "website":   "Website" if lang=="en" else "Webseite",
        "telegram":  "Telegram" if lang=="en" else "Telegram-Gruppe",
        "x":         "X (Twitter)",
        "buy":       "Buy on Sushi" if lang=="en" else "Auf Sushi kaufen",
        "contract":  "Contract (Polygonscan)" if lang=="en" else "Vertrag (Polygonscan)",
        "gecko":     "GeckoTerminal",
        "dextools":  "DEXTools",
        "dexscreener": "DexScreener"
    }

    out = []
    if "website" in needs:  out.append(f"🌐 <a href=\"{LINKS['website']}\">{L['website']}</a>")
    if "buy" in needs:      out.append(f"🛒 <a href=\"{LINKS['buy']}\">{L['buy']}</a>")
    if "contract" in needs: out.append(f"📜 <a href=\"{LINKS['contract_scan']}\">{L['contract']}</a>")
    if "pool" in needs:
        out.append(
            f"📈 <a href=\"{LINKS['gecko']}\">{L['gecko']}</a> • "
            f"<a href=\"{LINKS['dextools']}\">{L['dextools']}</a> • "
            f"<a href=\"{LINKS['dexscreener']}\">{L['dexscreener']}</a>"
        )
    if "telegram" in needs: out.append(f"💬 <a href=\"{LINKS['telegram']}\">{L['telegram']}</a>")
    if "x" in needs:        out.append(f"🕊️ <a href=\"{LINKS['x']}\">{L['x']}</a>")

    if not out:
        return ""

    return "\n\n— Quick Links —\n" + "\n".join(out)

def linkify(q, ans):
    lang = "de" if is_de(q) else "en"
    need = []

    # detect
    for key in KW:
        if key != "nft" and KW[key].search(q):
            need.append(key)

    # generic
    if re.search(r"(what is|was ist|tokenomics|buy|kaufen|chart|preis|kurs)", q, re.I):
        need += ["website","buy","contract","pool","telegram","x"]

    need = list(set(need))
    if not need:
        return ans

    block = build_links(lang, need)
    return ans + block

def enforce_nft_policy(q, ans):
    asked = bool(KW["nft"].search(q))

    if asked:
        suffix = ("🔒 TBP Gold NFTs sind aktuell offline / nicht verfügbar."
                  if is_de(q) else
                  "🔒 TBP Gold NFTs are currently offline / unavailable.")
        if "NFT" not in ans:
            ans += "\n\n" + suffix
        ans += ("\nBitte keine Erwartungen an zukünftige NFT-Rückkehr formulieren."
                if is_de(q) else
                "\nPlease avoid making promises about NFTs returning.")
    else:
        # remove NFT mentions
        if re.search(r"\bNFT\b", ans, re.I):
            ans = re.sub(r"NFT.*", "", ans, flags=re.I).strip() + "."
    return ans

# ================================================================
# == CORE AI ==
# ================================================================

def call_openai(question, context):
    headers = { "Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}" }

    messages = [
        {"role":"system", "content": build_system()},
    ]

    for item in context[-6:]:
        messages.append({"role": "user" if item.startswith("You:") else "assistant",
                         "content": item.split(":",1)[1].strip()})

    messages.append({"role":"user", "content":question})

    data = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.45
    }

    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers=headers, json=data, timeout=40)

    if not r.ok:
        return None

    try:
        out = r.json()["choices"][0]["message"]["content"]
    except:
        out = None
    return out

def ai_answer(question):
    # first try OpenAI
    resp = call_openai(question, MEM["ctx"])
    if not resp:
        resp = "Network glitch… try again 🐸"

    resp = sanitize_persona(resp)
    resp = enforce_nft_policy(question, resp)
    resp = linkify(question, resp)

    return resp


# ================================================================
# == WEB ENDPOINT ==
# ================================================================

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    q = data.get("question","").strip()

    if not q:
        return jsonify({"answer": "empty question"}), 200

    ans = ai_answer(q)

    # save context
    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-10:]

    return jsonify({"answer": ans})


# ================================================================
# == TELEGRAM BOT ==
# ================================================================

def tg_send(chat_id, text, reply_to=None):
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to

        requests.post(
            f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN')}/sendMessage",
            json=payload,
            timeout=10
        )
    except:
        pass

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.json
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    msg_id = message.get("message_id")

    low = text.lower()

    # commands
    if low.startswith("/start"):
        tg_send(chat_id, f"Hi, ich bin {BOT_NAME}. Frag mich alles zu TBP (DE/EN). Tippe /links.", reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id, "Commands: /links, /price, /about. Oder frag normal.", reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        tg_send(chat_id,
            f"🌐 Website: {LINKS['website']}\n"
            f"🛒 Kaufen: {LINKS['buy']}\n"
            f"📜 Contract: {LINKS['contract_scan']}\n"
            f"📈 Charts:\n  - {LINKS['gecko']}\n  - {LINKS['dextools']}\n  - {LINKS['dexscreener']}\n"
            f"💬 Telegram: {LINKS['telegram']}\n"
            f"🕊️ X: {LINKS['x']}",
            reply_to=msg_id)
        return jsonify({"ok": True})

    # NFT query direct
    if KW["nft"].search(low):
        msg = "🔒 TBP Gold NFTs sind aktuell offline/unavailable."
        tg_send(chat_id, msg, reply_to=msg_id)
        return jsonify({"ok": True})

    # normal flow
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
    port = int(os.environ.get("PORT", 10000))
    print(f"[TBP-AI] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
