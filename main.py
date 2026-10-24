import os
import time
import threading
from datetime import datetime
import pytz
import psutil
import asyncio
import google.generativeai as genai

from flask import Flask, jsonify, render_template_string
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

START_TIME = time.time()

# --- CONFIG 5 DIVISI MCI (API KEY & TOKEN TELEGRAM TERPISAH) ---
AGENTS_CONFIG = {
    "devmatrix": {
        "name": "MCI.DevMatrix",
        "token": os.getenv("TOKEN_DEVMATRIX"),
        "gemini_key": os.getenv("GEMINI_DEVMATRIX"),
        "prompt": "Kamu adalah MCI.DevMatrix, asisten koding & arsitek hardware.",
        "desk_pos": {"top": "300px", "left": "80px"},
        "sprite_work": "working_code.jpg"
    },
    "nexus": {
        "name": "MCI.Nexus",
        "token": os.getenv("TOKEN_NEXUS"),
        "gemini_key": os.getenv("GEMINI_NEXUS"),
        "prompt": "Kamu adalah MCI.Nexus, pusat manajemen proyek dan inventaris.",
        "desk_pos": {"top": "300px", "left": "220px"},
        "sprite_work": "working_doc.jpg"
    },
    "aegis": {
        "name": "MCI.Aegis",
        "token": os.getenv("TOKEN_AEGIS"),
        "gemini_key": os.getenv("GEMINI_AEGIS"),
        "prompt": "Kamu adalah MCI.Aegis, verifikator kritis dan safety checklist.",
        "desk_pos": {"top": "300px", "left": "360px"},
        "sprite_work": "working_doc.jpg"
    },
    "sentinel": {
        "name": "MCI.Sentinel",
        "token": os.getenv("TOKEN_SENTINEL"),
        "gemini_key": os.getenv("GEMINI_SENTINEL"),
        "prompt": "Kamu adalah MCI.Sentinel, pemantau dan pembuat rangkuman.",
        "desk_pos": {"top": "300px", "left": "500px"},
        "sprite_work": "working_doc.jpg"
    },
    "pulse": {
        "name": "MCI.Pulse",
        "token": os.getenv("TOKEN_PULSE"),
        "gemini_key": os.getenv("GEMINI_PULSE"),
        "prompt": "Kamu adalah MCI.Pulse, asisten Electrical Engineering.",
        "desk_pos": {"top": "120px", "left": "500px"},
        "sprite_work": "working_code.jpg"
    }
}

# --- STATE GLOBAL UNTUK SIMULASI 3D & TRACKING TOKEN ---
agents_state = {}
for key, cfg in AGENTS_CONFIG.items():
    agents_state[key] = {
        "name": cfg["name"],
        "status": "IDLE",
        "activity": "Standby di area sofa...",
        "target": "sofa",
        "image": "idle_sofa.jpg",
        "desk_pos": cfg["desk_pos"],
        "total_prompt_tokens": 0,
        "total_candidates_tokens": 0,
        "total_calls": 0,
        "last_call_time": "-"
    }

app = Flask(__name__)

# --- WEB DASHBOARD MASTER 3D ISOMETRIC ---
HTML_MASTER = """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCI HQ - Central Virtual Office 3D</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #090d16; color: #f8fafc; font-family: 'Segoe UI', Tahoma, sans-serif; overflow: hidden; height: 100vh; display: flex; flex-direction: column; }
        
        .header { background: #1e293b; padding: 12px 20px; border-bottom: 2px solid #334155; display: flex; justify-content: space-between; align-items: center; z-index: 10; }
        .title { font-size: 18px; font-weight: bold; color: #38bdf8; display: flex; align-items: center; gap: 8px; }
        .live-tag { background: #ef4444; color: white; font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: bold; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

        .viewport { flex: 1; position: relative; background: radial-gradient(circle, #1e293b 10%, #090d16 90%); overflow: hidden; cursor: grab; display: flex; align-items: center; justify-content: center; }
        .viewport:active { cursor: grabbing; }

        /* Denah Kantor 3D Isometric */
        .office-room { position: relative; width: 850px; height: 580px; background: rgba(30, 41, 59, 0.7); border: 3px solid #38bdf8; border-radius: 24px; transform: rotateX(55deg) rotateZ(-45deg); transform-style: preserve-3d; box-shadow: -20px 20px 50px rgba(0,0,0,0.6); transition: transform 0.1s ease-out; }

        /* Area Ruangan */
        .zone { position: absolute; border: 2px dashed rgba(56, 189, 248, 0.3); border-radius: 12px; font-size: 11px; font-weight: bold; color: #94a3b8; padding: 6px; }
        .zone-sofa { top: 30px; left: 30px; width: 220px; height: 220px; background: rgba(59, 130, 246, 0.1); }
        .zone-desks { bottom: 30px; left: 30px; width: 780px; height: 220px; background: rgba(234, 179, 8, 0.1); }
        .zone-pantry { top: 30px; right: 30px; width: 220px; height: 220px; background: rgba(74, 222, 128, 0.1); }

        /* Character Node */
        .character-node { position: absolute; width: 75px; height: 75px; transition: all 2.5s cubic-bezier(0.4, 0, 0.2, 1); transform-style: preserve-3d; }
        .avatar-img { width: 75px; height: 75px; border-radius: 50%; border: 2px solid #38bdf8; background-size: cover; background-position: center; transform: rotateZ(45deg) rotateX(-55deg); box-shadow: 0 8px 20px rgba(0,0,0,0.5); animation: float 3s ease-in-out infinite; }
        
        @keyframes float { 0%, 100% { transform: rotateZ(45deg) rotateX(-55deg) translateY(0px); } 50% { transform: rotateZ(45deg) rotateX(-55deg) translateY(-8px); } }

        /* Speech Bubble Realtime */
        .speech-bubble { position: absolute; top: -45px; left: 50%; transform: translateX(-50%) rotateZ(45deg) rotateX(-55deg); background: #0284c7; color: white; padding: 4px 10px; border-radius: 10px; font-size: 10px; font-weight: bold; white-space: nowrap; pointer-events: none; box-shadow: 0 4px 12px rgba(0,0,0,0.4); z-index: 20; }
        .token-tag { position: absolute; bottom: -20px; left: 50%; transform: translateX(-50%) rotateZ(45deg) rotateX(-55deg); background: rgba(15, 23, 42, 0.9); color: #38bdf8; padding: 2px 6px; border-radius: 4px; font-size: 8px; white-space: nowrap; border: 1px solid #38bdf8; }

        .hint { position: absolute; bottom: 15px; left: 20px; background: rgba(15, 23, 42, 0.85); padding: 8px 14px; border-radius: 8px; font-size: 12px; color: #94a3b8; border: 1px solid #334155; }
    </style>
</head>
<body>

    <div class="header">
        <div class="title"><span class="live-tag">LIVE</span> <span>MCI Virtual HQ (5 Divisi AI)</span></div>
        <div style="font-size: 12px; color: #94a3b8;">View-Only Monitor</div>
    </div>

    <div class="viewport" id="viewport">
        <div class="office-room" id="room">
            <div class="zone zone-sofa">AREA SOFA (RESTING)</div>
            <div class="zone zone-desks">BILIK MEJA KERJA DIVISI MCI</div>
            <div class="zone zone-pantry">PANTRY / DAPUR</div>

            <div id="characters-container"></div>
        </div>
    </div>

    <div class="hint">💡 Klik & Drag untuk putar/geser tampilan denah kantor 3D</div>

    <script>
        const viewport = document.getElementById('viewport');
        const room = document.getElementById('room');
        let isDragging = false, startX, startY, currentX = -45, currentY = 55;

        viewport.addEventListener('mousedown', (e) => { isDragging = true; startX = e.clientX; startY = e.clientY; });
        window.addEventListener('mouseup', () => isDragging = false);
        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            currentX += (e.clientX - startX) * 0.2;
            currentY = Math.max(20, Math.min(80, currentY - (e.clientY - startY) * 0.2));
            room.style.transform = `rotateX(${currentY}deg) rotateZ(${currentX}deg)`;
            startX = e.clientX; startY = e.clientY;
        });

        async function syncOffice() {
            try {
                const res = await fetch('/api/all-status');
                const data = await res.json();
                const container = document.getElementById('characters-container');

                for (const [key, agent] of Object.entries(data)) {
                    let charEle = document.getElementById(`char-${key}`);
                    if (!charEle) {
                        charEle = document.createElement('div');
                        charEle.id = `char-${key}`;
                        charEle.className = 'character-node';
                        charEle.innerHTML = `
                            <div id="bubble-${key}" class="speech-bubble"></div>
                            <div id="avatar-${key}" class="avatar-img"></div>
                            <div id="token-${key}" class="token-tag"></div>
                        `;
                        container.appendChild(charEle);
                    }

                    document.getElementById(`bubble-${key}`).innerText = `${agent.name}: ${agent.activity}`;
                    document.getElementById(`avatar-${key}`).style.backgroundImage = `url('/static/${agent.image}')`;
                    document.getElementById(`token-${key}`).innerText = `Calls: ${agent.total_calls} | Tokens: ${agent.total_prompt_tokens + agent.total_candidates_tokens}`;

                    // Posisi Karakter
                    if (agent.target === 'desk') {
                        charEle.style.top = agent.desk_pos.top;
                        charEle.style.left = agent.desk_pos.left;
                    } else if (agent.target === 'pantry') {
                        charEle.style.top = '70px';
                        charEle.style.left = '640px';
                    } else {
                        const sofaOffsets = { devmatrix: 40, nexus: 75, aegis: 110, sentinel: 145, pulse: 180 };
                        charEle.style.top = '70px';
                        charEle.style.left = `${sofaOffsets[key] || 40}px`;
                    }
                }
            } catch (e) { console.error("Sync error", e); }
        }

        setInterval(syncOffice, 1500);
        syncOffice();
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_MASTER)

@app.route('/api/all-status')
def api_all_status():
    return jsonify(agents_state)

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- TELEGRAM BOT LOGIC & COMMAND HANDLERS ---
def make_bot_handlers(agent_key):
    cfg = AGENTS_CONFIG[agent_key]

    # Command /status atau /tokens untuk cek penggunaan API Gemini
    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = agents_state[agent_key]
        wib = pytz.timezone("Asia/Jakarta")
        uptime = int(time.time() - START_TIME)
        
        tot_tokens = state["total_prompt_tokens"] + state["total_candidates_tokens"]
        msg = (
            f"📊 **STATUS & TOKENS: {cfg['name']}**\n\n"
            f"• Status Lokasi: `{state['status']}` ({state['target']})\n"
            f"• Total Panggilan API: `{state['total_calls']} kali`\n"
            f"• Total Token Terpakai: `{tot_tokens} tokens`\n"
            f"  - Prompt Token: `{state['total_prompt_tokens']}`\n"
            f"  - Output Token: `{state['total_candidates_tokens']}`\n"
            f"• Pemanggilan Terakhir: `{state['last_call_time']}`\n\n"
            f"⚙️ **Server Health Check:**\n"
            f"• CPU Usage: `{psutil.cpu_percent()}%`\n"
            f"• RAM Usage: `{psutil.virtual_memory().percent}%`\n"
            f"• Uptime Server: `{uptime // 3600}j {(uptime % 3600) // 60}m`\n"
            f"• Limit Gemini Free: ~15 RPM / 1M TPM (Aman)"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    # Handler Pesan Biasa
    async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_text = update.message.text or "Dokumen terdeteksi"
        state = agents_state[agent_key]

        # 1. Animasi Jalan ke Meja
        state["status"] = "WALKING"
        state["target"] = "desk"
        state["activity"] = "🚶 Jalan ke meja..."
        state["image"] = cfg["sprite_work"]
        
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(cfg["sprite_work"], "rb"), caption=f"🚶 {cfg['name']} menuju meja kerja...")
        await asyncio.sleep(2.5)

        # 2. Eksekusi Gemini
        state["status"] = "WORKING"
        state["activity"] = f"💻 Ngetik: '{user_text[:12]}...'"

        if cfg["gemini_key"]:
            genai.configure(api_key=cfg["gemini_key"])
            model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=cfg["prompt"])
            try:
                res = model.generate_content(user_text)
                reply = res.text
                
                # Tracking Token Usage
                if hasattr(res, 'usage_metadata') and res.usage_metadata:
                    state["total_prompt_tokens"] += res.usage_metadata.prompt_token_count
                    state["total_candidates_tokens"] += res.usage_metadata.candidates_token_count
                
                state["total_calls"] += 1
                wib = pytz.timezone("Asia/Jakarta")
                state["last_call_time"] = datetime.now(wib).strftime("%H:%M:%S WIB")

            except Exception as e:
                reply = f"Error Gemini API: {str(e)}"
        else:
            reply = "API Key Gemini belum di-set di Environment Variables Render!"

        await update.message.reply_text(reply)

        # 3. Animasi Jalan Balik ke Sofa / Pantry
        wib = pytz.timezone("Asia/Jakarta")
        if datetime.now(wib).hour == 12:
            state["target"] = "pantry"
            state["activity"] = "🍱 Lagi makan di dapur..."
            state["image"] = "eating.jpg"
        else:
            state["target"] = "sofa"
            state["activity"] = "☕ Balik santai di sofa..."
            state["image"] = "idle_sofa.jpg"
            
        state["status"] = "IDLE"

    return status_command, message_handler

async def start_all_bots():
    apps = []
    for key, cfg in AGENTS_CONFIG.items():
        if cfg["token"]:
            bot_app = ApplicationBuilder().token(cfg["token"]).build()
            status_h, msg_h = make_bot_handlers(key)
            
            bot_app.add_handler(CommandHandler(["status", "tokens"], status_h))
            bot_app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, msg_h))
            
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling()
            apps.append(bot_app)
            print(f"Bot {cfg['name']} Aktif!")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(start_all_bots())
