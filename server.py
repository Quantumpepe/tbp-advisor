# server.py — TBP-AI + C-BoostAI (Dual-System, Full PRO Version)
# Service: tbp-advisor
# -*- coding: utf-8 -*-

import os, re, json, time, threading, random
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# ================================================================
# CONFIG
# ================================================================

SERVICE_NAME = "tbp-advisor"

BOT_NAME_TBP    = "TBP-AI"
BOT_NAME_CBOOST = "C-BoostAI"

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

TELEGRAM_TOKEN_TBP    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_TOKEN_CBOOST = os.environ.get("TELEGRAM_BOT_TOKEN1", "").strip()

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "").strip()
ADMIN_USER_IDS = [x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()]

# Wird per /id gesetzt
CBOOST_CHAT_ID = int(os.environ.get("CBOOST_CHAT_ID", "0") or "0")

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"

LINKS = {
    "website":      "https://quantumpepe.github.io/TurboPepe/",
    "buy":          f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dexscreener":  f"https://dexscreener.com/polygon/{TBP_PAIR}",
    "dextools":     f"https://www.dextools.io/app/en/polygon/pair-explorer/{TBP_PAIR}",
    "contract_scan":f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

# ================================================================
# MEMORY — GLOBAL (pro Token getrennt)
# ================================================================

MEM_TBP = {
    "ctx": [],
    "last_autopost": None,
    "chat_count": 0,
    "resp_mode": "0",
    "resp_counter": {}
}

MEM_CBOOST = {
    "ctx": [],
    "chat_count": 0,
    "resp_mode": "0",
    "resp_counter": {}
}

# ================================================================
# UTIL
# ================================================================

def is_de(txt: str) -> bool:
    if not txt:
        return False
    return bool(re.search(r"\b(der|die|das|und|warum|wie|kann|preis|kurs)\b", txt.lower()))

def say(lang, de, en):
    return de if lang == "de" else en

def fmt_usd(x, digits=2):
    try:
        return f"${float(x):,.{digits}f}"
    except:
        return "N/A"

def is_admin(uid):
    return True if not ADMIN_USER_IDS else str(uid) in ADMIN_USER_IDS

def choose_token(chat_id: int):
    """C-Boost Chat → C-Boost Bot, sonst TBP Bot"""
    if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID and TELEGRAM_TOKEN_CBOOST:
        return TELEGRAM_TOKEN_CBOOST
    return TELEGRAM_TOKEN_TBP

# ================================================================
# TELEGRAM SEND
# ================================================================

def tg_send(chat_id, text, reply_to=None, preview=True):
    token = choose_token(chat_id)
    if not token:
        return
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10
        )
    except:
        pass

def tg_send_any(chat_id, text):
    """Hilfreich für /id vor Setzen der Chat-ID"""
    for token in [TELEGRAM_TOKEN_TBP, TELEGRAM_TOKEN_CBOOST]:
        if not token:
            continue
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10
            )
        except:
            pass

def tg_buttons(chat_id, text, buttons):
    token = choose_token(chat_id)
    if not token:
        return
    kb = {"inline_keyboard": [[{"text": t, "url": u} for (t, u) in buttons]]}
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": kb,
                "disable_web_page_preview": True
            },
            timeout=10
        )
    except:
        pass

# ================================================================
# PRICE DATA
# ================================================================

def get_live_price():
    # GeckoTerminal first
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}",
            timeout=6
        )
        j = r.json()
        v = j["data"]["attributes"]["base_token_price_usd"]
        return float(v)
    except:
        pass

    # DexScreener fallback
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        j = r.json()
        v = j.get("pair", {}).get("priceUsd") or j["pairs"][0]["priceUsd"]
        return float(v)
    except:
        return None

# ================================================================
# OPENAI — TBP / C-Boost getrennt
# ================================================================

def call_openai_tbp(question, mem):
    if not OPENAI_API_KEY:
        return "OpenAI key missing"

    system = (
        "You are TBP-AI, the assistant for TurboPepe (TBP) on Polygon.\n"
        "Detect language (DE or EN) and answer ONLY in that language.\n"
        "Short, friendly, factual. No financial advice.\n"
        "If asked about future: keep expectations realistic.\n"
    )

    msgs = [{"role":"system","content":system}]
    for item in mem["ctx"][-6:]:
        role, txt = item.split(":",1)
        msgs.append({"role": role.lower(), "content": txt.strip()})
    msgs.append({"role":"user","content":question})

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENAI_API_KEY}"},
            json={"model":OPENAI_MODEL,"messages":msgs,"max_tokens":280,"temperature":0.4},
            timeout=40
        )
        return r.json()["choices"][0]["message"]["content"]
    except:
        return "Network glitch. Try again."

def call_openai_cboost(question, mem):
    if not OPENAI_API_KEY:
        return "OpenAI key missing"

    # Website soll EN only beantworten → erzwingen:
    system = (
        "You are C-BoostAI, the official assistant for the C-Boost micro supply token on Polygon.\n"
        "ALWAYS answer in English.\n"
        "Key facts:\n"
        "- 5,000,000 total supply\n"
        "- Fair launch, no team allocation\n"
        "- Community-focused\n"
        "Tone: energetic but realistic. No financial advice.\n"
        "If asked about TBP: say you only handle C-Boost.\n"
    )

    msgs = [{"role":"system","content":system}]
    for item in mem["ctx"][-6:]:
        role, txt = item.split(":",1)
        msgs.append({"role": role.lower(), "content": txt.strip()})
    msgs.append({"role":"user","content":question})

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENAI_API_KEY}"},
            json={"model":OPENAI_MODEL,"messages":msgs,"max_tokens":280,"temperature":0.4},
            timeout=40
        )
        return r.json()["choices"][0]["message"]["content"]
    except:
        return "Network error. Try again."

# ================================================================
# FLASK APP
# ================================================================

app = Flask(__name__)
CORS(app)

@app.route("/")
def root():
    return jsonify({"ok":True,"service":SERVICE_NAME})

@app.route("/health")
def health():
    return jsonify({"ok":True})

# ================================================================
# WEBSITE ENDPOINTS
# ================================================================

@app.route("/ask", methods=["POST"])
def ask_tbp():
    data = request.json or {}
    q = data.get("question","").strip()
    if not q:
        return jsonify({"answer":"Empty question"})

    # detect language
    lang = "de" if is_de(q) else "en"

    if re.search(r"\b(price|kurs|preis)\b", q.lower()):
        p = get_live_price()
        if p:
            ans = say(lang,"Preis: "+fmt_usd(p,12),"Price: "+fmt_usd(p,12))
        else:
            ans = say(lang,"Preis nicht verfügbar.","Price unavailable.")
    else:
        ans = call_openai_tbp(q, MEM_TBP)

    MEM_TBP["ctx"].append(f"User: {q}")
    MEM_TBP["ctx"].append(f"Assistant: {ans}")
    MEM_TBP["ctx"] = MEM_TBP["ctx"][-10:]

    return jsonify({"answer":ans})

@app.route("/ask_cboost", methods=["POST"])
def ask_cboost():
    data = request.json or {}
    q = data.get("question","").strip()
    if not q:
        return jsonify({"answer":"Empty question"})

    ans = call_openai_cboost(q, MEM_CBOOST)
    MEM_CBOOST["ctx"].append(f"User: {q}")
    MEM_CBOOST["ctx"].append(f"Assistant: {ans}")
    MEM_CBOOST["ctx"] = MEM_CBOOST["ctx"][-10:]

    return jsonify({"answer":ans})

# ================================================================
# TELEGRAM BOT
# ================================================================

MEME_CAPTIONS_TBP = [
    "Nice photo! Want a TBP meme? 🐸⚡",
    "Fresh pixels! Shall I boost it? 😎",
    "Clean drop — Turbo mode? 🚀"
]

MEME_CAPTIONS_CBOOST = [
    "Boost-ready image detected! ⚡",
    "Wanna make this a C-Boost meme? 🚀",
    "Sharp pic — ready to boost the timeline? 😏"
]

@app.route("/telegram", methods=["POST"])
def telegram():
    update = request.json or {}
    msg = update.get("message",{}) or {}
    chat = msg.get("chat",{}) or {}
    chat_id = chat.get("id")
    user_id = msg.get("from",{}).get("id")
    text = (msg.get("text") or "").strip()
    msg_id = msg.get("message_id")

    if not chat_id:
        return jsonify({"ok":True})

    # Detect C-Boost or TBP
    is_cboost = (CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID)

    # Memory selector
    mem = MEM_CBOOST if is_cboost else MEM_TBP

    # Photos
    if "photo" in msg:
        cap = random.choice(MEME_CAPTIONS_CBOOST if is_cboost else MEME_CAPTIONS_TBP)
        tg_send(chat_id, cap, reply_to=msg_id)
        mem["chat_count"] += 1
        return jsonify({"ok":True})

    if not text:
        return jsonify({"ok":True})

    low = text.lower()

    # /id
    if low.startswith("/id"):
        tg_send_any(chat_id, f"Chat ID: <code>{chat_id}</code>")
        return jsonify({"ok":True})

    # /start
    if low.startswith("/start"):
        if is_cboost:
            tg_send(chat_id,
                    "Hi, I'm C-BoostAI 🤖 — ask me anything about C-Boost (English only). No financial advice.",
                    reply_to=msg_id)
        else:
            tg_buttons(chat_id,
                       "Hi, I'm TBP-AI. Ask me anything about TBP. 🚀",
                       [("Chart",LINKS["dexscreener"]),("Buy",LINKS["buy"])]
            )
        return jsonify({"ok":True})

    # /price
    if low.startswith("/price") or re.search(r"\b(price|kurs|preis)\b", low):
        p = get_live_price()
        if not is_cboost:
            if p:
                tg_buttons(chat_id, f"Price: {fmt_usd(p,12)}",
                           [("Chart",LINKS["dexscreener"]),("Buy",LINKS["buy"])])
            else:
                tg_send(chat_id,"Price unavailable.")
        else:
            tg_send(chat_id,"C-Boost price coming at launch.")
        return jsonify({"ok":True})

    # Regular AI
    if is_cboost:
        ans = call_openai_cboost(text, MEM_CBOOST)
    else:
        ans = call_openai_tbp(text, MEM_TBP)

    tg_send(chat_id, ans, reply_to=msg_id)

    mem["ctx"].append(f"User: {text}")
    mem["ctx"].append(f"Assistant: {ans}")
    mem["ctx"] = mem["ctx"][-10:]

    return jsonify({"ok":True})

# ================================================================
# MAIN
# ================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{SERVICE_NAME}] running on port {port}")
    app.run(host="0.0.0.0", port=port)
