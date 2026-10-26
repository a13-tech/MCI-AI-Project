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

# --- LOGGING CONFIG ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("mci.fable")

START_TIME = time.time()
WIB = pytz.timezone("Asia/Jakarta")

# --- MODEL CONSTANTS ---
PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-1.5-flash"]

# --- CORE MEMORY MCP (Global Codebase & Hardware Standards) ---
CODEBASE_MEMORY = (
    "\n\n[MCP MEMORY ENGINE v5.1]\n"
    "• Org: Mahardika Cakrawala Indonesia (MCI)\n"
    "• Tech: Python (Flask, python-telegram-bot), C/C++ (Arduino, PlatformIO), ESP32, Nano, RPi Pico W.\n"
    "• Hardware Standard: ESP32 I2C Default (SDA=21, SCL=22). Logic 3.3V (Must use LLC for 5V I2C/sensors).\n"
    "• Cloud: Render.com, Webhook/Polling Telegram Bot, Static Path: 'office'.\n"
    "• Agents: DevMatrix (Boss/Dev), Nexus (PM/Task), Aegis (Risk/Safety), Sentinel (Log/Summary), Pulse (EE/Power)."
)

# --- CAVEMAN MODE (Token Saver 50-75%) ---
CAVEMAN_INSTRUCTION = (
    "\n\n[SYSTEM: CAVEMAN MODE ACTIVE]\n"
    "1. Tanpa basa-basi/salam. Direct to point.\n"
    "2. Jawab singkat padat (bullet points).\n"
    "3. JIKA CODE: HANYA berikan full code siap copy tanpa penjelasan panjang.\n"
    "4. Presisi teknis, rumus, dan pinout 100% akurat."
)

# --- AGENT CONFIGURATIONS ---
AGENTS_CONFIG = {
    "devmatrix": {
        "name": "MCI.DevMatrix",
        "token": os.getenv("TOKEN_DEVMATRIX"),
        "gemini_key": os.getenv("GEMINI_DEVMATRIX"),
        "prompt": (
            "Kamu MCI.DevMatrix, Boss AI & Arsitek Utama MCI. Terima instruksi, tulis kode. "
            "Gunakan laporan sub-agent jika ada untuk menyusun solusi final."
            + CODEBASE_MEMORY
            + CAVEMAN_INSTRUCTION
        ),
    },
    "nexus": {
        "name": "MCI.Nexus",
        "token": os.getenv("TOKEN_NEXUS"),
        "gemini_key": os.getenv("GEMINI_NEXUS"),
        "prompt": (
            "Kamu MCI.Nexus, PM & Manager Tugas/Inventaris MCI."
            + CODEBASE_MEMORY
            + CAVEMAN_INSTRUCTION
        ),
    },
    "aegis": {
        "name": "MCI.Aegis",
        "token": os.getenv("TOKEN_AEGIS"),
        "gemini_key": os.getenv("GEMINI_AEGIS"),
        "prompt": (
            "Kamu MCI.Aegis, Verifikator Kritis & Risk Evaluator. Cek celah & bahaya hardware/software."
            + CODEBASE_MEMORY
            + CAVEMAN_INSTRUCTION
        ),
    },
    "sentinel": {
        "name": "MCI.Sentinel",
        "token": os.getenv("TOKEN_SENTINEL"),
        "gemini_key": os.getenv("GEMINI_SENTINEL"),
        "prompt": (
            "Kamu MCI.Sentinel, Summarizer & Diskusi Logger MCI."
            + CODEBASE_MEMORY
            + CAVEMAN_INSTRUCTION
        ),
    },
    "pulse": {
        "name": "MCI.Pulse",
        "token": os.getenv("TOKEN_PULSE"),
        "gemini_key": os.getenv("GEMINI_PULSE"),
        "prompt": (
            "Kamu MCI.Pulse, Electrical Engineering Specialist (Daya, Pinout, I2C/SPI/LoRa, Rumus EE)."
            + CODEBASE_MEMORY
            + CAVEMAN_INSTRUCTION
        ),
    },
}

# --- GLOBAL STATE (Sync with Virtual Office 3D) ---
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

HISTORY = {}
MAX_HISTORY = 8

# --- FLASK SERVER ---
app = Flask(__name__, static_folder="office", static_url_path="")


def _ok():
    token_in_env = os.getenv("VIEW_TOKEN", "").strip()
    token_in_url = request.args.get("k", "").strip()
    return bool(token_in_env) and secrets.compare_digest(token_in_url, token_in_env)


@app.route("/")
def home():
    if not _ok():
        abort(404)
    return send_from_directory("office", "index.html")


@app.route("/<path:filename>")
def serve_office_assets(filename):
    if not _ok():
        abort(404)
    return send_from_directory("office", filename)


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


# --- STATE MANAGERS ---
def _begin(key):
    s = agents_state[key]
    s["active"] += 1
    s["status"] = "WORKING"
    s["activity"] = "Sedang memproses..."


def _end(key):
    s = agents_state[key]
    s["active"] = max(0, s["active"] - 1)
    if s["active"] == 0:
        s["status"] = "IDLE"
        s["activity"] = "Siaga, menunggu perintah"


# --- GEMINI CLIENT ENGINE ---
async def call_gemini_api(api_key, system_prompt, history, user_content):
    client = genai.Client(api_key=api_key)
    models_to_try = [PRIMARY_MODEL] + FALLBACK_MODELS
    last_error = None

    for model_name in models_to_try:
        try:
            resp = await client.aio.models.generate_content(
                model=model_name,
                contents=history + [user_content],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=1000,
                ),
            )
            return resp
        except Exception as e:
            last_error = e
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                await asyncio.sleep(1)
                continue
            else:
                raise e
    raise last_error


# --- TELEGRAM HANDLERS ---
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

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"Fable 5.1 Engine Online. System: {cfg['name']}.\n/status for metrics."
        )

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        s = agents_state[key]
        uptime = int(time.time() - START_TIME)
        total = s["total_prompt_tokens"] + s["total_candidates_tokens"]
        await update.message.reply_text(
            f"📊 SYSTEM METRICS: {cfg['name']}\n\n"
            f"• State: {s['status']} ({s['activity']})\n"
            f"• API Calls: {s['total_calls']} times\n"
            f"• Tokens Used: {total}\n"
            f"  - Prompt: {s['total_prompt_tokens']}\n"
            f"  - Candidates: {s['total_candidates_tokens']}\n"
            f"• Last Ping: {s['last_call_time']}\n\n"
            f"⚙️ SERVER HEALTH:\n"
            f"• CPU: {psutil.cpu_percent()}%\n"
            f"• RAM: {psutil.virtual_memory().percent}%\n"
            f"• Uptime: {uptime // 3600}h {(uptime % 3600) // 60}m\n"
            f"• Engine Model: {PRIMARY_MODEL}"
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

        api_key = (cfg["gemini_key"] or "").strip()
        if not api_key:
            await msg.reply_text("⚠️ API Key Gemini missing in Environment Variables.")
            return

        hist_key = (key, msg.chat_id)
        history = HISTORY.setdefault(hist_key, [])
        user_content = types.Content(role="user", parts=[types.Part(text=text)])

        _begin(key)
        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")

            # --- BOSS AI ORCHESTRATOR ROUTER ---
            delegated_info = ""
            if key == "devmatrix":
                text_lower = text.lower()
                target_sub_agent = None

                if any(w in text_lower for w in ["volt", "ampere", "daya", "pinout", "i2c", "spi", "skema"]):
                    target_sub_agent = "pulse"
                elif any(w in text_lower for w in ["bahaya", "risiko", "cek celah", "keamanan", "safety"]):
                    target_sub_agent = "aegis"
                elif any(w in text_lower for w in ["tugas", "proyek", "jadwal", "inventaris"]):
                    target_sub_agent = "nexus"

                if target_sub_agent and AGENTS_CONFIG[target_sub_agent]["gemini_key"]:
                    sub_cfg = AGENTS_CONFIG[target_sub_agent]
                    _begin(target_sub_agent)
                    try:
                        sub_resp = await call_gemini_api(
                            sub_cfg["gemini_key"],
                            sub_cfg["prompt"],
                            [],
                            user_content,
                        )
                        sub_text = (sub_resp.text or "").strip()
                        delegated_info = f"\n\n[SUB-AGENT REPORT FROM {sub_cfg['name']}]:\n{sub_text}"
                    except Exception as sub_err:
                        log.warning("Delegation error to %s: %s", target_sub_agent, sub_err)
                    finally:
                        _end(target_sub_agent)

            final_user_content = user_content
            if delegated_info:
                combined_text = text + delegated_info
                final_user_content = types.Content(role="user", parts=[types.Part(text=combined_text)])

            resp = await call_gemini_api(api_key, cfg["prompt"], history, final_user_content)
            answer = (resp.text or "").strip() or "(No response generated.)"

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
        except Exception as e:
            log.exception("[%s] Execution error: %s", cfg["name"], e)
            answer = f"⚠️ Server Busy (503). Retrying required.\nDetail: {e}"
        finally:
            _end(key)

        for i in range(0, len(answer), 4000):
            await msg.reply_text(answer[i:i + 4000])

    return start_command, status_command, chat


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Telegram Error: %s", context.error)


async def run_bots():
    started = []
    for key, cfg in AGENTS_CONFIG.items():
        if not cfg["token"] or not cfg["gemini_key"]:
            log.warning("[%s] Skipped: TOKEN_%s / GEMINI_%s missing", cfg["name"], key.upper(), key.upper())
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
            log.info("[%s] Online and ready", cfg["name"])
        except Exception:
            log.exception("[%s] Startup failure", cfg["name"])
    log.info("%d of %d MCI Agents Online", len(started), len(AGENTS_CONFIG))
    await asyncio.Event().wait()


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_bots())
