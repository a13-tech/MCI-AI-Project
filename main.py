import os
import time
import asyncio
import threading
import secrets
import logging
from datetime import datetime

import pytz
import psutil
from flask import Flask, jsonify, abort, request, send_from_directory
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx mencatat URL request, dan URL Telegram memuat token bot. Jangan sampai masuk log.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("mci")

START_TIME = time.time()
WIB = pytz.timezone("Asia/Jakarta")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VIEW_TOKEN = os.getenv("VIEW_TOKEN", "")

# --- CONFIG 5 DIVISI MCI (token Telegram & key Gemini terpisah, dari Environment) ---
AGENTS_CONFIG = {
    "devmatrix": {
        "name": "MCI.DevMatrix",
        "token": os.getenv("TOKEN_DEVMATRIX"),
        "gemini_key": os.getenv("GEMINI_DEVMATRIX"),
        "prompt": (
            "Kamu adalah MCI.DevMatrix, asisten koding dan arsitek logika hardware "
            "(C/C++, Python, mikrokontroler). Jawab dalam bahasa Indonesia, ringkas dan praktis. "
            "Jika diminta kode, berikan kode lengkap yang siap disalin."
        ),
    },
    "nexus": {
        "name": "MCI.Nexus",
        "token": os.getenv("TOKEN_NEXUS"),
        "gemini_key": os.getenv("GEMINI_NEXUS"),
        "prompt": (
            "Kamu adalah MCI.Nexus, pusat manajemen proyek, inventaris, dan tugas tim. "
            "Jawab dalam bahasa Indonesia, terstruktur, dan fokus pada langkah yang bisa dikerjakan."
        ),
    },
    "aegis": {
        "name": "MCI.Aegis",
        "token": os.getenv("TOKEN_AEGIS"),
        "gemini_key": os.getenv("GEMINI_AEGIS"),
        "prompt": (
            "Kamu adalah MCI.Aegis, verifikator kritis, evaluator risiko, dan penyusun safety checklist sistem. "
            "Jawab dalam bahasa Indonesia. Cari celah, sebutkan risiko dengan jelas, dan jangan hanya setuju."
        ),
    },
    "sentinel": {
        "name": "MCI.Sentinel",
        "token": os.getenv("TOKEN_SENTINEL"),
        "gemini_key": os.getenv("GEMINI_SENTINEL"),
        "prompt": (
            "Kamu adalah MCI.Sentinel, pembuat rangkuman poin penting dari diskusi. "
            "Kamu hanya bisa membaca teks yang dikirim langsung kepadamu di Telegram, bukan grup WhatsApp. "
            "Jawab dalam bahasa Indonesia dengan poin-poin ringkas."
        ),
    },
    "pulse": {
        "name": "MCI.Pulse",
        "token": os.getenv("TOKEN_PULSE"),
        "gemini_key": os.getenv("GEMINI_PULSE"),
        "prompt": (
            "Kamu adalah MCI.Pulse, asisten Electrical Engineering (kalkulasi daya, pinout, I2C/SPI/LoRa). "
            "Jawab dalam bahasa Indonesia. Tunjukkan rumus dan satuan, dan ingatkan batas tegangan atau arus yang berbahaya."
        ),
    },
}

# --- STATE GLOBAL (dibaca halaman kantor 3D lewat /api/all-status) ---
agents_state = {}
for _key, _cfg in AGENTS_CONFIG.items():
    agents_state[_key] = {
        "name": _cfg["name"],
        "status": "IDLE",
        "activity": "Siaga, menunggu perintah",
        "active": 0,
        "total_prompt_tokens": 0,
        "total_candidates_tokens": 0,
        "total_calls": 0,
        "last_call_time": "-",
    }

HISTORY = {}          # (agent_key, chat_id) -> list[types.Content], hanya di RAM
MAX_HISTORY = 12

# --- WEB (Flask) ---
app = Flask(__name__, static_folder=None)   # jangan sajikan file repo ke publik


def _ok():
    return bool(VIEW_TOKEN) and secrets.compare_digest(request.args.get("k", ""), VIEW_TOKEN)


@app.route("/")
def home():
    if not _ok():
        abort(404)
    return send_from_directory("office", "index.html")


@app.route("/api/all-status")
def api_all_status():
    if not _ok():
        abort(404)
    return jsonify(agents_state)


@app.route("/healthz")
def healthz():
    return "ok"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# --- STATUS HELPERS ---
def _begin(key):
    s = agents_state[key]
    s["active"] += 1
    s["status"] = "WORKING"
    s["activity"] = "Memproses permintaan"   # sengaja umum, jangan isi chat


def _end(key):
    s = agents_state[key]
    s["active"] = max(0, s["active"] - 1)
    if s["active"] == 0:
        s["status"] = "IDLE"
        s["activity"] = "Siaga, menunggu perintah"


# --- TELEGRAM ---
def _addressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    m = update.message
    if m.chat.type == "private":
        return True
    uname = (context.bot.username or "").lower()
    if uname and f"@{uname}" in (m.text or "").lower():
        return True
    r = m.reply_to_message
    return bool(r and r.from_user and r.from_user.id == context.bot.id)


def make_handlers(key):
    cfg = AGENTS_CONFIG[key]
    client = genai.Client(api_key=cfg["gemini_key"])

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"Halo, saya {cfg['name']}.\n"
            "Kirim pesan untuk mulai.\n"
            "/status untuk cek penggunaan API dan kesehatan server."
        )

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = agents_state[key]
        uptime = int(time.time() - START_TIME)
        total = s["total_prompt_tokens"] + s["total_candidates_tokens"]
        await update.message.reply_text(
            f"📊 STATUS & TOKEN: {cfg['name']}\n\n"
            f"• Status: {s['status']} ({s['activity']})\n"
            f"• Total panggilan API: {s['total_calls']} kali\n"
            f"• Total token terpakai: {total}\n"
            f"  - Prompt: {s['total_prompt_tokens']}\n"
            f"  - Output: {s['total_candidates_tokens']}\n"
            f"• Panggilan terakhir: {s['last_call_time']}\n\n"
            f"⚙️ Kesehatan server:\n"
            f"• CPU: {psutil.cpu_percent()}%\n"
            f"• RAM: {psutil.virtual_memory().percent}%\n"
            f"• Uptime: {uptime // 3600}j {(uptime % 3600) // 60}m\n"
            f"• Model: {MODEL}"
        )

    async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not msg.text or not _addressed(update, context):
            return
        text = msg.text
        if context.bot.username:
            text = text.replace(f"@{context.bot.username}", "").strip()
        if not text:
            return

        hist_key = (key, msg.chat_id)
        history = HISTORY.setdefault(hist_key, [])
        user_content = types.Content(role="user", parts=[types.Part(text=text)])

        _begin(key)
        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
            resp = await client.aio.models.generate_content(
                model=MODEL,
                contents=history + [user_content],
                config=types.GenerateContentConfig(system_instruction=cfg["prompt"]),
            )
            answer = (resp.text or "").strip() or "(Tidak ada jawaban dari model.)"

            s = agents_state[key]
            u = resp.usage_metadata
            if u:
                s["total_prompt_tokens"] += u.prompt_token_count or 0
                s["total_candidates_tokens"] += u.candidates_token_count or 0
            s["total_calls"] += 1
            s["last_call_time"] = datetime.now(WIB).strftime("%d/%m %H:%M:%S WIB")

            history.append(user_content)
            history.append(types.Content(role="model", parts=[types.Part(text=answer)]))
            del history[:-MAX_HISTORY]
        except Exception:
            log.exception("[%s] Gagal memanggil Gemini", cfg["name"])
            answer = "⚠️ Maaf, ada gangguan saat menghubungi Gemini. Coba lagi sebentar lagi."
        finally:
            _end(key)

        for i in range(0, len(answer), 4000):   # batas Telegram 4096 karakter
            await msg.reply_text(answer[i:i + 4000])

    return start_command, status_command, chat


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Error Telegram: %s", context.error)


async def run_bots():
    started = []
    for key, cfg in AGENTS_CONFIG.items():
        if not cfg["token"] or not cfg["gemini_key"]:
            log.warning("[%s] dilewati: TOKEN_%s / GEMINI_%s belum diisi", cfg["name"], key.upper(), key.upper())
            continue
        try:
            start_cmd, status_cmd, chat = make_handlers(key)
            tg = ApplicationBuilder().token(cfg["token"]).build()
            tg.add_handler(CommandHandler("start", start_cmd))
            tg.add_handler(CommandHandler(["status", "tokens"], status_cmd))
            tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
            tg.add_error_handler(on_error)
            await tg.initialize()
            await tg.start()
            await tg.updater.start_polling(drop_pending_updates=True)
            started.append(tg)
            log.info("[%s] online", cfg["name"])
        except Exception:
            log.exception("[%s] gagal start", cfg["name"])
    log.info("%d dari %d agen online", len(started), len(AGENTS_CONFIG))
    await asyncio.Event().wait()   # tahan proses tetap hidup


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_bots())
