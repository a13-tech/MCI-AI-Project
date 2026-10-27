import os
import asyncio
import json
from flask import Flask, render_template, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

app = Flask(__name__, template_folder="office", static_folder="office")

# --- CONFIG & GEMINI SETUP ---
# Menggunakan 1 API Key Pay-As-You-Go yang sama untuk seluruh bot
DEFAULT_GEMINI_KEY = os.getenv("GEMINI_DEVMATRIX") or os.getenv("GEMINI_APEX") or ""
VIEW_TOKEN = os.getenv("VIEW_TOKEN", "alvaro123")

BOT_CONFIGS = {
    "APEX": {"token_env": "TOKEN_APEX", "name": "MCI.Apex", "role": "Boss AI & Orchestrator Utama"},
    "DEVMATRIX": {"token_env": "TOKEN_DEVMATRIX", "name": "MCI.DevMatrix", "role": "Spesialis Koding & Microcontroller"},
    "PULSE": {"token_env": "TOKEN_PULSE", "name": "MCI.Pulse", "role": "Spesialis Hardware & Sirkuit IoT"},
    "AEGIS": {"token_env": "TOKEN_AEGIS", "name": "MCI.Aegis", "role": "Keamanan & Audit System"},
    "NEXUS": {"token_env": "TOKEN_NEXUS", "name": "MCI.Nexus", "role": "Project Manager & Inventaris"},
    "SENTINEL": {"token_env": "TOKEN_SENTINEL", "name": "MCI.Sentinel", "role": "Uptime Logger & System Monitor"},
    "ATLAS": {"token_env": "TOKEN_ATLAS", "name": "MCI.Atlas", "role": "Riset Komponen & Roadmap Planner"},
    "SCRIBE": {"token_env": "TOKEN_SCRIBE", "name": "MCI.Scribe", "role": "Dokumentasi & Pembuat Laporan"},
    "LEDGER": {"token_env": "TOKEN_LEDGER", "name": "MCI.Ledger", "role": "Keuangan, Kas & Analisis Struk"},
    "HERALD": {"token_env": "TOKEN_HERALD", "name": "MCI.Herald", "role": "Humas & Broadcast Pengumuman"}
}

def ask_gemini(bot_key, prompt, system_instruction):
    api_key = os.getenv(f"GEMINI_{bot_key}") or DEFAULT_GEMINI_KEY
    if not api_key:
        return "Error: API Key Gemini belum dikonfigurasi."
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_instruction
        )
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error Gemini: {str(e)}"

# --- TELEGRAM BOT HANDLERS ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_name = context.bot_data.get("name", "MCI Bot")
    role = context.bot_data.get("role", "Staf MCI")
    await update.message.reply_text(f"Halo! Saya {bot_name} ({role}). Ada yang bisa dibantu?")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_key = context.bot_data.get("key")
    bot_name = context.bot_data.get("name")
    role = context.bot_data.get("role")
    
    user_text = update.message.text
    sys_prompt = f"Kamu adalah {bot_name}, {role} di Mahardika Cakrawala Indonesia (MCI). Jawab dengan padat, jelas, akurat, dan gunakan Caveman Mode (langsung ke inti tanpa basa-basi)."
    
    reply = ask_gemini(bot_key, user_text, sys_prompt)
    await update.message.reply_text(reply)

# --- WEB SERVER ROUTES ---
@app.route('/')
def index():
    k = request.args.get('k')
    if k != VIEW_TOKEN:
        return "Akses Ditolak: Password (VIEW_TOKEN) salah atau tidak ditemukan.", 403
    return render_template('index.html')

@app.route('/api/command', methods=['POST'])
def handle_command():
    data = request.json or {}
    command = data.get("command", "")
    if not command:
        return jsonify({"status": "error", "message": "Perintah kosong"}), 400
    
    sys_prompt = "Kamu adalah MCI.Apex, Boss AI MCI. Analisis perintah pengguna dan pecah menjadi delegasi tugas untuk staf 9 bot MCI lainnya dalam format JSON."
    reply = ask_gemini("APEX", command, sys_prompt)
    return jsonify({"status": "success", "response": reply})

# --- ASYNC BOT RUNNER ---
async def start_bots():
    apps = []
    for key, config in BOT_CONFIGS.items():
        token = os.getenv(config["token_env"])
        if token:
            application = Application.builder().token(token).build()
            application.bot_data["key"] = key
            application.bot_data["name"] = config["name"]
            application.bot_data["role"] = config["role"]
            
            application.add_handler(CommandHandler("start", start_handler))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
            
            await application.initialize()
            await application.start()
            await application.updater.start_polling()
            apps.append(application)
            print(f"[{config['name']}] Berhasil aktif!")
        else:
            print(f"[{config['name']}] Dibatalkan: Token tidak ditemukan.")
    return apps

if __name__ == '__main__':
    # Memulai bot Telegram secara background di event loop
    loop = asyncio.get_event_loop()
    loop.create_task(start_bots())
    
    # Jalankan server web Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
