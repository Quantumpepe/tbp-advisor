# server.py
import os, json
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

client = OpenAI(api_key=OPENAI_API_KEY)

@app.get("/")
def root():
    return "TBP advisor up", 200

@app.get("/health")
def health():
    ok = bool(OPENAI_API_KEY) and bool(MODEL)
    return jsonify(ok=ok, model=MODEL), (200 if ok else 500)

def build_messages(payload):
    question = (payload.get("question") or "").strip()
    ctx = payload.get("context") or []
    user_lang = payload.get("user_lang") or "de"

    system = (
        "Antworte kurz, präzise, hilfreich und mit leichtem Humor. "
        "Keine leeren Versprechen. Wenn unsicher, verweise auf On-Chain-Daten."
    )

    history = "\n".join(ctx[-8:]) if isinstance(ctx, list) else ""

    prompt = f"""Du bist der offizielle Assistent von TurboPepe-AI (TBP).
Sprache: {user_lang}

Kontext:
{history}

Frage des Nutzers:
{question}
"""
    return system, prompt

@app.post("/ask")
def ask():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        system, prompt = build_messages(payload)

        if not OPENAI_API_KEY:
            return jsonify(error="NO_API_KEY"), 500
        if not MODEL:
            return jsonify(error="NO_MODEL"), 500

        resp = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ]
        )

        text = ""
        for item in resp.output or []:
            if getattr(item, "type", "") == "output_text":
                text += item.text

        text = text.strip() or "Ich konnte keine Antwort abrufen. Bitte später erneut versuchen."
        return jsonify(answer=text, model=MODEL), 200

    except Exception as e:
        return jsonify(error="BACKEND_EXCEPTION", detail=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
