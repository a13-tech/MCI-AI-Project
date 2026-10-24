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
    "devmatrix": {"name": "MCI.DevMatrix", "role": "Asisten Koding & Arsitek Hardware", "prompt": "Kamu adalah MCI.DevMatrix, asisten koding & arsitek logika hardware (C/C++, Python, mikrokontroler). Jawab teknis, lugas, dan solutif."},
    "nexus": {"name": "MCI.Nexus", "role": "Pusat Manajemen Proyek & Inventaris", "prompt": "Kamu adalah MCI.Nexus, pusat manajemen proyek, inventaris, & tugas tim. Jawab terstruktur, rapi, dan sistematis."},
    "aegis": {"name": "MCI.Aegis", "role": "Verifikator Kritis & Safety Checklist", "prompt": "Kamu adalah MCI.Aegis, verifikator kritis, evaluasi risiko, & safety checklist sistem. Jawab analitis, kritis, dan fokus pada keamanan/risiko."},
    "sentinel": {"name": "MCI.Sentinel", "role": "Pemantau & Rangkuman Poin Penting", "prompt": "Kamu adalah MCI.Sentinel, pemantau & pembuat rangkuman poin penting. Jawab singkat, padat, dan langsung ke poin utama."},
    "pulse": {"name": "MCI.Pulse", "role": "Asisten Electrical Engineering", "prompt": "Kamu adalah MCI.Pulse, asisten Electrical Engineering (kalkulasi daya, pinout, I2C/SPI/LoRa). Jawab akurat berbasis perhitungan elektro."}
}

current_cfg = CONFIG.get(BOT_ROLE, CONFIG["devmatrix"])

office_state = {
    "status": "IDLE",
    "activity": "Lagi santai di sofa kantor...",
    "position": "sofa"
}

app = Flask(__name__)

HTML_VIEW_ONLY = """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCI Office - Live Monitor</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0b0f19; color: #f8fafc; font-family: 'Segoe UI', sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        .header { background: #1e293b; padding: 15px 25px; border-bottom: 2px solid #334155; display: flex; justify-content: space-between; align-items: center; }
        .title { font-size: 20px; font-weight: bold; color: #38bdf8; display: flex; align-items: center; gap: 10px; }
        .live-tag { background: #ef4444; color: white; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
        .office-stage { flex: 1; position: relative; background: #090d16; display: flex; justify-content: center; align-items: center; background-image: radial-gradient(#1e293b 1px, transparent 1px); background-size: 20px 20px; }
        .avatar-card { position: relative; width: 320px; background: #1e293b; border: 2px solid #38bdf8; border-radius: 20px; padding: 25px; text-align: center; box-shadow: 0 0 30px rgba(56, 189, 248, 0.2); transition: all 0.8s ease-in-out; }
        .sprite-box { width: 130px; height: 130px; margin: 0 auto 15px; border-radius: 50%; border: 4px solid #38bdf8; background-size: cover; background-position: center; transition: all 0.5s ease; }
        .speech-bubble { position: absolute; top: -35px; left: 50%; transform: translateX(-50%); background: #0284c7; color: white; padding: 6px 14px; border-radius: 12px; font-size: 13px; font-weight: 600; white-space: nowrap; }
        .anim-idle { transform: translateY(0px); }
        .anim-walking { transform: scale(1.05); }
        .anim-working { transform: scale(0.98); }
        .status-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-weight: bold; margin-top: 8px; font-size: 12px; }
        .bg-idle { background: #3b82f6; color: white; }
        .bg-walk { background: #f97316; color: white; }
        .bg-work { background: #eab308; color: black; }
    </style>
</head>
<body>
    <div class="header">
        <div class="title"><span class="live-tag">LIVE</span> <span id="agent-name">MCI Office</span></div>
        <div style="font-size: 13px; color: #94a3b8;">Read-Only View Monitor</div>
    </div>
    <div class="office-stage">
        <div id="card" class="avatar-card">
            <div id="bubble" class="speech-bubble">Lagi stand-by...</div>
            <div id="sprite" class="sprite-box anim-idle" style="background-image: url('/static/idle_sofa.jpg');"></div>
            <h2 id="role-title" style="color: #f8fafc; font-size: 18px;">-</h2>
            <div style="margin-top: 10px; font-size: 14px; color: #94a3b8;" id="activity-desc">Menunggu perintah...</div>
            <div id="badge" class="status-badge bg-idle">STANDBY</div>
        </div>
    </div>
    <script>
        async function syncOffice() {
            try {
                const res = await fetch('/api/live-status');
                const data = await res.json();
                document.getElementById('agent-name').innerText = data.role_name;
                document.getElementById('role-title').innerText = data.role_name;
                document.getElementById('bubble').innerText = data.activity;
                document.getElementById('activity-desc').innerText = "Lokasi: " + data.position;
                
                const sprite = document.getElementById('sprite');
                const badge = document.getElementById('badge');

                if (data.status === 'WALKING') {
                    sprite.className = "sprite-box anim-walking";
                    sprite.style.borderColor = "#f97316";
                    badge.className = "status-badge bg-walk";
                    badge.innerText = "MOVING TO DESK";
                } else if (data.status === 'WORKING') {
                    sprite.className = "sprite-box anim-working";
                    sprite.style.borderColor = "#eab308";
                    badge.className = "status-badge bg-work";
                    badge.innerText = "BUSY / WORKING";
                } else {
                    sprite.className = "sprite-box anim-idle";
                    sprite.style.borderColor = "#38bdf8";
                    badge.className = "status-badge bg-idle";
                    badge.innerText = "IDLE / RESTING";
                }
            } catch (e) { console.error("Error sync", e); }
        }
        setInterval(syncOffice, 1500);
        syncOffice();
    </script>
</body>
</html>
"""

@app.route('/')
def live_monitor():
    return render_template_string(HTML_VIEW_ONLY)

@app.route('/api/live-status')
def api_live_status():
    return jsonify({
        "role_name": current_cfg["name"],
        "status": office_state["status"],
        "activity": office_state["activity"],
        "position": office_state["position"]
    })

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text or "Dokumen terdeteksi"

    # 1. Animasi Jalan dari Sofa ke Meja
    office_state["status"] = "WALKING"
    office_state["position"] = "Jalan ke Meja Kerja..."
    office_state["activity"] = "🚶 Karakter lagi jalan menuju meja kerja..."
    
    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open("working_code.jpg", "rb"), caption=f"🚶 {current_cfg['name']} lagi jalan ke meja kerja...")
    time.sleep(3) # Delay simulasi animasi jalan

    # 2. Sampai Meja & Ngerjain
    office_state["status"] = "WORKING"
    office_state["position"] = "Meja Komputer"
    office_state["activity"] = f"💻 Ngetik: '{user_text[:20]}...'"

    model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=current_cfg["prompt"])
    try:
        response = model.generate_content(user_text)
        reply = response.text
    except Exception as e:
        reply = f"Error: {str(e)}"

    await update.message.reply_text(reply)

    # 3. Animasi Jalan Balik ke Sofa
    office_state["status"] = "WALKING"
    office_state["position"] = "Jalan Balik ke Sofa..."
    office_state["activity"] = "🚶 Tugas selesai, lagi jalan balik ke area sofa..."
    time.sleep(3)

    # 4. Mode Santai Balik
    wib = pytz.timezone("Asia/Jakarta")
    now = datetime.now(wib)
    if now.hour == 12:
        office_state["status"] = "EATING"
        office_state["position"] = "Area Dapur"
        office_state["activity"] = "🍱 Lagi makan siang..."
    else:
        office_state["status"] = "IDLE"
        office_state["position"] = "Sofa Kantor"
        office_state["activity"] = "☕ Balik nyantai di sofa kantor..."

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    telegram_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_message))
    print(f"Server {current_cfg['name']} Aktif...")
    telegram_app.run_polling()
