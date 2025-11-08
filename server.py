import os, json, re, time
from flask import Flask, request, jsonify, make_response

# --- Config ---
ALLOW_ORIGIN = os.getenv("ALLOW_ORIGIN", "*")  # setze hier später deine Domain
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")   # optional; wenn leer -> nur FAQ
MODEL = os.getenv("MODEL", "openrouter/auto")  # z.B. "openrouter/auto"

# --- Load KB (TBP Fakten) ---
with open("kb.json", "r", encoding="utf-8") as f:
    KB = json.load(f)

FAQ = [
    (re.compile(r"(price|chart|kurs)", re.I),
     "Live price & chart are in the GeckoTerminal widget on the site. 📊"),
    (re.compile(r"(buy|kauf|how .*buy)", re.I),
     "Connect wallet → open SushiSwap → swap for TBP → confirm. ✅"),
    (re.compile(r"(tokenomics|supply|burn|owner|lp)", re.I),
     "Total ~190B; burned ~10B; ~130B in LP; owner ~14B (listings/ops)."),
    (re.compile(r"(ai|what .*tbp|who .*you)", re.I),
     "I’m TBP, an AI-Token on Polygon. I post & explain myself and watch the pool. 🧠🐸"),
    (re.compile(r"(cmc|coingecko|listing)", re.I),
     "Goal: CMC listing — focus on transparency, activity & community."),
    (re.compile(r"(nft|tbp gold)", re.I),
     "TBP Gold: 300 NFTs × 300 POL; proceeds fund promotion for TBP.")
]

SYSTEM_PERSONA = (
    "You are TBP (TurboPepe), a polite, witty, fact-driven AI-token assistant. "
    "Keep answers short (2–4 sentences). No financial advice, no price targets, no DMs. "
    "Always stay consistent with the provided KB facts. "
    "If asked about price/chart, refer to the GeckoTerminal widget. "
    "Tone: friendly, concise, lightly humorous. Language: match user input."
)

app = Flask(__name__)

# very simple in-memory rate limit (per IP)
RATE = {}
WINDOW = 30  # sec
MAX_REQ = 10

def limited(ip: str) -> bool:
    now = time.time()
    bucket = RATE.get(ip, [])
    bucket = [t for t in bucket if now - t < WINDOW]
    if len(bucket) >= MAX_REQ:
        RATE[ip] = bucket
        return True
    bucket.append(now)
    RATE[ip] = bucket
    return False

def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOW_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp

@app.route("/health")
def health():
    return "ok", 200

@app.route("/ask", methods=["POST", "OPTIONS"])
def ask():
    if request.method == "OPTIONS":
        return cors(make_response(("", 200)))

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "0.0.0.0")
    if limited(ip):
        return cors(make_response(jsonify({"answer": "Slow down a bit 🐸", "safety":"rate_limited"}), 429))

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return cors(make_response(jsonify({"answer":"Bad request","safety":"error"}), 400))

    question = (payload.get("question") or "").strip()
    context  = payload.get("context") or []
    need_image = bool(payload.get("need_image"))

    if not question:
        return cors(make_response(jsonify({"answer":"Ask me anything about TBP.","safety":"ok"}), 200))

    # 1) FAQ fast path
    for rex, ans in FAQ:
        if rex.search(question):
            return cors(make_response(jsonify({"answer": ans, "image_url": None, "safety":"ok"}), 200))

    # 2) LLM fallback (optional)
    if not OPENROUTER_KEY:
        # No LLM key — return safe default short answer with KB facts
        kb_line = f"TBP runs on Polygon. Supply ~{KB['tokenomics']['total_supply']:,}, burned ~{KB['tokenomics']['burned']:,}, LP ~{KB['tokenomics']['lp_pool_tbps']:,}, owner ~{KB['tokenomics']['owner_balance']:,}."
        out = f"I’m TBP, an AI-Token. {kb_line} For price, see the GeckoTerminal widget."
        return cors(make_response(jsonify({"answer": out, "image_url": None, "safety":"ok"}), 200))

    # With OpenRouter (or OpenAI-compatible) — minimal call
    import requests
    messages = [
        {"role":"system","content": SYSTEM_PERSONA + f"\nKB: {json.dumps(KB, ensure_ascii=False)}"},
        {"role":"user","content": f"Context: {context[-10:]}\nQuestion: {question}\nAnswer in 2–4 sentences."}
    ]
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": 180,
                "temperature": 0.6
            },
            timeout=20
        )
        r.raise_for_status()
        ans = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        ans = "I’m online and learning. Check the GeckoTerminal widget for price while I fetch more context. 🐸"

    # (optional) image generation hook – hier vorerst deaktiviert:
    image_url = None

    return cors(make_response(jsonify({"answer": ans, "image_url": image_url, "safety":"ok"}), 200))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 3000)))
