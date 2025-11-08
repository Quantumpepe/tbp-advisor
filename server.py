import os, json, traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app)

# ---- Config & OpenAI client ----
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")
API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=API_KEY)

def build_messages(payload):
    # Kontext/Fakten optional – sicher parsen
    facts = payload.get("facts", {}) or {}
    tel   = payload.get("telemetry", {}) or {}
    ctx   = payload.get("context", []) or []
    q     = payload.get("question", "") or ""
    lang  = payload.get("user_lang", "en") or "en"

    style = (
        "Reply concise, precise, helpful, with light humor. "
        "No empty promises. If unsure, say so and point to on-chain links."
    )
    if lang.lower().startswith("de"):
        style = (
            "Antworte knapp, präzise, hilfreich, mit leichtem Humor. "
            "Keine leeren Versprechen. Bei Unsicherheit, sag es und verweise auf On-Chain-Links."
        )

    sys = f"""[STYLE]
{style}

[ROLE]
You are the official assistant for TurboPepe-AI (TBP).

[FACTS]
- Chain: {facts.get('chain','Polygon (POL)')}
- Contract: {facts.get('contract','0x50c40e03552A42fbE41b2507d522F56d7325D1F2')}
- Sushi Pool: {facts.get('pool','0x945c73101e11cc9e529c839d1d75648d04047b0b')}
- Total supply: {facts.get('supply_total','190,000,000,000')} TBP
- Burned: {facts.get('burned','10,000,000,000')} TBP
- LP (approx): {facts.get('lp','130,000,000,000')} TBP
- Owner (transparent): {facts.get('owner_tokens','14,000,000,000')} TBP
- TG: {facts.get('tg','https://t.me/turbopepe25')}
- X:  {facts.get('x','https://x.com/TurboPepe2025')}

[POLICY]
- Do NOT mention TBP Gold NFTs (page removed).
- No financial advice.
"""

    messages = [{"role":"system","content":sys}]
    if ctx:
        for turn in ctx[-8:]:
            if isinstance(turn, str):
                # Kontext als „role: content“ Strings?
                if turn.strip().lower().startswith("you:"):
                    messages.append({"role":"user","content":turn[4:].strip()})
                elif turn.strip().lower().startswith("tbp:"):
                    messages.append({"role":"assistant","content":turn[4:].strip()})
            elif isinstance(turn, dict) and "role" in turn and "content" in turn:
                messages.append(turn)

    messages.append({"role":"user","content":q})
    return messages

@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "model": OPENAI_MODEL}), 200

@app.route("/ask", methods=["POST","OPTIONS"])
def ask():
    if request.method == "OPTIONS":
        return ("", 200)

    try:
        payload = request.get_json(force=True, silent=True) or {}
        question = (payload.get("question") or "").strip()
        if not question:
            return jsonify({"answer":"Please send a question."}), 200

        msgs = build_messages(payload)

        # ---- OpenAI call (Responses API über Chat-compat) ----
        # Nutzt das neue SDK, gibt stabilen Text zurück
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=msgs,
            temperature=0.4,
            max_tokens=350
        )
        answer = resp.choices[0].message.content.strip()

        print(f"[/ask] ok, len={len(answer)}")
        return jsonify({"answer": answer}), 200

    except Exception as e:
        # LOGS für Render
        print("[/ask] ERROR:", repr(e))
        traceback.print_exc()
        # Saubere User-Antwort (kein „hiccup“ mehr)
        return jsonify({
            "answer": "Server gerade beschäftigt oder Key/Modell nicht akzeptiert. "
                      "Bitte in 5–10 Sekunden nochmal versuchen. 🧠💚"
        }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"[TBP-AI] starting on :{port} (model={OPENAI_MODEL})")
    app.run(host="0.0.0.0", port=port, debug=False)
