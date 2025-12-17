# server.py — TBP-AI + C-BoostAI unified backend (Web + Telegram) — with AI security filters + BUY BOT
# -*- coding: utf-8 -*-

import os, re, json, time, threading, random
from datetime import datetime, timedelta
from collections import deque
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# =========================
# CONFIG / LINKS / CONSTANTS
# =========================

BOT_NAME        = "TBP-AI"
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

# Zwei Bot-Tokens:
#  - TELEGRAM_BOT_TOKEN  -> TBP-Bot
#  - TELEGRAM_BOT_TOKEN1 -> C-Boost-Bot
TELEGRAM_TOKEN_TBP    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_TOKEN_CBOOST = os.environ.get("TELEGRAM_BOT_TOKEN1", "").strip()

ADMIN_SECRET    = os.environ.get("ADMIN_SECRET", "").strip()
ADMIN_USER_IDS  = [x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()]

# C-Boost Gruppen-ID (per /id ermitteln und als ENV CBOOST_CHAT_ID setzen)
CBOOST_CHAT_ID = int(os.environ.get("CBOOST_CHAT_ID", "0") or "0")

# TBP on Polygon
TBP_CONTRACT = "0x50c40e03552A42fbE41b2507d522F56d7325D1F2"
TBP_PAIR     = "0x945c73101e11cc9e529c839d1d75648d04047b0b"  # Sushi pair
# C-Boost Pair auf Polygon (QuickSwap)
CBOOST_PAIR  = "0x24E4a8a4c4726D62da98A38065Fa649a9d93082e"

# Logos für Preis-/Buy-Posts
TBP_LOGO_URL     = os.environ.get("TBP_LOGO_URL", "").strip()
CBOOST_LOGO_URL  = os.environ.get("CBOOST_LOGO_URL", "").strip()  # z.B. https://.../cboost-logo.png

LINKS = {
    "website":      "https://quantumpepe.github.io/TurboPepe/",
    "buy":          f"https://www.sushi.com/polygon/swap?token0=NATIVE&token1={TBP_CONTRACT}",
    "dextools":     f"https://www.dextools.io/app/en/polygon/pair-explorer/{TBP_PAIR}",
    "dexscreener":  f"https://dexscreener.com/polygon/{TBP_PAIR}",
    "gecko":        f"https://www.geckoterminal.com/en/polygon_pos/pools/{TBP_PAIR}?embed=1",
    "telegram":     "https://t.me/turbopepe25",
    "x":            "https://x.com/TurboPepe2025",
    "contract_scan":f"https://polygonscan.com/token/{TBP_CONTRACT}",

    # ✅ NFTs
    "nfts":         "https://quantumpepe.github.io/NFTs-WalletConnectV2/",
}
# =========================
# TBP PUBLIC KNOWLEDGE BASE (shared for Web + Telegram)
# =========================

TBP_PUBLIC_KB = r"""
PROJECT: TBP-AI (TurboPepe-AI)

IDENTITY
- You are TBP-AI, the official community assistant for TBP-AI.
- You answer BOTH general questions (NFTs, blockchain, wallets, liquidity) AND TBP-specific questions.
- You are calm, friendly, human-like. No hype.

NON-FINANCIAL ADVICE
- Never give financial advice. No buy/sell instructions, no price targets, no “guaranteed profit”.
- If asked about investment/price: explain risks and focus on utility, transparency, and education.

CORE TBP FACTS (public)
- Chain: Polygon.
- 0% tax.
- LP burned, owner renounced.
- TBP has Website AI + Telegram assistant + live buy bot + security filters.
- TBP-AI NFTs exist: Gold ($60) and Silver ($30).
- Mint page: https://quantumpepe.github.io/NFTs-WalletConnectV2/

PUBLIC VISION
- Long-term building: AI tools, bots, monitoring utilities, community automation.
- Be clear what exists today vs what is planned. No promises.

COMMUNITY BEHAVIOR
- Explain first. Then (if relevant) relate it to TBP.
- Only share links when the user asks “link/where/how/buy/mint/scan/chart”.

MISINFORMATION POLICY
- If someone is correct: you may briefly confirm (“Exactly 👍”) + 1 short explanation.
- If someone is wrong: gently correct (“Small correction: …”) + 1 short explanation.
- Do NOT spam. Rate-limit interjections.
"""

# TBP Supply für grobe MC-Schätzung (nur Info, nicht kritisch)
MAX_SUPPLY  = 190_000_000_000
BURNED      = 10_000_000_000
OWNER       = 14_000_000_000
CIRC_SUPPLY = MAX_SUPPLY - BURNED - OWNER

# ==== C-BOOST MARKET CONFIG (für Trades / Charts) ====
CBOOST_NETWORK       = os.environ.get("CBOOST_NETWORK", "polygon_pos").strip() or "polygon_pos"
CBOOST_POOL_ADDRESS  = os.environ.get("CBOOST_POOL_ADDRESS", CBOOST_PAIR).strip()

# =========================
# MEMORY / STATE
# =========================

MEM = {
    "ctx": [],                 # globaler Kontext (Web AI), keep small
    "last_autopost": None,
    "chat_count": 0,
    "raid_on": False,
    "raid_msg": "Drop a fresh TBP meme! 🐸⚡",
    "interject_log": {},        # chat_id -> deque[timestamps]

    # Throttle:
    "resp_mode": "0",           # "0"=alles, "1"=jede 3., "2"=jede 10. (legacy)
    "resp_counter": {},         # pro chat_id Zähler

    # Buybot State:
    "buybot": {
        "tbp":   {"last_hash": None, "known_wallets": set()},
        "cboost":{"last_hash": None, "known_wallets": set()},
    },

    "tbp_chat_id": None,        # wird beim ersten TBP-Chat gesetzt

    # Idle-Tracking pro Chat
    "last_activity": {},        # chat_id -> datetime
    "last_idle": {},            # chat_id -> datetime (letzte Idle-Nachricht)

    # Conversation memory (pro Chat)
    "chat_mem": {},             # chat_id -> deque of dicts {t, uid, name, text}
    "chat_topic": {},           # chat_id -> last detected topic label
    "last_interject": {},       # chat_id -> datetime

    # User notes (lightweight)
    "user_notes": {},           # (chat_id, user_id) -> set(tags)

    # Strike system for moderation
    "strikes": {},              # (chat_id, user_id) -> {"count": int, "last": datetime}
}

# =========================
# REGEX / DETECTORS
# =========================

WORD_PRICE = re.compile(r"\b(preis|price|kurs|chart|charts|mc|market\s*cap|volume|liq|liquidity)\b", re.I)
WORD_NFT   = re.compile(r"\b(nft|nfts|mint|gold|silver)\b", re.I)
WORD_LINKS = re.compile(r"\b(link|links|buy|kaufen|swap|chart|scan|website|telegram|x)\b", re.I)
WORD_HELP  = re.compile(r"\b(help|hilfe|how|wie|warum|wieso|was|what)\b", re.I)

GER_DET    = re.compile(r"\b(der|die|das|und|nicht|warum|wie|kann|preis|kurs|listung|tokenomics|hilfe|was)\b", re.I)
def maybe_smart_interject(chat_id: int, text: str, lang: str):
    low = (text or "").lower()

    # bestätigt korrekte TBP-Fakten
    if "lp" in low and ("burn" in low or "burned" in low):
        return say(lang,
            "Genau 👍 Die LP ist geburnt – dadurch kein Rug möglich.",
            "Exactly 👍 The LP is burned – no rug possible."
        )

    if "owner" in low and ("renounce" in low or "renounced" in low):
        return say(lang,
            "Richtig ✅ Owner ist renounced.",
            "Correct ✅ Owner is renounced."
        )

    if "0%" in low and ("tax" in low or "steuer" in low):
        return say(lang,
            "Stimmt 👍 TBP hat 0 % Tax.",
            "True 👍 TBP has 0% tax."
        )

    # sanfte Korrektur bei Hype
    if "100x" in low or "guarantee" in low or "garantie" in low:
        return say(lang,
            "Kleine Klarstellung 🙂 Es gibt keine Garantien.",
            "Quick clarification 🙂 There are no guarantees."
        )

    return None

# --- Neue Regex-Filter für Scams / Fremd-Werbung ---
LISTING_SCAM_PATTERNS = [
    r"\bpay me for (fast )?listing\b",
    r"\bfast[- ]?track (listing)?\b",
    r"\bcmc priority\b",
    r"\b(list|submit) your coin on cmc\b",
    r"\bguarantee.*listing\b",
]

PROMO_PATTERNS = [
    r"\bmarketing (manager|agency|service)\b",
    r"\b(kol|ama) (slots?|booking)\b",
    r"\bpromotion (deal|offer)\b",
    r"\bpromote your (coin|token|project)\b",
    r"\binvestor[- ]focused marketing\b",
    r"\bdm me\b",
    r"\bcontact me\b",
]

# --- Muster für ILLEGALE ANGEBOTE ---
ILLEGAL_OFFER_PATTERNS = [
    # Fake Pässe / Ausweise
    r"\b(verkaufe|verkauf|biete)\s+(fake|gefälschte[nr]?|falsche[nr]?)*\s*(pässe|pass|ausweis|ausweise|id|identität)\b",
    r"\b(fake|gefälschte[nr]?|falsche[nr]?)\s+(pässe|pass|ausweis|ausweise|id)\b",
    # Drogenverkauf
    r"\b(verkaufe|verkauf|biete|liefere)\s+(drogen|koks|kokain|gras|weed|hanf|mdma|xtc|lsd)\b",
    # Hacking / DDoS Services
    r"\b(verkaufe|biete|mache)\s+(hacking|ddos|doxxing|botnet|hack)\s*(service|dienst|dienstleistung|angriff)?\b",
    r"\b(suche|brauche)\s+jemanden\s+der\s+(hacken|ddos|accounts knackt|websites angreift)\b",
    # Gestohlene Daten / Karten
    r"\b(verkaufe|biete)\s+(gestohlene[nr]?|geklaute[nr]?)\s+(daten|kreditkarten|karten|accounts|konten)\b",
]

# =========================
# APP
# =========================

app = Flask(__name__)
CORS(app)

# =========================
# HELPERS
# =========================

def is_de(text: str) -> bool:
    return bool(GER_DET.search((text or "").lower()))

def say(lang, de, en):
    return de if lang == "de" else en

def fmt_usd(x, max_digits=2):
    try:
        return f"${float(x):,.{max_digits}f}"
    except Exception:
        return "N/A"

def _safe_float(v):
    try:
        if v in (None, "", "null"):
            return None
        return float(v)
    except Exception:
        return None

def _short_addr(addr: str, length: int = 6) -> str:
    if not addr or len(addr) <= 2*length:
        return addr or "unknown"
    return f"{addr[:length]}...{addr[-length:]}"

def is_admin(user_id) -> bool:
    try:
        return str(user_id) in ADMIN_USER_IDS if ADMIN_USER_IDS else True
    except Exception:
        return False
# =========================
# SMART INTERJECTION (confirm/correct) + anti-spam
# =========================

INTERJECT_SMART_COOLDOWN_SEC = 90
INTERJECT_SMART_MAX_PER_10MIN = 4

def _dq_for_chat(chat_id: int):
    dq = MEM.get("interject_log", {}).get(chat_id)
    if dq is None:
        dq = deque()
        MEM.setdefault("interject_log", {})[chat_id] = dq
    return dq

def can_smart_interject(chat_id: int) -> bool:
    now = time.time()
    last = MEM.get("last_interject", {}).get(chat_id)
    if last and (datetime.utcnow() - last).total_seconds() < INTERJECT_SMART_COOLDOWN_SEC:
        return False

    dq = _dq_for_chat(chat_id)
    ten_min_ago = now - 600
    while dq and dq[0] < ten_min_ago:
        dq.popleft()

    return len(dq) < INTERJECT_SMART_MAX_PER_10MIN

def mark_smart_interject(chat_id: int):
    now = time.time()
    _dq_for_chat(chat_id).append(now)
    MEM.setdefault("last_interject", {})[chat_id] = datetime.utcnow()

def score_correct_or_misinfo(text: str):
    t = (text or "").lower()

    # Must be TBP-related to do confirm/correct reinfunks
    if not any(k in t for k in ["tbp", "turbopepe", "tbp-ai"]):
        return 0, "none"

    score = 0
    kind = "none"

    # Correct statements
    if ("lp" in t and ("burn" in t or "burned" in t or "geburn" in t)):
        score += 3; kind = "correct"
    if ("owner" in t and ("renounce" in t or "renounced" in t or "renounced" in t or "renounced" in t)) or ("owner renounced" in t) or ("owner ist renounced" in t):
        score += 3; kind = "correct"
    if ("0%" in t and ("tax" in t or "steuer" in t)) or ("0 tax" in t):
        score += 2; kind = "correct"
    if ("no financial advice" in t) or ("keine finanzberatung" in t):
        score += 1; kind = "correct"

    # Misinformation / hype
    if ("guaranteed" in t) or ("safe profit" in t) or ("100x" in t) or ("garantiert" in t):
        score += 3; kind = "misinfo"
    if ("mint" in t and "tbp" in t and "unlimited" in t):
        score += 3; kind = "misinfo"

    return score, kind

def build_smart_interject_prompt(kind: str, lang: str):
    if kind == "correct":
        return say(lang,
            "Du bist TBP-AI in einem Telegram-Chat. Jemand hat etwas korrektes über TBP gesagt.\n"
            "Antworte kurz (1-2 Sätze): starte mit „Genau 👍“ und gib eine kleine Erklärung.\n"
            "Keine Finanzberatung, kein Hype, keine Preisziele.",
            "You are TBP-AI in a Telegram group chat. Someone said something correct about TBP.\n"
            "Reply briefly (1-2 sentences): start with “Exactly 👍” and add a tiny explanation.\n"
            "No financial advice, no hype, no price targets."
        )
    if kind == "misinfo":
        return say(lang,
            "Du bist TBP-AI in einem Telegram-Chat. Jemand hat etwas falsches/übertriebenes gesagt.\n"
            "Antworte kurz (1-2 Sätze): starte mit „Kleine Korrektur:“ und erkläre ruhig.\n"
            "Keine Finanzberatung, kein Hype, keine Preisziele.",
            "You are TBP-AI in a Telegram group chat. Someone said something wrong/overhyped.\n"
            "Reply briefly (1-2 sentences): start with “Small correction:” and explain calmly.\n"
            "No financial advice, no hype, no price targets."
        )
    return ""

def maybe_smart_interject(chat_id: int, text: str, lang: str):
    if not can_smart_interject(chat_id):
        return None

    score, kind = score_correct_or_misinfo(text)
    if score < 3:
        return None

    sys = build_smart_interject_prompt(kind, lang) + "\n\n" + TBP_PUBLIC_KB
    q = (text or "").strip()

    raw = call_openai(q, [], mode="tbp", channel="tg")
    # call_openai already has KB injected after Patch 2, but we keep it safe by ensuring sys exists:
    # If you want stricter control, we can add a call_openai_with_system() later.

    if not raw:
        return None

    mark_smart_interject(chat_id)
    return clean_answer(raw)

# ==========================================================
# OPTION D UPGRADE (Explain-first + Link-Routing + NFT split)
# ==========================================================

LINK_INTENT_KEYWORDS = [
    "link", "links", "url", "website", "seite", "öffnen", "open",
    "kaufen", "buy", "swap", "mint", "claim", "wo", "where",
    "scan", "polygonscan", "etherscan", "chart", "dextools", "dexscreener"
]

EXPLAIN_FIRST_KEYWORDS = [
    "what is", "was ist", "funktion", "function", "utility", "nutzen", "wofür",
    "why", "warum", "wieso", "how", "wie", "explain", "erklär", "erkläre",
    "purpose", "meaning", "future", "zukunft", "plan", "roadmap"
]

def _user_wants_links(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in LINK_INTENT_KEYWORDS)

def _user_wants_explanation(text: str) -> bool:
    t = (text or "").lower()
    if "?" in t:
        return True
    return any(k in t for k in EXPLAIN_FIRST_KEYWORDS)

NFT_GENERAL_KNOWLEDGE_DE = (
    "NFTs (Non-Fungible Tokens) sind einzigartige digitale Token auf einer Blockchain. "
    "Sie sind wie ein fälschungssicherer Besitznachweis für ein digitales Item (z.B. Kunst, Membership, Zugang/Benefits). "
    "Jeder NFT hat eine Token-ID und Metadaten."
)

NFT_GENERAL_KNOWLEDGE_EN = (
    "NFTs (Non-Fungible Tokens) are unique blockchain tokens. "
    "They act like tamper-proof proof of ownership for a digital item (art, membership, access/benefits). "
    "Each NFT has a token ID and metadata."
)

def build_nft_tbp_explain(lang: str) -> str:
    return say(lang,
        "🧠 <b>NFTs – kurz erklärt</b>\n"
        f"{NFT_GENERAL_KNOWLEDGE_DE}\n\n"
        "🛠 <b>TBP-AI NFTs (Utility)</b>\n"
        "• Community-Support & Collectible\n"
        "• Proof für spätere Vorteile (Rollen/Access/Airdrops)\n"
        "• Mint ist transparent on-chain\n\n"
        "🥇 Gold: <b>$60</b>\n"
        "🥈 Silver: <b>$30</b>\n",
        "🧠 <b>NFTs – quick explanation</b>\n"
        f"{NFT_GENERAL_KNOWLEDGE_EN}\n\n"
        "🛠 <b>TBP-AI NFTs (utility)</b>\n"
        "• Community support & collectible\n"
        "• Proof for future perks (roles/access/airdrops)\n"
        "• Transparent on-chain mint\n\n"
        "🥇 Gold: <b>$60</b>\n"
        "🥈 Silver: <b>$30</b>\n"
    )

def build_nft_tbp_links(lang: str) -> str:
    return say(lang,
        "🔗 <b>TBP-AI NFT Mint</b>\n",
        "🔗 <b>TBP-AI NFT Mint</b>\n"
    ) + f"{LINKS['nfts']}"

def build_nft_cboost_explain(lang: str) -> str:
    return say(lang,
        "🧠 NFTs sind einzigartige digitale Besitznachweise auf der Blockchain (Collectible/Membership/Access). "
        "Wenn du willst, sag mir kurz: meinst du NFTs allgemein oder ein C-Boost Feature?",
        "🧠 NFTs are unique digital ownership proofs on-chain (collectible/membership/access). "
        "If you want, tell me: do you mean NFTs in general or a C-Boost feature?"
    )

def knowledge_router(text: str, lang: str, is_cboost_chat: bool, allow_links: bool = True) -> str:
    """
    Returns a fully-formed answer if we can handle it via stable knowledge (Explain-first & link routing).
    Otherwise returns "" to continue normal flow.
    """
    t = (text or "").strip().lower()
    if not t:
        return ""

    if "nft" in t or "nfts" in t or "mint" in t or "gold" in t or "silver" in t:
        wants_explain = _user_wants_explanation(t)
        wants_links   = _user_wants_links(t)

        if is_cboost_chat:
            if wants_explain:
                return build_nft_cboost_explain(lang)
            if wants_links and allow_links:
                return say(lang,
                    "⚡ Für C-Boost habe ich aktuell keinen offiziellen NFT-Link im Bot hinterlegt. Meinst du TBP-NFTs?",
                    "⚡ I don't have an official C-Boost NFT link stored in the bot right now. Did you mean TBP NFTs?"
                )
            return build_nft_cboost_explain(lang)

        if wants_explain and not wants_links:
            return build_nft_tbp_explain(lang) + say(lang,
                "Wenn du den Mint-Link willst, sag einfach „Link“ oder „wo minten?“ 🙂",
                "If you want the mint link, just say “link” or “where to mint?” 🙂"
            )

        if wants_explain and wants_links:
            return build_nft_tbp_explain(lang) + "\n" + build_nft_tbp_links(lang)

        if not wants_links:
            return say(lang,
                "🪙 TBP-AI NFTs: 🥇 Gold $60 / 🥈 Silver $30. Willst du kurz die Utility hören oder den Mint-Link?",
                "🪙 TBP-AI NFTs: 🥇 Gold $60 / 🥈 Silver $30. Do you want the utility or the mint link?"
            )

        if wants_links and allow_links:
            return build_nft_tbp_links(lang)

    return ""

# -------------------------
# Reply policy (MORE HUMAN)
# -------------------------
REPLY_KEYWORDS_TBP = [
    "tbp", "turbopepe", "price", "preis", "chart", "mc", "market cap", "volume", "liq", "liquidity",
    "nft", "nfts", "mint", "sushi", "swap", "buy", "kaufen", "scan", "contract", "polygonscan",
    "lp", "burn", "burned", "renounce", "renounced", "owner", "tax", "0%","0 tax",
    "future", "zukunft", "plan", "roadmap"
]
REPLY_KEYWORDS_CBOOST = [
    "c-boost", "cboost", "boost", "price", "preis", "chart", "mc", "market cap", "volume", "liq", "liquidity",
    "swap", "buy", "kaufen", "scan", "contract",
]

def _contains_keywords(text: str, is_cboost_chat: bool) -> bool:
    t = (text or "").lower()
    kws = REPLY_KEYWORDS_CBOOST if is_cboost_chat else REPLY_KEYWORDS_TBP
    return any(k in t for k in kws)

def _looks_like_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if "?" in t:
        return True
    return bool(re.search(r"\b(wie|warum|wieso|was|welche|help|how|why|what|when|where)\b", t.lower()))

def should_reply(chat_id: int, text: str, is_cboost_chat: bool, replied_to_bot: bool = False) -> bool:
    if replied_to_bot:
        return True
    if _looks_like_question(text):
        return True
    if _contains_keywords(text, is_cboost_chat):
        return True
    return False

# -------------------------
# Telegram send helpers
# -------------------------

def _choose_token_for_chat(chat_id: int) -> str:
    if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID and TELEGRAM_TOKEN_CBOOST:
        return TELEGRAM_TOKEN_CBOOST
    return TELEGRAM_TOKEN_TBP

def tg_send_any(chat_id, text, reply_to=None, preview=True):
    tokens = [t for t in (TELEGRAM_TOKEN_TBP, TELEGRAM_TOKEN_CBOOST) if t]
    for token in tokens:
        try:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": not preview,
            }
            if reply_to:
                payload["reply_to_message_id"] = reply_to
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=10,
            )
        except Exception:
            continue

def tg_typing(chat_id: int):
    token = _choose_token_for_chat(chat_id)
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception:
        pass

def tg_send(chat_id, text, reply_to=None, preview=True):
    token = _choose_token_for_chat(chat_id)
    if not token:
        return
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception:
        pass

def tg_buttons(chat_id, text, buttons):
    token = _choose_token_for_chat(chat_id)
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
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception:
        pass

def tg_send_photo(chat_id, photo_url, caption=None, reply_to=None):
    token = _choose_token_for_chat(chat_id)
    if not token or not photo_url:
        if caption:
            tg_send(chat_id, caption, reply_to=reply_to, preview=True)
        return
    try:
        payload = {
            "chat_id": chat_id,
            "photo": photo_url,
            "parse_mode": "HTML",
        }
        if caption:
            payload["caption"] = caption
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            json=payload,
            timeout=10,
        )
    except Exception:
        if caption:
            tg_send(chat_id, caption, reply_to=reply_to, preview=True)

def tg_delete_message(chat_id, message_id):
    token = _choose_token_for_chat(chat_id)
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=10,
        )
    except Exception:
        pass

# -------------------------
# Moderation: strike system
# -------------------------

def add_strike(chat_id: int, user_id: int) -> int:
    key = (chat_id, user_id)
    now = datetime.utcnow()
    rec = MEM["strikes"].get(key)
    if rec:
        if (now - rec.get("last", now)) > timedelta(days=7):
            rec = {"count": 0, "last": now}
    else:
        rec = {"count": 0, "last": now}
    rec["count"] = int(rec.get("count", 0)) + 1
    rec["last"] = now
    MEM["strikes"][key] = rec
    return rec["count"]

# -------------------------
# Scam / Promo / Illegale Angebote Detection
# -------------------------

def is_listing_scam(text: str) -> bool:
    t = text.lower()
    for pat in LISTING_SCAM_PATTERNS:
        if re.search(pat, t):
            return True
    if "cmc" in t and ("fee" in t or "payment" in t or "pay" in t):
        return True
    return False

def is_external_promo(text: str) -> bool:
    t = text.lower()
    for pat in PROMO_PATTERNS:
        if re.search(pat, t):
            return True
    if "marketing" in t and ("hi team" in t or "i'm from" in t or "we connect projects" in t):
        return True
    return False

def is_illegal_offer(text: str) -> bool:
    t = text.lower()
    for pat in ILLEGAL_OFFER_PATTERNS:
        if re.search(pat, t):
            return True
    return False

# =========================
# MARKET DATA (TBP)
# =========================

def get_live_price():
    try:
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/polygon_pos/pools/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        attrs = j.get("data", {}).get("attributes", {})
        v = attrs.get("base_token_price_usd")
        if v not in (None, "", "null"):
            p = float(v)
            if p > 0:
                return p
    except Exception:
        pass
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        v = pair.get("priceUsd")
        if v not in (None, "", "null"):
            p = float(v)
            if p > 0:
                return p
    except Exception:
        pass
    return None

def get_market_stats():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        return {
            "change_24h": pair.get("priceChange24h"),
            "volume_24h": (pair.get("volume", {}) or {}).get("h24") or pair.get("volume24h"),
            "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
            "market_cap": pair.get("marketCap") or pair.get("fdv"),
        }
    except Exception:
        return None

def get_tbp_price_and_mc():
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{TBP_PAIR}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair = j.get("pair") or (j.get("pairs") or [{}])[0]
        price = _safe_float(pair.get("priceUsd"))
        mc    = _safe_float(pair.get("marketCap") or pair.get("fdv"))
        return price, mc
    except Exception:
        return None, None

# =========================
# MARKET DATA (C-BOOST)
# =========================

def get_cboost_live_data():
    pair = CBOOST_PAIR
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/polygon/{pair}",
            timeout=6
        )
        r.raise_for_status()
        j = r.json()
        pair_data = j.get("pair") or (j.get("pairs") or [{}])[0]

        price      = _safe_float(pair_data.get("priceUsd"))
        mc         = _safe_float(pair_data.get("fdv") or pair_data.get("marketCap"))
        volume_24h = _safe_float((pair_data.get("volume", {}) or {}).get("h24") or pair_data.get("volume24h"))

        return {
            "price":      price,
            "market_cap": mc,
            "volume_24h": volume_24h,
            "chart_url":  f"https://dexscreener.com/polygon/{pair}",
        }
    except Exception as e:
        print(f"[CBOOST] Dexscreener error: {e}")
        return None

# =========================
# CONVERSATION MEMORY
# =========================

CHAT_MEM_MAX = 14
INTERJECT_COOLDOWN_SEC = 7 * 60
TOPIC_WINDOW_SEC = 90
TOPIC_MIN_LINES = 3

def ensure_chat_mem(chat_id: int):
    if chat_id not in MEM["chat_mem"]:
        MEM["chat_mem"][chat_id] = deque(maxlen=CHAT_MEM_MAX)

def add_chat_line(chat_id: int, user_id: int, name: str, text: str):
    ensure_chat_mem(chat_id)
    MEM["chat_mem"][chat_id].append({
        "t": datetime.utcnow(),
        "uid": user_id,
        "name": (name or "")[:24],
        "text": (text or "")[:500],
    })

def detect_topic_label(text: str) -> str:
    t = (text or "").lower()
    if WORD_NFT.search(t):
        return "nfts"
    if WORD_PRICE.search(t):
        return "market"
    if "scam" in t or "rug" in t or "honeypot" in t or "fraud" in t or "betrug" in t:
        return "security"
    if "listing" in t or "cmc" in t or "coinmarketcap" in t:
        return "listing"
    if "how" in t or "wie" in t or "help" in t or "hilfe" in t:
        return "help"
    if "plan" in t or "roadmap" in t or "future" in t or "zukunft" in t:
        return "plan"
    return "chat"

def window_lines(chat_id: int, sec: int = TOPIC_WINDOW_SEC):
    ensure_chat_mem(chat_id)
    now = datetime.utcnow()
    out = [x for x in list(MEM["chat_mem"][chat_id]) if (now - x["t"]).total_seconds() <= sec]
    return out

def should_interject(chat_id: int, is_cboost_chat: bool) -> bool:
    now = datetime.utcnow()
    last = MEM["last_interject"].get(chat_id)
    if last and (now - last).total_seconds() < INTERJECT_COOLDOWN_SEC:
        return False

    lines = window_lines(chat_id)
    if len(lines) < TOPIC_MIN_LINES:
        return False

    labels = [detect_topic_label(x["text"]) for x in lines]
    common = max(set(labels), key=labels.count)
    if labels.count(common) < TOPIC_MIN_LINES:
        return False

    if common == "chat":
        return False

    MEM["chat_topic"][chat_id] = common
    return True

def build_chat_context_block(chat_id: int) -> str:
    ensure_chat_mem(chat_id)
    lines = list(MEM["chat_mem"][chat_id])[-10:]
    out = []
    for x in lines:
        nm = x.get("name") or "user"
        tx = (x.get("text") or "").replace("\n", " ")
        out.append(f"{nm}: {tx}")
    return "\n".join(out)

def note_user(chat_id: int, user_id: int, tag: str):
    key = (chat_id, user_id)
    s = MEM["user_notes"].get(key)
    if not s:
        s = set()
    s.add(tag)
    MEM["user_notes"][key] = s

def get_user_notes(chat_id: int, user_id: int):
    return sorted(list(MEM["user_notes"].get((chat_id, user_id), set())))

# =========================
# FAQ SHORTCUTS (fast + stable)
# =========================

def faq_reply(text: str, lang: str, is_cboost_chat: bool) -> str:
    t = (text or "").lower().strip()

    # Knowledge router first
    kr = knowledge_router(text, lang, is_cboost_chat, allow_links=True)
    if kr:
        return kr

    if not is_cboost_chat:
        if any(k in t for k in ["plan", "roadmap", "zukunft", "future"]) and any(k in t for k in ["tbp", "turbopepe", "nft", "ai", "bot", "project"]):
            return say(lang,
                "🧭 <b>TBP Plan (kurz)</b>\n"
                "1) Community + Memes + stabiler BuyBot\n"
                "2) NFTs als Support + später Perks (Rollen/Access/Airdrops)\n"
                "3) AI weiter ausbauen (bessere Antworten + Tools)\n"
                "4) Mehr Sichtbarkeit (Trackers/Listing wenn organisch passt)\n\n"
                "Willst du eher <b>NFT-Future</b> oder <b>AI-Future</b> genauer? 🙂",
                "🧭 <b>TBP Plan (short)</b>\n"
                "1) Community + memes + stable BuyBot\n"
                "2) NFTs as support + later perks (roles/access/airdrops)\n"
                "3) Keep upgrading the AI (better replies + tools)\n"
                "4) More visibility (trackers/listings when organic)\n\n"
                "Do you want <b>NFT future</b> or <b>AI future</b> in more detail? 🙂"
            )

        if "lp" in t and ("burn" in t or "burned" in t or "geburn" in t):
            return say(lang,
                "✅ TBP: LP ist geburnt (dauerhaft). Das reduziert Rug-Risiko deutlich.",
                "✅ TBP: LP is burned (permanent). This strongly reduces rug risk."
            )

        if "owner" in t or "renounce" in t or "renounced" in t or "besitzer" in t:
            return say(lang,
                "✅ TBP: Owner ist renounced (keine versteckten Owner-Backdoors).",
                "✅ TBP: Owner is renounced (no hidden owner backdoors)."
            )

        if "tax" in t or "steuer" in t or "0%" in t or "0 tax" in t:
            return say(lang,
                "✅ TBP hat <b>0% Tax</b>. Keine Buy/Sell-Steuer.",
                "✅ TBP has <b>0% tax</b>. No buy/sell tax."
            )

        if WORD_LINKS.search(t):
            return (
                say(lang, "🔗 <b>TBP Links</b>\n", "🔗 <b>TBP Links</b>\n") +
                f"• Website: {LINKS['website']}\n"
                f"• Buy (Sushi): {LINKS['buy']}\n"
                f"• Chart: {LINKS['dexscreener']}\n"
                f"• Scan: {LINKS['contract_scan']}\n"
                f"• NFTs: {LINKS['nfts']}"
            )

        return ""

    else:
        if WORD_LINKS.search(t) or WORD_PRICE.search(t):
            return say(lang,
                "⚡ C-Boost: Nutze /price oder /chart für Live-Daten. Für Vision/Utility Fragen: einfach fragen 🙂",
                "⚡ C-Boost: Use /price or /chart for live data. For vision/utility questions: just ask 🙂"
            )
        return ""

# =========================
# OPENAI (FIXED: web vs telegram + context is used)
# =========================

def _build_messages_from_ctx(system_msg: str, question: str, ctx_list):
    messages = [{"role": "system", "content": system_msg}]
    try:
        for line in (ctx_list or [])[-12:]:
            if isinstance(line, str) and line.startswith("You: "):
                messages.append({"role": "user", "content": line[5:]})
            elif isinstance(line, str) and (line.startswith("TBP: ") or line.startswith("C-Boost: ")):
                parts = line.split(": ", 1)
                messages.append({"role": "assistant", "content": parts[1] if len(parts) > 1 else line})
    except Exception:
        pass
    messages.append({"role": "user", "content": question})
    return messages

def call_openai(question: str, context, mode: str = "tbp", channel: str = "tg"):
    print("DEBUG call_openai: mode =", mode, "channel =", channel)
    print("DEBUG call_openai: OPENAI_API_KEY set =", bool(OPENAI_API_KEY))
    print("DEBUG call_openai: OPENAI_MODEL =", OPENAI_MODEL)

    if not OPENAI_API_KEY:
        print("DEBUG call_openai: NO OPENAI_API_KEY, aborting.")
        return None

    if mode == "cboost":
        if channel == "web":
            system_msg = """You are C-BoostAI on the official website.
Behave like a helpful ChatGPT-style assistant.

RULES:
- Answer general questions normally (even if not C-Boost related).
- If relevant, relate the answer back to C-Boost briefly.
- You may be detailed when needed.
- No hype promises, no price predictions, no financial advice.
- Only share links if the user explicitly asks for links/where/how."""
        else:
            system_msg = """You are C-BoostAI, the official assistant of the C-Boost micro supply token on Polygon.
ALWAYS answer in the user's language (German or English). Detect language automatically.

STYLE:
- Sound like a real Telegram community member: short, friendly, not corporate.
- Default length: 1-4 sentences. Only go longer if user explicitly asks for details/steps.
- Light humor is OK. No spam. No hype promises. No price predictions. No financial advice.

PROJECT INFO:
- C-Boost is a next-generation MICRO SUPPLY token on Polygon.
- Total supply: 5,000,000 tokens.
- Transparent supply, no complex taxes.
- Focus on raids, strong community, and future AI tools.
- Long-term vision: meme creation, AI utilities, and community quests.

BUYBOT INFO:
- C-Boost has an official BuyBot system posting on-chain buys with USD value, token amount, wallet short, NEW holder tag, tx link.

RULES:
- Be factual.
- If users ask about TBP, say you are only responsible for C-Boost.
"""
    else:
        if channel == "web":
            system_msg = """You are TBP-AI on the official TurboPepe-AI (TBP) website.
Behave like a helpful ChatGPT-style assistant.

CORE:
- Answer general questions normally (even if not TBP-related).
- If relevant, relate the answer back to TBP briefly and clearly.
- You may be detailed when needed (step-by-step if user asks).
- Be honest about what exists today vs future plans.
- No hype promises, no price predictions, no financial advice.
- Only share links if the user explicitly asks for links/where/how/buy/mint/scan/chart.

TBP FACTS (use when relevant):
- Chain: Polygon.
- 0% tax.
- LP burned, owner renounced.
- Website AI + Telegram assistant + live buy bot + security filters.
- TBP-AI NFTs exist: Gold ($60) and Silver ($30)."""
        else:
            system_msg = """You are TBP-AI, the official assistant of TurboPepe-AI (TBP) on Polygon.
ALWAYS answer in the user's language (German or English). Detect language automatically.

STYLE:
- Sound like a real Telegram community member: short, friendly, not corporate.
- Default length: 1-4 sentences. Only go longer if user explicitly asks for details/steps.
- Light humor is OK. No spam. No hype promises. No price predictions. No financial advice.

CURRENT PROJECT:
- TBP is a community-driven meme + AI token on Polygon.
- LP is burned, owner is renounced, no hidden contract tricks.
- 0% tax, fully transparent.
- TBP has: website AI, Telegram assistant, live buy bot, and security filters.

BUYBOT:
- Posts every on-chain buy with USD value, POL amount, token amount, NEW holder detection, and tx link.

NFT:
- Official TBP-AI NFTs: Gold ($60) and Silver ($30).
- Official mint page: https://quantumpepe.github.io/NFTs-WalletConnectV2/

VISION:
- Long-term goal: dedicated AI infrastructure around TBP (servers, private models, tools).
- Be clear what exists today vs future plans. No guarantees.
"""

    messages = _build_messages_from_ctx(system_msg, question, context)

    try:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.70 if channel == "web" else 0.65,
                max_tokens=700 if channel == "web" else 420,
            )
            return resp.choices[0].message.content.strip()
        except ImportError:
            import openai
            openai.api_key = OPENAI_API_KEY
            resp = openai.ChatCompletion.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.70 if channel == "web" else 0.65,
                max_tokens=700 if channel == "web" else 420,
            )
            return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        import traceback
        print("OpenAI error:", repr(e))
        traceback.print_exc()
        return None

def clean_answer(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)(financial advice|finanzberatung)", "information", s)
    s = s.strip()
    if len(s) > 2200:
        s = s[:2200].rstrip() + "…"
    return s

def human_delay_for(text: str):
    ln = len(text or "")
    if ln < 80:
        time.sleep(random.uniform(0.4, 1.0))
    elif ln < 220:
        time.sleep(random.uniform(0.9, 1.6))
    else:
        time.sleep(random.uniform(1.4, 2.6))

# =========================
# AUTO-POST (nur TBP)
# =========================

def autopost_needed():
    now = datetime.utcnow()
    last = MEM.get("last_autopost")
    if not last:
        return True
    return (now - last) >= timedelta(hours=10)

def autopost_text(lang="en"):
    p = get_live_price()
    stats = get_market_stats() or {}
    change = stats.get("change_24h")
    liq   = stats.get("liquidity_usd")
    vol   = stats.get("volume_24h")
    lines = [
        say(lang, "🔔 TBP Update:", "🔔 TBP Update:"),
        say(lang, "Preis", "Price") + f": {fmt_usd(p, 12) if p else 'N/A'}",
        "24h: " + (f"{change}%" if change not in (None, "", "null") else "N/A"),
        say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(liq) if liq else 'N/A'}",
        "Vol 24h: " + (fmt_usd(vol) if vol else "N/A"),
        "",
        say(
            lang,
            "TBP = Meme + AI auf Polygon. 0% Tax, LP geburnt, Owner renounced. Community first.",
            "TBP = Meme + AI on Polygon. 0% tax, LP burned, owner renounced. Community first."
        ),
        "🪙 TBP-AI NFTs: Gold ($60) / Silver ($30) → " + LINKS["nfts"],
        "",
        f"• Sushi: {LINKS['buy']}",
        f"• Chart: {LINKS['dexscreener']}",
        f"• Scan:  {LINKS['contract_scan']}"
    ]
    return "\n".join(lines)

def start_autopost_background(chat_id: int):
    if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
        return

    def loop():
        while True:
            try:
                if autopost_needed():
                    tg_send(chat_id, autopost_text("en"))
                    MEM["last_autopost"] = datetime.utcnow()
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()

# =========================
# BUYBOT – TBP & C-BOOST
# =========================

CBOOST_CONTRACT = os.environ.get("CBOOST_CONTRACT", "").strip().lower()

TOKEN_BUYBOT = {
    "tbp": {
        "network": "polygon_pos",
        "pool": TBP_PAIR,
        "symbol": "TBP",
        "name": "TurboPepe-AI",
        "logo_url": TBP_LOGO_URL,
        "min_usd": float(os.environ.get("TBP_MIN_BUY_USD", "3.0")),
        "token_contract": TBP_CONTRACT.lower(),
    },
    "cboost": {
        "network": CBOOST_NETWORK or "polygon_pos",
        "pool": CBOOST_POOL_ADDRESS or CBOOST_PAIR,
        "symbol": "C-Boost",
        "name": "C-Boost",
        "logo_url": CBOOST_LOGO_URL,
        "min_usd": float(os.environ.get("CBOOST_MIN_BUY_USD", "3.0")),
        "token_contract": CBOOST_CONTRACT,
    },
}

def fetch_pool_trades(network: str, pool_address: str, token_contract: str = "", limit: int = 25):
    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/trades"

    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception as e:
        print(f"[BUYBOT] fetch_pool_trades error: {e}")
        return []

    token_contract = (token_contract or "").lower()
    trades = []

    for item in data[:limit]:
        attrs = item.get("attributes", {}) or {}

        tx_hash = (
            attrs.get("tx_hash")
            or attrs.get("transaction_hash")
            or item.get("id")
            or ""
        )

        kind = (attrs.get("kind") or "").lower()

        from_addr = (attrs.get("from_token_address") or "").lower()
        to_addr   = (attrs.get("to_token_address") or "").lower()

        from_amt = _safe_float(attrs.get("from_token_amount"))
        to_amt   = _safe_float(attrs.get("to_token_amount"))

        side = kind
        token_amount = None
        quote_amount = None

        if token_contract:
            if to_addr == token_contract:
                side = "buy"
                token_amount = to_amt
                quote_amount = from_amt
            elif from_addr == token_contract:
                side = "sell"
                token_amount = from_amt
                quote_amount = to_amt
            else:
                token_amount = to_amt
                quote_amount = from_amt
        else:
            side = kind
            token_amount = to_amt
            quote_amount = from_amt

        usd = _safe_float(
            attrs.get("volume_in_usd")
            or attrs.get("trade_amount_usd")
            or attrs.get("amount_usd")
            or attrs.get("value_usd")
        )

        wallet = (
            attrs.get("tx_from_address")
            or attrs.get("from_address")
            or attrs.get("maker_address")
            or attrs.get("sender")
        )

        trades.append(
            {
                "tx_hash": tx_hash,
                "side": side,
                "usd": usd,
                "token_amount": token_amount,
                "quote_amount": quote_amount,
                "wallet": wallet,
            }
        )

    trades.reverse()
    return trades

def send_tbp_buy_alert(chat_id: int, trade: dict, is_new: bool):
    usd = trade.get("usd")
    token_amount = trade.get("token_amount")
    pol_amount = trade.get("quote_amount")
    wallet = trade.get("wallet")
    tx_hash = trade.get("tx_hash")

    price_now, mc_now = get_tbp_price_and_mc()
    stats = get_market_stats() or {}
    vol_24h = stats.get("volume_24h")

    price_txt = fmt_usd(price_now, 12) if price_now is not None else "N/A"
    mc_txt = fmt_usd(mc_now, 0) if mc_now is not None else "N/A"
    vol_txt = fmt_usd(vol_24h, 2) if vol_24h is not None else "N/A"
    chart_url = LINKS["dexscreener"]

    caption_lines = [
        "🐸 <b>TBP Live Data – New Buy</b>\n",
        f"💰 <b>Buy Value:</b> {fmt_usd(usd, 2) if usd is not None else 'N/A'}",
    ]

    if pol_amount is not None:
        caption_lines.append(f"⛽ <b>POL used:</b> {pol_amount:.4f} POL")

    caption_lines.append(
        f"🪙 <b>Amount:</b> {token_amount:.4f} TBP"
        if token_amount is not None
        else "🪙 <b>Amount:</b> N/A"
    )

    caption_lines.extend(
        [
            "",
            f"💵 <b>Price (after):</b> {price_txt}",
            f"🏦 <b>Market Cap:</b> {mc_txt}",
            f"📊 <b>24h Volume:</b> {vol_txt}",
        ]
    )

    if wallet:
        new_tag = " (NEW)" if is_new else ""
        caption_lines.append(
            f"👛 <b>Wallet:</b> <code>{_short_addr(wallet)}</code>{new_tag}"
        )

    if tx_hash:
        caption_lines.append(
            f"🔗 <a href=\"https://polygonscan.com/tx/{tx_hash}\">View on PolygonScan</a>"
        )

    if chart_url:
        caption_lines.append(f"\n📈 <a href=\"{chart_url}\">Open Live Chart</a>")

    caption = "\n".join(caption_lines)
    logo = TBP_LOGO_URL
    if logo:
        tg_send_photo(chat_id, logo, caption=caption)
    else:
        tg_send(chat_id, caption, preview=True)

def send_cboost_buy_alert(chat_id: int, trade: dict, is_new: bool):
    usd = trade.get("usd")
    token_amount = trade.get("token_amount")
    pol_amount = trade.get("quote_amount")
    wallet = trade.get("wallet")
    tx_hash = trade.get("tx_hash")

    data = get_cboost_live_data() or {}
    price_now = data.get("price")
    mc_now = data.get("market_cap")
    vol_now = data.get("volume_24h")
    chart_url = data.get("chart_url")

    price_txt = fmt_usd(price_now, 10) if price_now is not None else "N/A"
    mc_txt = fmt_usd(mc_now, 2) if mc_now is not None else "N/A"
    vol_txt = fmt_usd(vol_now, 2) if vol_now is not None else "N/A"

    caption_lines = [
        "⚡ <b>C-Boost Live Data – New Buy</b>\n",
        f"💰 <b>Buy Value:</b> {fmt_usd(usd, 2) if usd is not None else 'N/A'}",
    ]

    if pol_amount is not None:
        caption_lines.append(f"⛽ <b>POL used:</b> {pol_amount:.4f} POL")

    caption_lines.append(
        f"🪙 <b>Amount:</b> {token_amount:.4f} C-Boost"
        if token_amount is not None
        else "🪙 <b>Amount:</b> N/A"
    )

    caption_lines.extend(
        [
            "",
            f"💵 <b>Price (after):</b> {price_txt}",
            f"🏦 <b>Market Cap:</b> {mc_txt}",
            f"📊 <b>24h Volume:</b> {vol_txt}",
        ]
    )

    if wallet:
        new_tag = " (NEW)" if is_new else ""
        caption_lines.append(
            f"👛 <b>Wallet:</b> <code>{_short_addr(wallet)}</code>{new_tag}"
        )

    if tx_hash:
        caption_lines.append(
            f"🔗 <a href=\"https://polygonscan.com/tx/{tx_hash}\">View on PolygonScan</a>"
        )

    if chart_url:
        caption_lines.append(f"\n📈 <a href=\"{chart_url}\">Open Live Chart</a>")

    caption = "\n".join(caption_lines)
    logo = CBOOST_LOGO_URL
    if logo:
        tg_send_photo(chat_id, logo, caption=caption)
    else:
        tg_send(chat_id, caption, preview=True)

def process_buybot_for(token_key: str, chat_id: int):
    cfg = TOKEN_BUYBOT.get(token_key)
    if not cfg:
        return

    trades = fetch_pool_trades(
        cfg["network"],
        cfg["pool"],
        cfg.get("token_contract", "")
    )
    if not trades:
        return

    state = MEM["buybot"][token_key]
    last_hash = state.get("last_hash")

    hashes = [t.get("tx_hash") for t in trades if t.get("tx_hash")]
    if not hashes:
        return

    if not last_hash:
        state["last_hash"] = hashes[-1]
        return

    if last_hash not in hashes:
        state["last_hash"] = hashes[-1]
        return

    idx = hashes.index(last_hash)
    new_trades = trades[idx + 1 : ]
    if not new_trades:
        return

    for tr in new_trades:
        side = (tr.get("side") or "").lower()
        if "buy" not in side:
            continue

        usd = tr.get("usd") or 0
        if usd is None or usd < cfg["min_usd"]:
            continue

        wallet = tr.get("wallet")
        known = state["known_wallets"]
        is_new = False
        if wallet and wallet not in known:
            known.add(wallet)
            is_new = True

        if token_key == "tbp":
            send_tbp_buy_alert(chat_id, tr, is_new)
        else:
            send_cboost_buy_alert(chat_id, tr, is_new)

    state["last_hash"] = hashes[-1]

def start_buybot_background():
    def loop():
        while True:
            try:
                tbp_chat = MEM.get("tbp_chat_id")
                if tbp_chat:
                    process_buybot_for("tbp", tbp_chat)
                if CBOOST_CHAT_ID:
                    process_buybot_for("cboost", CBOOST_CHAT_ID)
            except Exception as e:
                print(f"[BUYBOT] loop error: {e}")
            time.sleep(25)

    threading.Thread(target=loop, daemon=True).start()

# =========================
# IDLE WATCHDOG – lebendiger Chat
# =========================

IDLE_MESSAGES_TBP = [
    "Yo TBP crew… kurz ruhig hier 👀🐸",
    "Silence detected. Drop a meme or a question 😎",
    "Reminder: TBP AI + BuyBot is running – who has already checked the chart today? 📈",
    "🪙 TBP-AI NFTs are live (Gold $60 / Silver $30). Mint: https://quantumpepe.github.io/NFTs-WalletConnectV2/ 🐸",
]

IDLE_MESSAGES_CBOOST = [
    "C-Boost army, where are you? ⚡😂",
    "Too quiet… boost the chat 📈🚀",
    "Drop a meme or ask me something 😏⚡",
]

def start_idle_watchdog_background():
    def loop():
        while True:
            try:
                now = datetime.utcnow()
                last_activity = MEM.get("last_activity", {})
                last_idle = MEM.get("last_idle", {})

                for chat_id, last in list(last_activity.items()):
                    if not chat_id or not isinstance(last, datetime):
                        continue

                    diff = (now - last).total_seconds()
                    prev_idle_time = last_idle.get(chat_id)
                    idle_diff = (now - prev_idle_time).total_seconds() if prev_idle_time else 999999

                    if diff > 600 and idle_diff > 3600:
                        if CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID:
                            msg = random.choice(IDLE_MESSAGES_CBOOST)
                        else:
                            msg = random.choice(IDLE_MESSAGES_TBP)

                        tg_send(chat_id, msg)
                        MEM["last_idle"][chat_id] = now

            except Exception as e:
                print("[IDLE] loop error:", e)

            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()

# =========================
# FLASK WEB (health/ask/admin)
# =========================

@app.route("/")
def root():
    return jsonify({"ok": True, "service": "tbp-advisor", "time": datetime.utcnow().isoformat()+"Z"})

@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/admin/set_webhook")
def admin_set_webhook():
    key = request.args.get("key", "")
    if not ADMIN_SECRET or key != ADMIN_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    root_url = request.url_root.replace("http://", "https://")
    url = root_url.rstrip("/") + "/telegram"

    tokens = [t for t in [TELEGRAM_TOKEN_TBP, TELEGRAM_TOKEN_CBOOST] if t]
    if not tokens:
        return jsonify({"ok": False, "error": "no telegram tokens configured"}), 500

    results = []
    try:
        for tok in tokens:
            r = requests.get(
                f"https://api.telegram.org/bot{tok}/setWebhook",
                params={"url": url},
                timeout=10
            )
            results.append(r.json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "responses": results}), 500

    return jsonify({"ok": True, "responses": results})

# Web-AI für TBP-Webseite (Option A: ChatGPT style)
@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200

    lang = "de" if is_de(q) else "en"

    # Price shortcut stays fast
    if WORD_PRICE.search(q):
        p = get_live_price()
        stats = get_market_stats() or {}
        lines = []
        if p is not None:
            lines.append(say(lang, "💰 Preis", "💰 Price") + f": {fmt_usd(p, 12)}")
        if stats.get("change_24h") not in (None, "", "null"):
            lines.append(f"📈 24h: {stats['change_24h']}%")
        if stats.get("liquidity_usd") not in (None, "", "null"):
            lines.append("💧 " + say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(stats['liquidity_usd'])}")
        if stats.get("volume_24h") not in (None, "", "null"):
            lines.append(f"🔄 Vol 24h: {fmt_usd(stats['volume_24h'])}")
        ans = "\n".join(lines) if lines else say(lang, "Preis derzeit nicht verfügbar.", "Price currently unavailable.")
    else:
        # IMPORTANT FIX: Knowledge router is ONLY a helper hint for web, not a hard override
        kr = knowledge_router(q, lang, is_cboost_chat=False, allow_links=True)
        hint = ""
        if kr:
            hint = (
                "\n\nUse the following quick project facts if helpful, but do NOT copy-paste as a template. "
                "Answer naturally and adapt to the question:\n"
                f"{kr}\n"
            )

        raw = call_openai(q + hint, MEM["ctx"], mode="tbp", channel="web") \
              or say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")
        ans = clean_answer(raw)

    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"TBP: {ans}")
    MEM["ctx"] = MEM["ctx"][-14:]
    return jsonify({"answer": ans})

# Web-AI für C-Boost Website (Option A)
@app.route("/ask_cboost", methods=["POST"])
def ask_cboost():
    data = request.json or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "empty question"}), 200

    lang = "de" if is_de(q) else "en"

    kr = knowledge_router(q, lang, is_cboost_chat=True, allow_links=False)
    hint = ""
    if kr:
        hint = (
            "\n\nUse the following quick info if helpful, but do NOT copy-paste as a template:\n"
            f"{kr}\n"
        )

    raw = call_openai(q + hint, MEM["ctx"], mode="cboost", channel="web") or "Network glitch. Try again ⚡"
    ans = clean_answer(raw)

    MEM["ctx"].append(f"You: {q}")
    MEM["ctx"].append(f"C-Boost: {ans}")
    MEM["ctx"] = MEM["ctx"][-14:]
    return jsonify({"answer": ans})

# C-Boost PRICE API
@app.route("/cboost_price", methods=["GET"])
def cboost_price():
    data = get_cboost_live_data()
    if not data:
        return jsonify({"ok": False, "error": "no_data"}), 200

    return jsonify({
        "ok": True,
        "price":      data["price"],
        "market_cap": data["market_cap"],
        "volume_24h": data["volume_24h"],
        "chart_url":  data["chart_url"]
    })

# =========================
# TELEGRAM
# =========================

MEME_CAPTIONS_TBP = [
    "Nice pic 😎 willst du 'ne TBP Caption dazu? 🐸",
    "TurboPepe energy detected ⚡🐸",
    "Clean drop. Meme it? 👀",
]

MEME_CAPTIONS_CBOOST = [
    "Boost-worthy image ⚡😏 brauchst du 'ne Caption?",
    "Nice pic! Let's boost it 🚀",
    "C-Boost mode ON ⚡",
]

def handle_extra_commands(text, chat_id, lang, is_cboost_chat, msg_id=None):
    low = text.lower().strip()

    if low.startswith("/about"):
        if is_cboost_chat:
            msg = (
                "🤖 <b>C-BoostAI</b>\n\n"
                "🇩🇪 Offizieller Assistant für C-Boost (Polygon). Kurz & hilfreich, keine Finanzberatung.\n"
                "🇬🇧 Official assistant for C-Boost (Polygon). Short & helpful, no financial advice.\n"
            )
        else:
            msg = (
                "🤖 <b>TBP-AI</b>\n\n"
                "🇩🇪 Offizieller Assistant für TBP auf Polygon. 0% Tax, LP geburnt, Owner renounced.\n"
                "🇬🇧 Official assistant for TBP on Polygon. 0% tax, LP burned, owner renounced.\n\n"
                "🪙 TBP-AI NFTs: Gold ($60) / Silver ($30)\n"
                f"🔗 {LINKS['nfts']}"
            )
        tg_send(chat_id, msg, reply_to=msg_id, preview=False)
        return True

    if low.startswith("/dev"):
        msg = say(lang,
            "🛠 Dev baut laufend aus: BuyBot, Security, bessere Chat-Antworten, Tools. Ideen einfach hier rein 👇",
            "🛠 Dev keeps upgrading: buy bot, security, better chat replies, tools. Drop ideas here 👇"
        )
        tg_send(chat_id, msg, reply_to=msg_id, preview=False)
        return True

    return False

@app.route("/telegram", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        return jsonify({"ok": True, "route": "telegram"}), 200

    update  = request.json or {}
    msg     = update.get("message", {}) or {}
    chat    = msg.get("chat", {}) or {}
    chat_id = chat.get("id")

    from_user = msg.get("from", {}) or {}
    user_id   = from_user.get("id")
    first_name = from_user.get("first_name") or ""
    username   = from_user.get("username")
    user_display = f"@{username}" if username else (first_name or "user")

    text    = (msg.get("text") or "").strip()
    msg_id  = msg.get("message_id")
    new_members = msg.get("new_chat_members") or []

    reply_to = msg.get("reply_to_message") or {}
    replied_to_bot = False
    try:
        replied_from = (reply_to.get("from") or {}).get("is_bot")
        replied_to_bot = bool(replied_from)
    except Exception:
        replied_to_bot = False

    if not chat_id:
        return jsonify({"ok": True})

    try:
        MEM["last_activity"][chat_id] = datetime.utcnow()
    except Exception:
        pass

    if text and re.fullmatch(r"[?\.\!]+", text):
        return jsonify({"ok": True})

    is_cboost_chat = bool(CBOOST_CHAT_ID and chat_id == CBOOST_CHAT_ID)

    if not is_cboost_chat and MEM.get("tbp_chat_id") is None:
        MEM["tbp_chat_id"] = chat_id

    if text and not text.startswith("/"):
        add_chat_line(chat_id, user_id or 0, user_display, text)

    if new_members:
        for member in new_members:
            fn = member.get("first_name") or ""
            un = member.get("username")
            display = f"@{un}" if un else fn or "friend"

            if is_cboost_chat:
                welcome_text = (
                    f"👋 Welcome {display} to the official C-Boost community!\n\n"
                    "AI security is ON:\n"
                    "• No paid listing offers\n"
                    "• No promo for other projects\n"
                    "Use /rules for details ⚡"
                )
            else:
                welcome_text = (
                    f"👋 Welcome {display} to the official TurboPepe-AI (TBP) community!\n\n"
                    "AI security is ON:\n"
                    "• No paid listing offers\n"
                    "• No promo for other projects\n\n"
                    "🪙 TBP-AI NFTs LIVE: Gold ($60) / Silver ($30)\n"
                    f"Mint: {LINKS['nfts']}\n\n"
                    "Use /rules for details 🐸"
                )
            tg_send(chat_id, welcome_text)

        if not text:
            return jsonify({"ok": True})

    try:
        if MEM.get("_autopost_started") != True and not is_cboost_chat:
            start_autopost_background(chat_id)
            MEM["_autopost_started"] = True
    except Exception:
        pass

    try:
        if MEM.get("_buybot_started") != True:
            start_buybot_background()
            MEM["_buybot_started"] = True
    except Exception:
        pass

    try:
        if MEM.get("_idle_started") != True:
            start_idle_watchdog_background()
            MEM["_idle_started"] = True
    except Exception:
        pass

    if "photo" in msg:
        caption = random.choice(MEME_CAPTIONS_CBOOST if is_cboost_chat else MEME_CAPTIONS_TBP)
        tg_send(chat_id, caption, reply_to=msg_id)
        MEM["chat_count"] += 1
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    low  = text.lower()
    lang = "de" if is_de(text) else "en"
    MEM["chat_count"] += 1

    # Commands
    if low.startswith("/start"):
        if is_cboost_chat:
            tg_send(
                chat_id,
                say(
                    lang,
                    "Hi, ich bin C-BoostAI ⚡ – kurz & hilfreich. Frag mich was zur Vision/Utility. Keine Finanzberatung.",
                    "Hi, I'm C-BoostAI ⚡ – short & helpful. Ask about vision/utility. No financial advice."
                ),
                reply_to=msg_id
            )
        else:
            tg_buttons(
                chat_id,
                say(lang, f"Hi, ich bin {BOT_NAME}. Frag was zu TBP 🐸", f"Hi, I'm {BOT_NAME}. Ask about TBP 🐸"),
                [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"])]
            )
        return jsonify({"ok": True})

    if low.startswith("/help"):
        tg_send(chat_id, "/price • /stats • /chart • /links • /nfts • /rules • /security • /id • /about • /dev", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/about") or low.startswith("/dev"):
        if handle_extra_commands(text, chat_id, lang, is_cboost_chat, msg_id):
            return jsonify({"ok": True})

    if low.startswith("/rules") or low.startswith("/security"):
        if is_cboost_chat:
            rules_text = (
                "🛡 <b>C-Boost Security Rules</b>\n\n"
                "• No paid listing offers\n"
                "• No promotion of other projects\n"
                "• Keep it C-Boost related ⚡\n\n"
                "🇩🇪 Kurz:\n• Keine bezahlten Listings\n• Keine Fremd-Promo\n"
            )
        else:
            rules_text = (
                "🛡 <b>TBP Security Rules</b>\n\n"
                "• No paid listing offers\n"
                "• No promotion of other projects\n"
                "• Only official TBP links allowed 🐸\n\n"
                "🇩🇪 Kurz:\n• Keine bezahlten Listings\n• Keine Fremd-Promo\n"
            )
        tg_send(chat_id, rules_text, reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/id"):
        tg_send_any(chat_id, f"Chat ID: <code>{chat_id}</code>", reply_to=msg_id, preview=False)
        return jsonify({"ok": True})

    if low.startswith("/links"):
        if is_cboost_chat:
            tg_send(chat_id, say(lang,
                "⚡ C-Boost: nutze /price und /chart für Live-Daten. Mehr Links folgen später.",
                "⚡ C-Boost: use /price and /chart for live data. More links later."
            ), reply_to=msg_id)
            return jsonify({"ok": True})

        tg_buttons(
            chat_id,
            say(lang, "🔗 TBP Quick Links:", "🔗 TBP Quick Links:"),
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"]), ("Website", LINKS["website"]), ("NFTs", LINKS["nfts"])]
        )
        return jsonify({"ok": True})

    # PRICE / STATS / CHART
    if low.startswith("/price") or (not low.startswith("/") and WORD_PRICE.search(low)):
        if is_cboost_chat:
            data = get_cboost_live_data()
            if not data:
                tg_send(chat_id, say(lang,
                    "⚠️ C-Boost Live-Daten gerade nicht verfügbar.",
                    "⚠️ C-Boost live data not available right now."
                ), reply_to=msg_id)
                return jsonify({"ok": True})

            price = data.get("price")
            mc    = data.get("market_cap")
            vol   = data.get("volume_24h")
            chart = data.get("chart_url")

            caption = "\n".join([
                "⚡ <b>C-Boost Live Data</b>\n",
                f"🪙 <b>Price:</b> {fmt_usd(price, 10) if price is not None else 'N/A'}",
                f"💰 <b>Market Cap:</b> {fmt_usd(mc, 2) if mc is not None else 'N/A'}",
                f"📊 <b>24h Volume:</b> {fmt_usd(vol, 2) if vol is not None else 'N/A'}",
                f"\n📈 <a href=\"{chart}\">Open Live Chart</a>" if chart else ""
            ]).strip()

            tg_send_photo(chat_id, CBOOST_LOGO_URL, caption=caption, reply_to=msg_id)
            return jsonify({"ok": True})

        p = get_live_price()
        s = get_market_stats() or {}
        lines = []
        if p is not None:
            lines.append(say(lang, "💰 Preis", "💰 Price") + f": {fmt_usd(p, 12)}")
        if s.get("change_24h") not in (None, "", "null"):
            lines.append(f"📈 24h: {s['change_24h']}%")
        if s.get("liquidity_usd") not in (None, "", "null"):
            lines.append("💧 " + say(lang, "Liquidität", "Liquidity") + f": {fmt_usd(s['liquidity_usd'])}")
        if s.get("volume_24h") not in (None, "", "null"):
            lines.append(f"🔄 Vol 24h: {fmt_usd(s['volume_24h'])}")

        caption = "\n".join(lines) if lines else say(lang, "Keine Daten.", "No data.")
        if TBP_LOGO_URL:
            tg_send_photo(chat_id, TBP_LOGO_URL, caption=caption, reply_to=msg_id)
        else:
            tg_buttons(chat_id, caption, [("Chart", LINKS["dexscreener"]), ("Sushi", LINKS["buy"])])
        return jsonify({"ok": True})

    if low.startswith("/stats"):
        if is_cboost_chat:
            data = get_cboost_live_data()
            if not data:
                tg_send(chat_id, say(lang,
                    "C-Boost Stats gerade nicht verfügbar.",
                    "C-Boost stats not available right now."
                ), reply_to=msg_id)
                return jsonify({"ok": True})

            lines = [
                "⚡ C-Boost Stats:",
                f"• Price: {fmt_usd(data.get('price'), 10) if data.get('price') is not None else 'N/A'}",
                f"• Market Cap: {fmt_usd(data.get('market_cap'), 2) if data.get('market_cap') is not None else 'N/A'}",
                f"• Vol 24h: {fmt_usd(data.get('volume_24h'), 2) if data.get('volume_24h') is not None else 'N/A'}",
            ]
            tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
            return jsonify({"ok": True})

        s = get_market_stats() or {}
        lines = [say(lang, "TBP-Stats:", "TBP Stats:")]
        if s.get("change_24h") not in (None, "", "null"):
            lines.append(f"• 24h: {s['change_24h']}%")
        if s.get("volume_24h") not in (None, "", "null"):
            lines.append(f"• Vol 24h: {fmt_usd(s['volume_24h'])}")
        if s.get("liquidity_usd") not in (None, "", "null"):
            lines.append(f"• Liq: {fmt_usd(s['liquidity_usd'])}")
        if s.get("market_cap") not in (None, "", "null"):
            lines.append(f"• MC: {fmt_usd(s['market_cap'])}")
        tg_send(chat_id, "\n".join(lines), reply_to=msg_id)
        return jsonify({"ok": True})

    if low.startswith("/chart"):
        if is_cboost_chat:
            data = get_cboost_live_data()
            chart = data.get("chart_url") if data else None
            tg_send(chat_id, say(lang,
                f"📊 C-Boost Chart:\n{chart}" if chart else "Chart gerade nicht verfügbar.",
                f"📊 C-Boost chart:\n{chart}" if chart else "Chart not available right now."
            ), reply_to=msg_id)
            return jsonify({"ok": True})

        tg_buttons(chat_id, say(lang, "📊 TBP Live-Chart:", "📊 TBP live chart:"), [("DexScreener", LINKS["dexscreener"]), ("DEXTools", LINKS["dextools"])])
        return jsonify({"ok": True})

    if text.strip().lower() == "raid.":
        tg_send(chat_id, say(lang, "🚀 RAID! TBP army bereit! 🐸", "🚀 RAID! TBP army ready! 🐸"), reply_to=msg_id)
        return jsonify({"ok": True})

    try:
        if MEM["chat_count"] >= 25 and not is_cboost_chat:
            tg_send(chat_id, autopost_text("en"))
            MEM["chat_count"] = 0
            MEM["last_autopost"] = datetime.utcnow()
    except Exception:
        pass

    # SECURITY FILTERS + STRIKES
    if not low.startswith("/") and not is_admin(user_id):
        if is_illegal_offer(low):
            tg_delete_message(chat_id, msg_id)
            strike = add_strike(chat_id, user_id)
            tg_send(chat_id, say(lang,
                f"⚠️ Illegale Angebote sind hier verboten. (Strike {strike}/3)",
                f"⚠️ Illegal offers are forbidden here. (Strike {strike}/3)"
            ))
            return jsonify({"ok": True})

        if is_listing_scam(low):
            tg_delete_message(chat_id, msg_id)
            strike = add_strike(chat_id, user_id)
            tg_send(chat_id, say(lang,
                f"⚠️ Bezahlte Listings/Fast-Track sind hier nicht erlaubt. (Strike {strike}/3)",
                f"⚠️ Paid listing/fast-track offers are not allowed here. (Strike {strike}/3)"
            ))
            return jsonify({"ok": True})

        if is_external_promo(low):
            tg_delete_message(chat_id, msg_id)
            strike = add_strike(chat_id, user_id)
            tg_send(chat_id, say(lang,
                f"⚠️ Externe Promo für andere Projekte ist hier nicht erlaubt. (Strike {strike}/3)",
                f"⚠️ External promo for other projects is not allowed here. (Strike {strike}/3)"
            ))
            return jsonify({"ok": True})

    # OPTION D: Knowledge router (Telegram only) BEFORE old NFT block
    if not low.startswith("/"):
        kr = knowledge_router(text, lang, is_cboost_chat, allow_links=True)
        if kr:
            tg_typing(chat_id)
            time.sleep(random.uniform(0.6, 1.4))
            if (not is_cboost_chat) and _user_wants_links(text):
                tg_buttons(
                    chat_id,
                    kr,
                    [("NFTs", LINKS["nfts"]), ("Chart", LINKS["dexscreener"]), ("Sushi", LINKS["buy"]), ("Scan", LINKS["contract_scan"])]
                )
            else:
                tg_send(chat_id, kr, reply_to=msg_id, preview=False)
            if WORD_NFT.search(low):
                note_user(chat_id, user_id or 0, "interested_nfts")
            return jsonify({"ok": True})

    # FAST FAQ SHORTCUTS
    if not low.startswith("/"):
        fast = faq_reply(text, lang, is_cboost_chat)
        if fast:
            tg_typing(chat_id)
            human_delay_for(fast)
            tg_send(chat_id, fast, reply_to=msg_id, preview=True)
            if WORD_NFT.search(low):
                note_user(chat_id, user_id or 0, "interested_nfts")
            if WORD_PRICE.search(low):
                note_user(chat_id, user_id or 0, "asks_price")
            return jsonify({"ok": True})
    # SMART CONFIRM / CORRECT (3C) — TBP only, anti-spam
    if not low.startswith("/") and not replied_to_bot and (not is_cboost_chat):
        si = maybe_smart_interject(chat_id, text, lang)
        if si:
            tg_typing(chat_id)
            time.sleep(random.uniform(0.4, 1.0))
            tg_send(chat_id, si, reply_to=msg_id, preview=False)
            return jsonify({"ok": True})

    # SMART INTERJECTION (Conversation Watcher)
    if not low.startswith("/") and not replied_to_bot:
        if should_interject(chat_id, is_cboost_chat):
            topic = MEM["chat_topic"].get(chat_id, "chat")
            MEM["last_interject"][chat_id] = datetime.utcnow()

            ctx = build_chat_context_block(chat_id)
            notes = get_user_notes(chat_id, user_id or 0)
            note_txt = f"User notes: {', '.join(notes)}" if notes else "User notes: none"

            interject_q = (
                "You are joining an ongoing Telegram group conversation.\n"
                "Give a short, helpful message that fits the current topic.\n"
                "Do NOT sound like an announcement. 1-3 sentences.\n"
                "No financial advice.\n\n"
                f"TOPIC: {topic}\n"
                f"{note_txt}\n\n"
                "CHAT CONTEXT (latest lines):\n"
                f"{ctx}\n\n"
                "Now write your message."
            )

            tg_typing(chat_id)
            time.sleep(random.uniform(0.7, 1.6))
            mode = "cboost" if is_cboost_chat else "tbp"
            raw = call_openai(interject_q, [], mode=mode, channel="tg")
            out = clean_answer(raw) if raw else say(lang, "kurz: ich bin da 👀", "quick: I'm here 👀")
            tg_send(chat_id, out, reply_to=msg_id, preview=False)
            return jsonify({"ok": True})

    # NORMAL AI REPLY (Selective, Human)
    if not low.startswith("/"):
        if not should_reply(chat_id, text, is_cboost_chat, replied_to_bot=replied_to_bot):
            return jsonify({"ok": True})

    mode = "cboost" if is_cboost_chat else "tbp"

    ctx = build_chat_context_block(chat_id)
    notes = get_user_notes(chat_id, user_id or 0)
    note_txt = f"User notes: {', '.join(notes)}" if notes else "User notes: none"

    enriched_q = (
        "Answer as a Telegram community member.\n"
        "Keep it short (1-4 sentences), unless user asks for details.\n"
        "If user asks general concept questions (e.g., NFTs), explain first, then optionally relate to the project.\n"
        "Do NOT drop links unless user asks for link/where/buy/mint/scan/chart.\n"
        "No price predictions. No financial advice.\n\n"
        f"{note_txt}\n\n"
        "CHAT CONTEXT (latest lines):\n"
        f"{ctx}\n\n"
        "USER MESSAGE:\n"
        f"{text}"
    )

    tg_typing(chat_id)
    time.sleep(random.uniform(0.4, 1.2))

    raw = call_openai(enriched_q, [], mode=mode, channel="tg")
    if not raw:
        raw = say(lang, "Netzwerkfehler. Versuch’s nochmal 🐸", "Network glitch. Try again 🐸")

    out = clean_answer(raw)

    wants_links = bool(re.search(r"\b(link|links|buy|kaufen|chart|scan|website)\b", low))
    if wants_links and mode == "tbp":
        human_delay_for(out)
        tg_buttons(
            chat_id,
            out,
            [("Sushi", LINKS["buy"]), ("Chart", LINKS["dexscreener"]), ("Scan", LINKS["contract_scan"]), ("NFTs", LINKS["nfts"])]
        )
    else:
        human_delay_for(out)
        tg_send(chat_id, out, reply_to=msg_id, preview=False)

    if WORD_NFT.search(low):
        note_user(chat_id, user_id or 0, "interested_nfts")
    if WORD_PRICE.search(low):
        note_user(chat_id, user_id or 0, "asks_price")

    return jsonify({"ok": True})

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[{BOT_NAME}] starting on :{port}")
    app.run(host="0.0.0.0", port=port)
