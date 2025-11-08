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
    user_lang = payload.get("user_lang") or "en"

    system = (
        "Reply concise, precise, helpful, with light humor.\n"
        "No empty promises. If unsure, say so and point to on-chain links."
    )
    # einfache Kontext-Zusammenführung
    history = "\n".join(ctx[-8:]) if isinstance(ctx, list) else ""

    prompt = f"""You are the official assistant for TurboPepe-AI (TBP).
Language: {user_lang}

Context:
{history}

User question:
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
                {"role":"system", "content": system},
                {"role":"user",   "content": prompt}
            ]
        )
        # Text robust extrahieren (Responses API)
        text = ""
        for item in resp.output or []:
            if getattr(item, "type", "") == "output_text":
                text += item.text

        text = text.strip() or "I couldn’t retrieve data right now. Please try again."
        return jsonify(answer=text, model=MODEL), 200

    except Exception as e:
        # für Logs UND saubere Frontend-Fehleranzeige
        return jsonify(error="BACKEND_EXCEPTION", detail=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
