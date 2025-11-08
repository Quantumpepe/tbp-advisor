import os, json
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def build_messages(payload):
    facts = payload.get("facts", {})
    tel   = payload.get("telemetry", {})
    ctx   = payload.get("context", []) or []
    q     = payload.get("question", "")
    lang  = payload.get("user_lang","en")

    style = ("Antworte knapp, präzise, hilfreich, mit leichtem Humor. "
             "Keine leeren Versprechen. Wenn unsicher, sag es und verweise auf On-Chain-Links.") \
            if lang=="de" else \
            ("Reply concise, precise, helpful, with light humor. "
             "No empty promises. If unsure, say so and point to on-chain links.")

    sys = f"""[STYLE]
{style}

[ROLE]
You are the official assistant for TurboPepe-AI (TBP).

[FACTS]
- Chain: {facts.get('chain')}
- Contract: {facts.get('contract')}
- Sushi/Pool: {facts.get('pool')}
- Total supply: {facts.get('supply_total'):,} TBP
- Burned: {facts.get('supply_burned'):,} TBP
- LP: ~{facts.get('supply_lp'):,} TBP
- Owner (transparent): ~{facts.get('owner_tokens'):,} TBP (listings/ops)
- NFT: {facts.get('nft')}
- Socials: TG {facts.get('tg')} • X {facts.get('x')}

[TELEMETRY]
- Price line: {tel.get('priceLine')}
- Market cap line: {tel.get('mcLine')}

[SAFETY]
No financial advice. Be accurate, non-hype, and transparent."""
    messages = [{"role":"system","content":sys}]

    # kurzer Verlauf einbetten (bis 8 Turns)
    for turn in ctx[-8:]:
        if turn.startswith("You:"):
            messages.append({"role":"user","content":turn.replace("You:","").strip()})
        elif turn.startswith("TBP:"):
            messages.append({"role":"assistant","content":turn.replace("TBP:","").strip()})

    messages.append({"role":"user","content":q})
    return messages

@app.route("/ask", methods=["POST"])
def ask():
    try:
        payload = request.get_json(force=True)
        messages = build_messages(payload)

        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL","gpt-4o-mini"),
            messages=messages,
            temperature=0.6,
            top_p=0.9,
            frequency_penalty=0.3,
            presence_penalty=0.0,
            max_tokens=400
        )
        answer = resp.choices[0].message.content.strip()
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"answer": None, "error": str(e)}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
