import os, re
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

MODEL  = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
APIKEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=APIKEY)

SYSTEM = (
    "Reply concise, precise, helpful, with light humor. "
    "No promises. If unsure, say so and point to on-chain links. "
    "You are the official assistant for TurboPepe-AI (TBP) on Polygon."
)

@app.get("/")
def health():
    return "ok", 200

@app.post("/ask")
def ask():
    if not APIKEY:
        return jsonify({"error": "Missing OPENAI_API_KEY on server"}), 500

    payload = request.get_json(force=True) or {}
    q   = (payload.get("question") or "").strip()
    ctx = payload.get("context") or []

    if not q:
        return jsonify({"error": "Empty question"}), 400

    # einfache Spracherkennung für DE/EN
    is_de = bool(re.search(r"\b(was|wie|warum|kann|preis|kurs|kosten|listung)\b|[äöüß]", q, re.I))
    lang  = "German" if is_de else "English"

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": f"Context: {ctx}\n\nQuestion: {q}\nAnswer in {lang}."}
    ]

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.4,
            max_tokens=400,
        )
        text = (resp.choices[0].message.content or "").strip()
        return jsonify({"answer": text})
    except Exception as e:
        # gib den echten Fehler an den Client zurück, damit du siehst, was los ist
        return jsonify({"error": str(e)}), 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
