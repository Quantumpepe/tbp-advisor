import os, json, re, time
from typing import Dict, Any, Tuple
import requests
from flask import Flask, request, jsonify, make_response

# ----------------- Config -----------------
ALLOW_ORIGIN   = os.getenv("ALLOW_ORIGIN", "*")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "")    # optional; if empty -> no LLM fallback
MODEL          = os.getenv("MODEL", "openrouter/auto")
DEX_PAIR       = os.getenv("DEX_PAIR", "0x945c73101e11cc9e529c839d1d75648d04047b0b")  # Sushi POL TBP pair

# ----------------- Load KB -----------------
with open("kb.json", "r", encoding="utf-8") as f:
    KB: Dict[str, Any] = json.load(f)

TOKEN = KB["project"]["name"]
TOK   = "TBP"

TOTAL = int(KB["tokenomics"]["total_supply"])
BURN  = int(KB["tokenomics"]["burned"])
OWNER = int(KB["tokenomics"]["owner_balance"])
LP_TB = int(KB["tokenomics"]["lp_pool_tbps"])

# strict circulating (exkl. burned + owner)
CIRC  = max(TOTAL - BURN - OWNER, 0)

# ----------------- App & simple rate limit -------------
app = Flask(__name__)
RATE: Dict[str, list] = {}
WINDOW=30; MAX_REQ=20

def limited(ip: str) -> bool:
    now=time.time()
    q=RATE.get(ip,[])
    q=[t for t in q if now-t<WINDOW]
    if len(q)>=MAX_REQ:
        RATE[ip]=q; return True
    q.append(now); RATE[ip]=q; return False

def cors(resp):
    resp.headers["Access-Control-Allow-Origin"]=ALLOW_ORIGIN
    resp.headers["Access-Control-Allow-Headers"]="Content-Type"
    resp.headers["Access-Control-Allow-Methods"]="POST, OPTIONS, GET"
    return resp

@app.route("/health")
def health(): return "ok", 200

# ----------------- Helpers: language & intents ----------
RE_PRICE   = re.compile(r"\b(price|kurs|preis|chart|mc|market ?cap|fdv|lp|liquidity|pool)\b", re.I)
RE_BUY     = re.compile(r"\b(buy|kaufen|how.*buy|swap|kauf)\b", re.I)
RE_SUPPLY  = re.compile(r"\b(supply|angebot|total|burn|burned|owner|circulating|umlauf)\b", re.I)
RE_NFT     = re.compile(r"\b(nft|gold)\b", re.I)
RE_LINK    = re.compile(r"\b(link|website|telegram|twitter|x|gecko|dextools|contract|adresse|address)\b", re.I)
RE_ROADMAP = re.compile(r"\b(roadmap|ziel|goal|milestone|listing|cmc|coingecko)\b", re.I)
RE_AI      = re.compile(r"\b(ai|ki|what.*tbp|wer bist du|who are you)\b", re.I)
RE_SECURITY= re.compile(r"\b(security|sicherheit|lock|burn proof|renounce|rug|audit)\b", re.I)

def is_german(text:str)->bool:
    t=text.lower()
    return any(x in t for x in [" der "," die "," das "," und "," oder ","nicht","wie","preis","kaufen","warum","wann","wo","wer","was","mit","ich","du","wir","euch","euro","franken","kurs"])

def fmt_int(n:int)->str:
    return f"{n:,}".replace(",", " ")

def fetch_dex_pair(pair_addr:str)->Tuple[float, Dict[str, Any]]:
    """Return (price_usd, raw_pair_dict). price_usd=0.0 if unknown."""
    url=f"https://api.dexscreener.com/latest/dex/pairs/polygon/{pair_addr}"
    try:
        r=requests.get(url, timeout=12)
        r.raise_for_status()
        data=r.json().get("pair") or {}
        price=float(data.get("priceUsd") or 0)
        return price, data
    except Exception:
        return 0.0, {}

def compute_caps(price:float)->Tuple[float,float]:
    """(circulating_mc, fdv)"""
    return price*CIRC, price*TOTAL

def persona(lang_de:bool)->str:
    base = (
        "You are TBP (TurboPepe), an AI-token assistant. "
        "Be concise (2–4 sentences), factual, slightly humorous (🐸), no financial advice, no price predictions. "
        "Use the provided KB facts; if asked about live price/MC/LP, prefer tool results. "
    )
    if lang_de:
        base += "Antwortsprache: Deutsch."
    else:
        base += "Answer in English."
    return base

def llm_answer(prompt:str, lang_de:bool)->str:
    if not OPENROUTER_KEY:
        # Fallback minimal message if no LLM key configured
        if lang_de:
            return "Ich bin TBP, ein AI-Token auf Polygon. Für Live-Preis/Chart siehe das GeckoTerminal-Widget unten. Frag mich nach Tokenomics, Sicherheit oder wie man kauft 🐸."
        return "I’m TBP, an AI-token on Polygon. For live price/chart see the GeckoTerminal widget below. Ask me about tokenomics, security, or how to buy 🐸."
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type":"application/json"},
            json={
                "model": MODEL,
                "messages":[
                    {"role":"system","content": persona(lang_de) + f" KB: {json.dumps(KB, ensure_ascii=False)}"},
                    {"role":"user","content": prompt}
                ],
                "max_tokens": 220,
                "temperature": 0.6
            },
            timeout=25
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ("Ich lerne noch weiter – schau dir unten den Live-Chart an. 🐸"
                if lang_de else
                "Still learning — check the live chart below meanwhile. 🐸")

def answer_price(lang_de:bool)->str:
    price, pair = fetch_dex_pair(DEX_PAIR)
    if price<=0:
        return ("Preis/Chart derzeit nicht verfügbar. Versuche es gleich erneut. 🐸"
                if lang_de else
                "Price/chart currently unavailable. Try again shortly. 🐸")
    circ, fdv = compute_caps(price)
    lp_usd = pair.get("liquidity",{}).get("usd")
    price_s = f"${price:.12f}"
    circ_s  = f"${circ:,.2f}"
    fdv_s   = f"${fdv:,.2f}"
    lp_s    = f"${lp_usd:,.0f}" if lp_usd else "—"
    if lang_de:
        return f"Aktueller Preis: {price_s}. Geschätzte MC (Umlauf): {circ_s}, FDV: {fdv_s}. Liquidity: {lp_s}. Live-Chart unten auf der Seite. 🐸"
    return f"Current price: {price_s}. Est. circulating MC: {circ_s}, FDV: {fdv_s}. Liquidity: {lp_s}. Live chart below. 🐸"

def answer_buy(lang_de:bool)->str:
    link = KB["project"]["links"]["website"]
    sushi = f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={KB['project'].get('contract','0x50c40e03552A42fbE41b2507d522F56d7325D1F2')}"
    if lang_de:
        return f"Wallet verbinden (Polygon) → {sushi} öffnen → MATIC zu {TOK} tauschen → bestätigen. Contract: {KB['project'].get('contract','—')}. Website: {link}"
    return f"Connect wallet (Polygon) → open {sushi} → swap MATIC to {TOK} → confirm. Contract: {KB['project'].get('contract','—')}. Website: {link}"

def answer_supply(lang_de:bool)->str:
    if lang_de:
        return (f"Total: {fmt_int(TOTAL)} {TOK}, verbrannt: {fmt_int(BURN)}, LP ca.: {fmt_int(LP_TB)}, "
                f"Owner ca.: {fmt_int(OWNER)}, Umlauf (≈Total−Burn−Owner): {fmt_int(CIRC)}.")
    return (f"Total: {fmt_int(TOTAL)} {TOK}, burned: {fmt_int(BURN)}, LP ~{fmt_int(LP_TB)}, "
            f"owner ~{fmt_int(OWNER)}, circulating (≈Total−Burn−Owner): {fmt_int(CIRC)}.")

def answer_links(lang_de:bool)->str:
    links=KB["project"]["links"]
    extras=[]
    if lang_de:
        extras.append(f"Website: {links.get('website','—')}")
        extras.append(f"Telegram: {links.get('telegram','—')}")
        extras.append(f"X/Twitter: {links.get('x','—')}")
        extras.append(f"Chart: {links.get('dex_widget','—')}")
        extras.append(f"Contract: {KB['project'].get('contract','—')}")
        return " • ".join(extras)
    else:
        extras.append(f"Website: {links.get('website','—')}")
        extras.append(f"Telegram: {links.get('telegram','—')}")
        extras.append(f"X/Twitter: {links.get('x','—')}")
        extras.append(f"Chart: {links.get('dex_widget','—')}")
        extras.append(f"Contract: {KB['project'].get('contract','—')}")
        return " • ".join(extras)

def answer_nft(lang_de:bool)->str:
    n=KB["nft"]
    if lang_de:
        return f"TBP Gold NFT: {n['count']} Stück à {n['price_per_nft_POL']} POL – Erlös fließt in Promotion. Link: https://app.manifold.xyz/c/turbopepe-gold"
    return f"TBP Gold NFT: {n['count']} supply at {n['price_per_nft_POL']} POL each — proceeds fund promotion. Link: https://app.manifold.xyz/c/turbopepe-gold"

def answer_roadmap(lang_de:bool)->str:
    if lang_de:
        return ("Ziele: AI-Bot voll live, Transparenz/Community, CoinGecko & CMC Listing. "
                "Danach Utility: Escrow-Zahlungen (Freigabe nach Leistung), In-App Wallet & Chat, Partnerschaften. 🐸")
    return ("Goals: AI bot fully live, transparency/community, CoinGecko & CMC listings. "
            "Next: escrow payments (release-on-approval), in-app wallet & chat, partnerships. 🐸")

def answer_ai(lang_de:bool)->str:
    if lang_de:
        return "Ich bin TBP, ein AI-Token auf Polygon. Ich poste Memes, beantworte Fragen und überwache Pool/Stats. Für Preis/Chart nutze das Widget unten. 🐸"
    return "I’m TBP, an AI-token on Polygon. I post memes, answer questions, and watch pool/stats. For price/chart use the widget below. 🐸"

def answer_security(lang_de:bool)->str:
    burn_tx = "https://polygonscan.com/tx/0x6cd24c5c4f8376961e21aa892b966329c09d4fa7490e699e1ce26765459ddf1a"
    if lang_de:
        return f"Sicherheit: Contract verifiziert, LP geburnt/gelockt (siehe Nachweis: {burn_tx}). Keine Steuern. Owner-Bestand transparent (~{fmt_int(OWNER)} {TOK}) für Listings/Operations."
    return f"Security: Contract verified, LP burned/locked (proof: {burn_tx}). No taxes. Owner balance transparent (~{fmt_int(OWNER)} {TOK}) for listings/ops."

# ----------------- Main endpoint -----------------------
@app.route("/ask", methods=["POST","OPTIONS"])
def ask():
    if request.method=="OPTIONS":
        return cors(make_response(("",200)))
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "0.0.0.0")
    if limited(ip):
        return cors(make_response(jsonify({"answer": "Slow down a bit 🐸", "safety":"rate_limited"}), 429))

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return cors(make_response(jsonify({"answer":"Bad request","safety":"error"}),400))

    q = (payload.get("question") or "").strip()
    context = payload.get("context") or []
    lang_de = is_german(q + " " + " ".join(context))

    if not q:
        out = "Frag mich etwas über TBP (Preis, Tokenomics, Sicherheit, Kaufen, Roadmap). 🐸" if lang_de \
              else "Ask me anything about TBP (price, tokenomics, security, how to buy, roadmap). 🐸"
        return cors(make_response(jsonify({"answer": out, "safety":"ok"}), 200))

    # intent routing with live tools
    if RE_PRICE.search(q):    return cors(make_response(jsonify({"answer":answer_price(lang_de)}),200))
    if RE_SUPPLY.search(q):   return cors(make_response(jsonify({"answer":answer_supply(lang_de)}),200))
    if RE_BUY.search(q):      return cors(make_response(jsonify({"answer":answer_buy(lang_de)}),200))
    if RE_LINK.search(q):     return cors(make_response(jsonify({"answer":answer_links(lang_de)}),200))
    if RE_NFT.search(q):      return cors(make_response(jsonify({"answer":answer_nft(lang_de)}),200))
    if RE_ROADMAP.search(q):  return cors(make_response(jsonify({"answer":answer_roadmap(lang_de)}),200))
    if RE_AI.search(q):       return cors(make_response(jsonify({"answer":answer_ai(lang_de)}),200))
    if RE_SECURITY.search(q): return cors(make_response(jsonify({"answer":answer_security(lang_de)}),200))

    # fallback to LLM (optional)
    prompt = f"Context: {context[-10:]}\nQuestion: {q}\nRespond succinctly."
    out = llm_answer(prompt, lang_de)
    return cors(make_response(jsonify({"answer": out, "safety":"ok"}),200))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 3000)))
