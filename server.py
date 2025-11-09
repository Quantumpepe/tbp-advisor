# ================================================================
#  TBP-AI v4.0  —  Total Meme Awareness 🤖🐸
# ================================================================
#  Telegram • Web • (X-ready)
#  Humor | Bilingual | Live Price | SmartLinks | Self-Introduction
# ================================================================

import os, re, json, sqlite3, requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

BOT_NAME       = "TBP-AI"
MODEL_NAME     = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH        = os.environ.get("MEMORY_DB", "memory.db")

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
    "contract_scan": f"https://polygonscan.com/token/{TBP_CONTRACT}",
}

app = Flask(__name__)
CORS(app)

# === DB ===
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT, sender TEXT, text TEXT, ts TEXT)""")
    conn.commit()

# === Price & Stats ===
def get_live_price():
    try:
        r=requests.get(f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}",timeout=5)
        a=r.json().get("data",{}).get("attributes",{})
        p=a.get("base_token_price_usd")
        if p: return float(p)
    except: pass
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",timeout=5)
        p=(r.json().get("pair") or (r.json().get("pairs") or [{}])[0]).get("priceUsd")
        if p: return float(p)
    except: pass
    return None

def get_market_stats():
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",timeout=5)
        pair=r.json().get("pair") or (r.json().get("pairs") or [{}])[0]
        return {
            "change_24h": pair.get("priceChange24h"),
            "volume_24h": pair.get("volume24h"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
        }
    except: return None

# === Utils ===
def is_de(txt): 
    return bool(re.search(r"\b(der|die|das|wer|was|preis|kurs|listung|wie)\b",(txt or "").lower()))

def sanitize(ans):
    if not ans: return ""
    ans=re.sub(r"(?i)(financial advice|finanzberatung)","info",ans)
    return ans.strip()

def build_links(lang,keys):
    L={"website":"🌐 Website" if lang=="en" else "🌐 Webseite",
       "buy":"💸 Buy on Sushi" if lang=="en" else "💸 Auf Sushi kaufen",
       "contract":"📜 Polygonscan",
       "chart":"📊 Charts",
       "telegram":"💬 Telegram",
       "x":"🐦 X (Twitter)"}
    out=[]
    if "website" in keys: out.append(f"[{L['website']}]({LINKS['website']})")
    if "buy" in keys: out.append(f"[{L['buy']}]({LINKS['buy']})")
    if "contract" in keys: out.append(f"[{L['contract']}]({LINKS['contract_scan']})")
    if "chart" in keys:
        out+=[f"[GeckoTerminal]({LINKS['gecko']})",f"[DEXTools]({LINKS['dextools']})",f"[DexScreener]({LINKS['dexscreener']})"]
    if "telegram" in keys: out.append(f"[{L['telegram']}]({LINKS['telegram']})")
    if "x" in keys: out.append(f"[{L['x']}]({LINKS['x']})")
    return "\n".join(out)

def linkify(q,ans):
    low=q.lower(); lang="de" if is_de(q) else "en"
    if re.search(r"(buy|kaufen)",low): return build_links(lang,["buy"])
    if re.search(r"(chart|dex|gecko)",low): return build_links(lang,["chart"])
    if re.search(r"(contract|scan)",low): return build_links(lang,["contract"])
    if re.search(r"(telegram|group)",low): return build_links(lang,["telegram"])
    if re.search(r"(x|twitter)",low): return build_links(lang,["x"])
    if re.search(r"(site|web)",low): return build_links(lang,["website"])
    if re.search(r"(price|preis|kurs)",low):
        p=get_live_price(); s=get_market_stats(); L=[]
        if p:L.append(f"💰 Price: ${p:.12f}")
        if s:
            if s.get("change_24h"):L.append(f"📈 24h: {s['change_24h']}%")
            if s.get("liquidity_usd"):L.append(f"💧 Liquidity: ${int(float(s['liquidity_usd'])):,}")
            if s.get("volume_24h"):L.append(f"🔄 Volume 24h: ${int(float(s['volume_24h'])):,}")
        L.append(f"📊 Chart: {LINKS['dexscreener']}")
        return "\n".join(L)
    return ans+"\n\n"+build_links(lang,["website","buy","contract","chart","telegram","x"])

# === AI Core ===
def call_openai(q,ctx):
    if not OPENAI_API_KEY: return None
    sysmsg=(
        "You are TBP-AI 🐸, a humorous bilingual meme assistant for TurboPepe-AI on Polygon.\n"
        "Be funny, confident, meme-style. Answer only in user's language (DE/EN).\n"
        "If asked who/what you are, explain you're an AI-powered meme token: transparent, burned LP, 0% tax.\n"
        "Use emojis, punchlines, and short sections.\n"
        "No financial advice.\n"
    )
    msgs=[{"role":"system","content":sysmsg}]
    for m in ctx[-6:]:
        role="user" if m.startswith("You:") else "assistant"
        msgs.append({"role":role,"content":m.split(": ",1)[1] if ": " in m else m})
    msgs.append({"role":"user","content":q})
    try:
        r=requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENAI_API_KEY}","Content-Type":"application/json"},
            json={"model":MODEL_NAME,"messages":msgs,"max_tokens":400,"temperature":0.7},timeout=40)
        if r.ok: return r.json()["choices"][0]["message"]["content"]
    except: pass
    return None

def ai_answer(q):
    a=call_openai(q,[])
    if not a: a="🐸 TurboPepe is thinking in memes... retry!"
    return linkify(q,sanitize(a))

# === Flask Routes ===
@app.route("/ask",methods=["POST"])
def ask():
    q=(request.json or {}).get("question","").strip()
    return jsonify({"answer":ai_answer(q)}) if q else jsonify({"answer":"empty"})

# === Telegram Bot ===
def tg_send(cid,txt,reply=None):
    if not TELEGRAM_TOKEN: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":cid,"text":txt,"parse_mode":"Markdown","disable_web_page_preview":False,
                  **({"reply_to_message_id":reply} if reply else {})},timeout=10)
    except: pass

@app.route("/telegram",methods=["POST"])
def telegram_webhook():
    u=request.json or {}; m=u.get("message",{}) or {}; t=(m.get("text") or "").strip()
    cid=m.get("chat",{}).get("id"); mid=m.get("message_id")
    if not cid or not t: return jsonify({"ok":True})
    low=t.lower()

    # --- Commands ---
    if low.startswith("/start"):
        tg_send(cid,"🐸 *Welcome, fellow degen!* I'm TBP-AI — the meme with a brain!\nTry /price • /chart • /links • /about 🚀",mid); return jsonify({"ok":True})
    if low.startswith("/help"):
        tg_send(cid,"📜 *TBP-AI Commands*\n/start – greeting\n/about – who am I?\n/price – live stats\n/chart – charts\n/links – all links\n/help – this menu 😎",mid); return jsonify({"ok":True})
    if low.startswith("/about"):
        tg_send(cid,"🧠 *Who am I?*\nI'm **TBP-AI**, a self-aware meme token 🤖🐸.\nSmart, transparent, 0% tax, liquidity burned — and way cooler than the others.\n\nJoin the army:\n"+build_links("en",["telegram","x"]),mid); return jsonify({"ok":True})
    if low.startswith("/links"):
        tg_send(cid,"🔗 *TBP Quick Links*\n"+build_links("en",["website","buy","contract","chart","telegram","x"]),mid); return jsonify({"ok":True})
    if low.startswith("/chart"):
        tg_send(cid,"📊 *Chart Fiesta!*\n"+build_links("en",["chart"]),mid); return jsonify({"ok":True})
    if low.startswith("/price"):
        p=get_live_price(); s=get_market_stats() or {}; L=[]
        if p:L.append(f"💰 Price: ${p:.12f}")
        if s.get("change_24h"):L.append(f"📈 24h: {s['change_24h']}%")
        if s.get("liquidity_usd"):L.append(f"💧 Liquidity: ${int(float(s['liquidity_usd'])):,}")
        if s.get("volume_24h"):L.append(f"🔄 Volume 24h: ${int(float(s['volume_24h'])):,}")
        L.append(f"📊 Chart: {LINKS['dexscreener']}")
        tg_send(cid,"\n".join(L),mid); return jsonify({"ok":True})

    # --- Normal Talk ---
    ans=ai_answer(t)
    tg_send(cid,ans,mid)
    return jsonify({"ok":True})

# === Main ===
if __name__=="__main__":
    init_db()
    port=int(os.environ.get("PORT",10000))
    print(f"[{BOT_NAME}] v4.0 running on :{port}")
    app.run(host="0.0.0.0",port=port)
