import os
import random
import time
import threading
from datetime import datetime
import pytz
import psutil
import google.generativeai as genai

from flask import Flask, jsonify, render_template_string
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

START_TIME = time.time()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BOT_ROLE = os.getenv("BOT_ROLE", "devmatrix").lower()

genai.configure(api_key=GEMINI_API_KEY)

CONFIG = {
    "devmatrix": {"name": "MCI.DevMatrix", "role": "Asisten Koding & Hardware", "prompt": "Kamu adalah MCI.DevMatrix, asisten koding & arsitek logika hardware."},
    "nexus": {"name": "MCI.Nexus", "role": "Manajemen Proyek", "prompt": "Kamu adalah MCI.Nexus, pusat manajemen proyek dan inventaris."},
    "aegis": {"name": "MCI.Aegis", "role": "Safety Checklist", "prompt": "Kamu adalah MCI.Aegis, verifikator kritis dan safety checklist."},
    "sentinel": {"name": "MCI.Sentinel", "role": "Pemantau & Rangkuman", "prompt": "Kamu adalah MCI.Sentinel, pemantau dan pembuat rangkuman."},
    "pulse": {"name": "MCI.Pulse", "role": "Electrical Engineering", "prompt": "Kamu adalah MCI.Pulse, asisten Electrical Engineering."}
}

current_cfg = CONFIG.get(BOT_ROLE, CONFIG["devmatrix"])

# Status internal untuk kontrol posisi visual kantor
office_state = {
    "status": "IDLE",            # IDLE, WALKING, WORKING, EATING
    "activity": "Lagi santai di area sofa kantor...",
    "target_area": "sofa",       # sofa, desk, pantry
    "image_active": "idle_sofa.jpg"
}

app = Flask(__name__)

# --- DASHBOARD VISUAL 3D ISOMETRIC INTERAKTIF ---
HTML_OFFICE_3D = """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCI Office Virtual Monitor 3D</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #090d16; color: #f8fafc; font-family: 'Segoe UI', Tahoma, sans-serif; overflow: hidden; height: 100vh; display: flex; flex-direction: column; }
        
        .header { background: #1e293b; padding: 12px 20px; border-bottom: 2px solid #334155; display: flex; justify-content: space-between; align-items: center; z-index: 10; }
        .title { font-size: 18px; font-weight: bold; color: #38bdf8; display: flex; align-items: center; gap: 8px; }
        .live-tag { background: #ef4444; color: white; font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: bold; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

        /* Stage View-Only 3D Canvas */
        .viewport { flex: 1; position: relative; background: radial-gradient(circle, #1e293b 10%, #090d16 90%); overflow: hidden; cursor: grab; display: flex; align-items: center; justify-content: center; }
        .viewport:active { cursor: grabbing; }

        /* Isometric Office Room Grid Layout */
        .office-room { position: relative; width: 700px; height: 500px; background: rgba(30, 41, 59, 0.6); border: 3px solid #38bdf8; border-radius: 24px; transform: rotateX(55deg) rotateZ(-45deg); transform-style: preserve-3d; box-shadow: -20px 20px 50px rgba(0,0,0,0.6); transition: transform 0.1s ease-out; }

        /* Denah Sekat Ruangan Kantor */
        .zone { position: absolute; border: 2px dashed rgba(56, 189, 248, 0.3); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: bold; color: #94a3b8; }
        .zone-sofa { top: 30px; left: 30px; width: 200px; height: 180px; background: rgba(59, 130, 246, 0.1); }
        .zone-desk { bottom: 30px; right: 30px; width: 260px; height: 220px; background: rgba(234, 179, 8, 0.1); }
        .zone-pantry { top: 30px; right: 30px; width: 180px; height: 160px; background: rgba(74, 222, 128, 0.1); }

        /* Karakter & Animasi Gerak */
        .character-node { position: absolute; width: 100px; height: 100px; transition: all 2.5s cubic-bezier(0.4, 0, 0.2, 1); transform-style: preserve-3d; }
        .avatar-img { width: 100px; height: 100px; border-radius: 50%; border: 3px solid #38bdf8; background-size: cover; background-position: center; transform: rotateZ(45deg) rotateX(-55deg); box-shadow: 0 10px 25px rgba(0,0,0,0.5); animation: float 3s ease-in-out infinite; }
        
        @keyframes float { 0%, 100% { transform: rotateZ(45deg) rotateX(-55deg) translateY(0px); } 50% { transform: rotateZ(45deg) rotateX(-55deg) translateY(-10px); } }

        /* Gelembung Status Realtime di Atas Kepala */
        .speech-bubble { position: absolute; top: -50px; left: 50%; transform: translateX(-50%) rotateZ(45deg) rotateX(-55deg); background: #0284c7; color: white; padding: 6px 12px; border-radius: 12px; font-size: 11px; font-weight: bold; white-space: nowrap; box-shadow: 0 4px 12px rgba(0,0,0,0.4); z-index: 20; pointer-events: none; }

        .controls-hint { position: absolute; bottom: 15px; left: 20px; background: rgba(15, 23, 42, 0.8); padding: 8px 14px; border-radius: 8px; font-size: 12px; color: #94a3b8; border: 1px solid #334155; }
    </style>
</head>
<body>

    <div class="header">
        <div class="title"><span class="live-tag">LIVE</span> <span id="agent-name">MCI Office 3D</span></div>
        <div style="font-size: 12px; color: #94a3b8;">View-Only Interactive Floor</div>
    </div>

    <div class="viewport" id="viewport">
        <div class="office-room" id="room">
            <!-- Area Sekat Kantor -->
            <div class="zone zone-sofa">AREA SOFA (RESTING)</div>
            <div class="zone zone-desk">MEJA KERJA (WORKING)</div>
            <div class="zone zone-pantry">PANTRY / DAPUR</div>

            <!-- Karakter AI Realtime -->
            <div id="character" class="character-node" style="top: 60px; left: 60px;">
                <div id="bubble" class="speech-bubble">Standby di sofa...</div>
                <div id="avatar" class="avatar-img" style="background-image: url('/static/idle_sofa.jpg');"></div>
            </div>
        </div>
    </div>

    <div class="controls-hint">💡 Klik & Drag area luar buat geser/putar tampilan denah kantor</div>

    <script>
        // Fitur Keliling Denah Kantor (Drag & Rotasi)
        const viewport = document.getElementById('viewport');
        const room = document.getElementById('room');
        let isDragging = false, startX, startY, currentX = -45, currentY = 55;

        viewport.addEventListener('mousedown', (e) => { isDragging = true; startX = e.clientX; startY = e.clientY; });
        window.addEventListener('mouseup', () => isDragging = false);
        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            currentX += dx * 0.2;
            currentY = Math.max(20, Math.min(80, currentY - dy * 0.2));
            room.style.transform = `rotateX(${currentY}deg) rotateZ(${currentX}deg)`;
            startX = e.clientX;
            startY = e.clientY;
        });

        // Sinkronisasi Pergerakan Karakter Real-time dari Server Render
        async function syncOffice() {
            try {
                const res = await fetch('/api/live-status');
                const data = await res.json();

                document.getElementById('agent-name').innerText = data.role_name;
                document.getElementById('bubble').innerText = data.activity;
                
                const char = document.getElementById('character');
                const avatar = document.getElementById('avatar');

                // Update posisi koordinat di lantai denah kantor
                if (data.target_area === 'desk') {
                    char.style.top = '260px';
                    char.style.left = '420px';
                } else if (data.target_area === 'pantry') {
                    char.style.top = '60px';
                    char.style.left = '450px';
                } else {
                    char.style.top = '60px';
                    char.style.left = '60px';
                }

                // Ganti skin gambar
                avatar.style.backgroundImage = `url('/static/${data.image_active}')`;

            } catch (e) {
                console.error("Gagal sinkron", e);
            }
        }

        setInterval(syncOffice, 1500);
        syncOffice();
    </script>
</body>
</html>
"""

@app.route('/')
def live_monitor():
    return render_template_string(HTML_OFFICE_3D)

@app.route('/api/live-status')
def api_live_status():
    return jsonify({
        "role_name": current_cfg["name"],
        "status": office_state["status"],
        "activity": office_state["activity"],
        "target_area": office_state["target_area"],
        "image_active": office_state["image_active"]
    })

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- TELEGRAM BOT LOGIC ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text or "Dokumen terdeteksi"

    # 1. Fase Jalan dari Sofa ke Meja
    office_state["status"] = "WALKING"
    office_state["target_area"] = "desk"
    office_state["activity"] = "🚶 Lagi jalan menuju meja kerja..."
    office_state["image_active"] = "working_code.jpg"
    
    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open("working_code.jpg", "rb"), caption=f"🚶 {current_cfg['name']} lagi jalan ke meja kerja...")
    time.sleep(2.5)  # Jeda waktu animasi jalan di denah web

    # 2. Fase Ngerjain Tugas
    office_state["status"] = "WORKING"
    office_state["activity"] = f"💻 Ngetik: '{user_text[:20]}...'"

    model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=current_cfg["prompt"])
    try:
        response = model.generate_content(user_text)
        reply = response.text
    except Exception as e:
        reply = f"Error: {str(e)}"

    await update.message.reply_text(reply)

    # 3. Fase Jalan Balik ke Sofa / Pantry
    wib = pytz.timezone("Asia/Jakarta")
    now = datetime.now(wib)
    
    if now.hour == 12:
        office_state["status"] = "WALKING"
        office_state["target_area"] = "pantry"
        office_state["activity"] = "🍱 Tugas kelar, lagi jalan ke dapur buat makan..."
        office_state["image_active"] = "eating.jpg"
        time.sleep(2.5)
        office_state["status"] = "EATING"
    else:
        office_state["status"] = "WALKING"
        office_state["target_area"] = "sofa"
        office_state["activity"] = "☕ Tugas kelar, lagi jalan balik ke area sofa..."
        office_state["image_active"] = "idle_sofa.jpg"
        time.sleep(2.5)
        office_state["status"] = "IDLE"

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    telegram_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_message))
    print(f"Server 3D {current_cfg['name']} Running...")
    telegram_app.run_polling()
