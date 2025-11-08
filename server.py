import os, json
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # erlaubt Anfragen von GitHub Pages

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")  # Default-Modell

# -------------------------------------------------
#   Chat-Nachricht vorbereiten
# -------------------------------------------------
def build_messages(payload):
    facts = payload.get("facts", {})
    tel  = payload.get("telemetry", {})
    ctx  = payload.get("context", [])
    q    = payload.get("question", "")
    lang = payload.get("user_lang", "en")

    style = (
        "Reply concise, precise, helpful, with light humor. "
        "No empty promises. If unsure, say so and point to on-chain links."
    )
    if lang.startswith("de"):
        style = (
            "Antworte knapp, präzise, hilfreich – mit leichtem Humor. "
            "Keine leeren Versprechen. Wenn unsicher, sag es und verweise auf On-Chain-Links."
        )

    sys = f"""[STYLE]
{style}

[ROLE]
You are the official assistant for TurboPepe-AI (TBP).

[FACTS]
- Chain: {facts.get('chain','Polygon (POL)')}
- Contract: {facts.get('contract','0x50c40e...D1F2')}
- Sushi/Pool: {facts.get('pool','0x945c73...47b0b')}
- Supply: {facts.get('supply_total','190B')} TBP
- Burned: {facts.get('supply_burned','10B')} TBP
- LP: ~{facts.get('supply_lp','130B')} TBP
- Owner (transparent): ~{facts.get('owner_tokens','14B')} TBP (listings/ops)
- Socials: TG {facts.get('tg')} • X {facts.get('x')}

[TELEMETRY]
- Price line: {tel.get('priceline')}
- Market cap line: {tel.get('mcline')}

[POLICY]
- No financial advice. Never invent links. Be transparent if unknown.
"""

    messages = [{"role": "system", "content": sys}]
    for turn in ctx[-8:]:
        if turn.startswith("You:"):
            messages.append({"role": "user", "content": turn[4:].strip()})
        elif turn.startswith("TBP:"):
            messages.append({"role": "assistant", "content": turn[4:].strip()})
    messages.append({"role": "user", "content": q})
    return messages

# -------------------------------------------------
#   Routen
# -------------------------------------------------
@app.route("/", methods=["GET"])
def root():
    return "TBP advisor ok", 200


@app.route("/ask", methods=["POST"])
def ask():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        print("[/ask] incoming:", json.dumps(payload)[:500])  # Logging

        if not os.environ.get("OPENAI_API_KEY"):
            return jsonify({"answer": "Server has no API key configured."}), 500

        messages = build_messages(payload)

        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.4,
            max_tokens=320
        )

        answer = resp.choices[0].message.content.strip()
        print("[/ask] ok, len:", len(answer))
        return jsonify({"answer": answer})

    except Exception as e:
        print("[/ask] error:", repr(e))
        return jsonify({"answer": "Backend hiccup – please try again in a moment."}), 200


if __name__ == "__main__":
    print("[TBP-AI] starting on :10000")
    app.run(host="0.0.0.0", port=10000)
