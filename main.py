"""MCI HQ: 11 bot di 4 lantai (10 agen AI Gemini + MCI.Canvas pelukis Cloudflare), Boss pembagi tugas, Sidang Dewan, Skills,
mode Caveman, MCP, keuangan AI, dan web kantor virtual 3D.

Prinsip hemat token (Gemini Tier 1):
1. Router lokal tanpa AI: pesan yang jelas tujuannya langsung ke bot yang tepat, Boss tidak dipanggil.
2. Skill dipicu kata kunci: instruksi lengkap skill hanya dikirim saat dipakai.
3. Boss & bot "cepat" memakai model Flash-Lite, thinking diset paling rendah.
4. Hasil 1 tugas tidak dirangkum ulang; hasil pendek digabung tanpa AI.
5. Cache jawaban identik 15 menit, pembatas RPM, dan batas token harian.

Studio Gambar: Cloudflare Workers AI (FLUX schnell + img2img), gratis 10.000 neuron/hari.
"""
import os, io, csv, json, re, time, gzip, base64, asyncio, secrets, logging, sqlite3, threading, contextlib, hashlib, zipfile
from collections import deque, OrderedDict
from datetime import datetime, timedelta, timezone
from html import unescape as _unesc

import httpx, pytz, psutil
from flask import Flask, jsonify, abort, request, send_from_directory, make_response, redirect, Response
from pydantic import BaseModel
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

try:
    import psycopg2
except ImportError:
    psycopg2 = None
try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    ClientSession = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URL httpx memuat token bot Telegram
log = logging.getLogger("mci")

START = time.time()
WIB = pytz.timezone("Asia/Jakarta")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MODEL_LITE = os.getenv("GEMINI_MODEL_LITE", os.getenv("GEMINI_MODEL_BOSS", "gemini-3.5-flash-lite"))
RPM = max(1, int(os.getenv("GEMINI_RPM", "60")))
DAILY_LIMIT = int(os.getenv("DAILY_TOKEN_LIMIT", "0"))      # 0 = tanpa batas
MAX_TASKS = max(1, min(4, int(os.getenv("BOSS_MAX_TASKS", "3"))))
COUNCIL_MAX = max(2, min(6, int(os.getenv("COUNCIL_MAX", "4"))))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
VIEW_TOKEN = os.getenv("VIEW_TOKEN", "")
# Anggota: tiap orang punya token pribadi (diawali nama depan). Hak akses = gabungan jabatan + divisi.
# Maks 2 divisi dan 3 jabatan per orang. Dev mengatur lewat web → Anggota.
DIVISI = {"management": "Management", "creative": "Creative Project", "ss": "Secret Service", "dokumentasi": "Dokumentasi"}
JABATAN = {"admin": "Admin/Dev", "manager": "Manager", "pj": "PJ Divisi", "sekretaris": "Sekretaris", "bendahara": "Bendahara",
           "designer": "Designer", "anggota": "Anggota biasa"}
_BASE = {"boss", "kas", "gambar_lihat", "memori", "skills", "tim", "stok", "catatan", "polling", "dok_lihat"}
JABATAN_PERM = {
    "admin": {"*"},
    "manager": _BASE | {"laporan_lihat", "polling_buat", "polling_semua"},
    "pj": _BASE | {"polling_buat"},
    "sekretaris": _BASE | {"catatan_kelola", "laporan_lihat"},
    "bendahara": _BASE | {"kas_edit", "hutang"},
    "designer": _BASE | {"gambar_buat"},
    "anggota": set(_BASE),
}
DIVISI_PERM = {"ss": {"blacklist"}, "dokumentasi": {"dok_kelola", "gambar_buat"}}
# Divisi & jabatan bisa ditambah/diubah Dev dari web (disimpan di tabel pengaturan, kunci "struktur").
# Yang bawaan tidak bisa dihapus; Admin/Dev selalu punya semua izin dan tidak bisa diubah.
_DEF = {"divisi": (dict(DIVISI), {k: set(v) for k, v in DIVISI_PERM.items()}),
        "jabatan": (dict(JABATAN), {k: set(v) for k, v in JABATAN_PERM.items()})}
IZIN_PILIHAN = {  # izin yang boleh dipasang ke divisi/jabatan (Sidang Dewan, Lab IoT, Anggota tetap khusus Dev)
    "boss": "Boss bagi tugas (maks 3x/hari)", "kas": "Lihat Keuangan AI", "kas_edit": "Catat & hapus kas", "hutang": "Kelola hutang",
    "gambar_lihat": "Lihat galeri Studio Gambar", "gambar_buat": "Buat gambar (maks 5x/hari)", "memori": "Lihat Memori", "skills": "Lihat Skills",
    "tim": "Lihat Tim & Token", "stok": "Lihat Stok Barang", "catatan": "Catatan", "catatan_kelola": "Kelola catatan bersama",
    "polling": "Ikut voting", "polling_buat": "Buat polling (divisi sendiri)", "polling_semua": "Buat polling untuk semua anggota",
    "dok_lihat": "Lihat Dokumentasi", "dok_kelola": "Kelola Dokumentasi", "laporan_lihat": "Lihat Laporan Mingguan", "blacklist": "Blacklist",
}
STRUKTUR_MAKS = {"divisi": 20, "jabatan": 30}
MEMBERS = {}   # id -> {id, nama, token, divisi[], jabatan[], aktif}
HUTANG_MAX = max(0, int(os.getenv("HUTANG_MAX", "15000")))      # batas pinjaman per anggota per bulan
HUTANG_HARI = max(1, int(os.getenv("HUTANG_HARI", "3")))         # jatuh tempo H+3
KUOTA = {"boss": max(0, int(os.getenv("KUOTA_BOSS", "3"))), "gambar": max(0, int(os.getenv("KUOTA_GAMBAR", "5")))}
MAIL_WEBHOOK = os.getenv("MAIL_WEBHOOK", "").strip()       # URL Google Apps Script (Render gratis memblokir port SMTP)
MAIL_SECRET = os.getenv("MAIL_SECRET", "").strip()
MAIL_TO = os.getenv("MAIL_TO", "mci.corp.indonesia@gmail.com").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "")
PG = bool(DATABASE_URL and psycopg2)
DB_PATH = os.getenv("DB_PATH", "mci.db")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x.isdigit()}
CF_ACCOUNT = os.getenv("CF_ACCOUNT_ID", "").strip()
CF_TOKEN = os.getenv("CF_API_TOKEN", "").strip()
CF_BASE = os.getenv("CF_API_BASE", "https://api.cloudflare.com/client/v4").rstrip("/")
CF_MODEL = os.getenv("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
CF_EDIT_MODEL = os.getenv("CF_EDIT_MODEL", "@cf/runwayml/stable-diffusion-v1-5-img2img")
IMG_DAILY = max(0, int(os.getenv("IMG_DAILY_LIMIT", "150")))   # FLUX 1024px ~58 neuron, jatah gratis 10.000/hari
IMG_KEEP = max(10, int(os.getenv("IMG_KEEP", "60")))
IOT_KEY = os.getenv("IOT_KEY", "").strip()                      # kunci rahasia perangkat ESP32 (header X-IOT-KEY)
IOT_DAYS = max(1, int(os.getenv("IOT_DAYS", "7")))              # data sensor disimpan berapa hari             # jumlah gambar yang disimpan di galeri
try:
    MCP_SERVERS = json.loads(os.getenv("MCP_SERVERS", "[]"))
except ValueError:
    MCP_SERVERS = []

CAVEMAN = ("\n\nMODE CAVEMAN AKTIF: jawab sesingkat mungkin, kalimat pendek gaya telegram, tanpa basa-basi, "
           "tanpa pembuka atau penutup. Kode, angka, satuan, dan peringatan keselamatan tetap utuh dan akurat.")

# ---------- 4 LANTAI & DIVISI ----------
LANTAI = {1: "Lobby & Santai", 2: "Divisi Teknik", 3: "Operasional & Keuangan", 4: "Eksekutif"}


def _ag(nama, lantai, divisi, peran, prompt, kata, boss=False, cepat=False):
    return {"nama": "MCI." + nama, "lantai": lantai, "divisi": divisi, "peran": peran, "boss": boss, "cepat": cepat,
            "prompt": prompt + " Jawab dalam bahasa Indonesia.",
            "pola": re.compile(r"\b(" + "|".join(kata) + r")\b", re.I) if kata else None}


AGENTS = {
    # Lantai 1: bot cepat untuk urusan ringan (pakai model Lite)
    "herald": _ag("Herald", 1, "Humas & Resepsionis", "Jadwal, pengumuman, humas, caption",
                  "Kamu MCI.Herald, resepsionis dan humas MCI: jadwal, pengumuman, caption media sosial.",
                  ["jadwal", "pengumuman", "caption", "acara", "undangan", "humas", "sosmed", "instagram", "agenda"], cepat=True),
    "sentinel": _ag("Sentinel", 1, "Humas & Resepsionis", "Perangkum poin penting diskusi",
                    "Kamu MCI.Sentinel, perangkum poin penting. Kamu hanya membaca teks yang dikirim langsung kepadamu, bukan grup WhatsApp. Jawab dengan poin ringkas.",
                    ["rangkum", "ringkas", "ringkasan", "notulen", "poin penting", "rekap chat", "rekap diskusi"], cepat=True),
    # Lantai 2: Divisi Teknik
    "devmatrix": _ag("DevMatrix", 2, "Divisi Teknik", "Koding & arsitek logika hardware (C/C++, Python, mikrokontroler)",
                     "Kamu MCI.DevMatrix, asisten koding dan arsitek logika hardware. Jika diminta kode, beri kode lengkap yang siap disalin.",
                     ["kode", "coding", "program", "arduino", "esp32", "python", "error", "compile", "bug", "library", "sketch", "firmware"]),
    "pulse": _ag("Pulse", 2, "Divisi Teknik", "Electrical Engineering (daya, pinout, I2C/SPI/LoRa)",
                 "Kamu MCI.Pulse, asisten Electrical Engineering. Tunjukkan rumus dan satuan, ingatkan batas tegangan atau arus berbahaya.",
                 ["volt", "tegangan", "arus", "ampere", "daya", "watt", "pinout", "i2c", "spi", "lora", "resistor", "baterai", "rangkaian", "sensor"]),
    "aegis": _ag("Aegis", 2, "Divisi Teknik", "Verifikator kritis, risiko, safety checklist",
                 "Kamu MCI.Aegis, verifikator kritis dan penyusun safety checklist. Cari celah, sebut risiko dengan jelas, jangan hanya setuju.",
                 ["risiko", "aman", "keamanan", "safety", "bahaya", "verifikasi", "cek ulang", "checklist", "periksa"]),
    # Lantai 3: Operasional & Keuangan
    "nexus": _ag("Nexus", 3, "Operasional & Keuangan", "Manajemen proyek, inventaris, tugas tim",
                 "Kamu MCI.Nexus, pusat manajemen proyek, inventaris, dan tugas tim. Terstruktur dan bisa langsung dikerjakan.",
                 ["proyek", "tugas", "inventaris", "stok", "deadline", "timeline", "progres", "pembagian kerja"], cepat=True),
    "ledger": _ag("Ledger", 3, "Operasional & Keuangan", "Keuangan: kas masuk/keluar jadi tabel",
                  "Kamu MCI.Ledger, asisten keuangan MCI. Teliti soal angka, tulis rupiah dengan titik ribuan.",
                  ["kas", "uang", "saldo", "transfer", "bayar", "pembayaran", "keuangan", "anggaran", "budget", "iuran", "pengeluaran", "pemasukan"], cepat=True),
    "scribe": _ag("Scribe", 3, "Operasional & Keuangan", "Dokumen, laporan, proposal",
                  "Kamu MCI.Scribe, penulis dokumen, laporan, dan proposal yang rapi dan profesional.",
                  ["laporan", "proposal", "surat", "dokumen", "makalah", "esai", "artikel", "notula"]),
    "atlas": _ag("Atlas", 3, "Operasional & Keuangan", "Riset & olah data",
                 "Kamu MCI.Atlas, peneliti dan pengolah data. Bedakan fakta dari dugaan, sebut keterbatasan datamu.",
                 ["riset", "penelitian", "data", "statistik", "bandingkan", "perbandingan", "analisis", "survei", "referensi"]),
    # Lantai 4: Eksekutif
    "apex": _ag("Apex", 4, "Eksekutif", "Boss koordinator, membagi tugas ke tim",
                "Kamu MCI.Apex, boss AI yang mengoordinasi tim MCI. Ringkas dan tegas.", [], boss=True, cepat=True),
}
assert len(AGENTS) <= 10, "Maksimal 10 bot"
STATE = {k: {"status": "IDLE", "activity": "Siaga", "active": 0, "calls": 0, "tokens": 0, "mode": "meja"} for k in [*AGENTS, "canvas"]}
# Canvas = bot gambar (Cloudflare), tampil di kantor 3D lantai 1 tapi bukan agen Gemini (batas 10 agen tetap)
CANVAS = {"nama": "MCI.Canvas", "lantai": 1, "divisi": "Studio Gambar", "peran": "Pelukis AI: bikin dan ubah gambar", "boss": False}
SET = {"caveman": False, "mcp": False, "mem_tg": "tidak", "laporan_on": True}   # mem_tg: simpan chat Telegram (tidak / 72 / permanen)
TASKLOG = deque(maxlen=40)
JOBS = {}
LOOP = None
KAT = ["Jasa", "Penjualan", "Iuran", "Hibah", "Komponen", "Operasional", "Transport", "Gaji", "Lainnya"]
MULTI = re.compile(r"\b(lalu|kemudian|setelah itu|terus minta|dan minta|sekaligus|juga minta|habis itu)\b", re.I)

SKILL_SEED = [
    ("rekap-kas", "ledger", "Rekap kas per periode jadi tabel ringkas", "rekap kas, laporan kas, rekap keuangan",
     "Pakai data kas yang diberikan. Keluarkan tabel (tanggal | keterangan | masuk | keluar), total masuk, total keluar, saldo akhir, lalu 1 kalimat catatan. Rupiah pakai titik ribuan."),
    ("laporan-mingguan", "scribe", "Laporan mingguan format resmi MCI", "laporan mingguan, weekly report",
     "Struktur: Judul, Ringkasan (3 kalimat), Pencapaian (poin), Kendala (poin), Rencana minggu depan (poin). Bahasa formal, maksimal 250 kata."),
    ("cek-wiring", "aegis", "Checklist keselamatan rangkaian sebelum dinyalakan", "cek wiring, cek rangkaian, sebelum dinyalakan",
     "Periksa: tegangan suplai vs rating komponen, polaritas, ground bersama, arus maksimum per pin, potensi korsleting, level logika 3.3V/5V. Tandai tiap poin AMAN, PERIKSA, atau BAHAYA."),
    ("pinout-esp32", "pulse", "Pemetaan pin ESP32 DevKit untuk modul", "pinout esp32, pin esp32, sambung ke esp32",
     "Beri tabel: modul | pin modul | pin ESP32 | catatan. Hindari GPIO 6-11 (flash). Waspadai strapping pin 0, 2, 5, 12, 15. GPIO 34-39 hanya input. Sebut level tegangan."),
    ("rangkum-diskusi", "sentinel", "Rangkum diskusi grup jadi keputusan dan tugas", "rangkum chat, rangkum diskusi, notulen rapat",
     "Keluarkan: Keputusan (poin), Tugas (siapa, apa, kapan), Pertanyaan terbuka. Abaikan basa-basi. Maksimal 150 kata."),
    ("caption-sosmed", "herald", "Caption media sosial MCI", "caption, postingan instagram, pengumuman sosmed",
     "Buat 2 versi caption (formal dan santai), masing-masing maksimal 60 kata, 3-5 hashtag relevan, dan ajakan bertindak di akhir."),
]


# ---------- DATABASE (Postgres jika DATABASE_URL, selain itu SQLite sementara) ----------
@contextlib.contextmanager
def db():
    c = psycopg2.connect(DATABASE_URL, connect_timeout=10) if PG else sqlite3.connect(DB_PATH)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def sql(c, q, p=()):
    cur = c.cursor()
    cur.execute(q.replace("?", "%s") if PG else q, p)
    return cur


def db_init():
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    big = "BIGINT" if PG else "INTEGER"
    with db() as c:
        sql(c, f"CREATE TABLE IF NOT EXISTS kas (id {pk}, tanggal TEXT NOT NULL, keterangan TEXT NOT NULL, kategori TEXT NOT NULL, masuk {big} NOT NULL DEFAULT 0, keluar {big} NOT NULL DEFAULT 0, sumber TEXT, dibuat TEXT)")
        sql(c, "CREATE TABLE IF NOT EXISTS pengaturan (k TEXT PRIMARY KEY, v TEXT)")
        sql(c, "CREATE TABLE IF NOT EXISTS skills (nama TEXT PRIMARY KEY, bot TEXT NOT NULL, deskripsi TEXT NOT NULL, pemicu TEXT, isi TEXT NOT NULL, dibuat TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS memori (id {pk}, waktu TEXT NOT NULL, sumber TEXT, bot TEXT, jalur TEXT, perintah TEXT NOT NULL, jawaban TEXT, kadaluarsa TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS iot (id {pk}, ts {big} NOT NULL, perangkat TEXT NOT NULL, data TEXT NOT NULL)")
        sql(c, f"CREATE TABLE IF NOT EXISTS gambar (id {pk}, prompt TEXT NOT NULL, prompt_en TEXT, sumber TEXT, mime TEXT NOT NULL, data TEXT NOT NULL, dibuat TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS aktivitas (id {pk}, waktu TEXT NOT NULL, bot TEXT, tugas TEXT, status TEXT, jalur TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS anggota (id {pk}, nama TEXT NOT NULL, token TEXT NOT NULL, divisi TEXT, jabatan TEXT, aktif INTEGER NOT NULL DEFAULT 1, dibuat TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS catatan (id {pk}, pemilik INTEGER NOT NULL, judul TEXT, isi TEXT, bersama INTEGER NOT NULL DEFAULT 0, sematkan INTEGER NOT NULL DEFAULT 0, dibuat TEXT, diubah TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS blacklist (id {pk}, nama TEXT NOT NULL, alasan TEXT, keterangan TEXT, oleh TEXT, tanggal TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS polling (id {pk}, judul TEXT NOT NULL, deskripsi TEXT, opsi TEXT NOT NULL, pembuat INTEGER, pembuat_nama TEXT, divisi TEXT, status TEXT NOT NULL DEFAULT 'buka', batas TEXT, dibuat TEXT, ditutup TEXT)")
        sql(c, "CREATE TABLE IF NOT EXISTS suara (poll INTEGER NOT NULL, pemilih INTEGER NOT NULL, pilihan INTEGER NOT NULL, waktu TEXT, PRIMARY KEY (poll, pemilih))")
        sql(c, f"CREATE TABLE IF NOT EXISTS dokumentasi (id {pk}, judul TEXT NOT NULL, tanggal TEXT, keterangan TEXT, mime TEXT, data TEXT, oleh TEXT, dibuat TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS hutang (id {pk}, nama TEXT NOT NULL, jumlah {big} NOT NULL, tanggal TEXT NOT NULL, jatuh_tempo TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'belum', lunas TEXT, konfirmasi INTEGER NOT NULL DEFAULT 0, catatan TEXT, dibuat TEXT)")
        sql(c, "CREATE TABLE IF NOT EXISTS dikeluarkan (nama TEXT PRIMARY KEY, tanggal TEXT NOT NULL, alasan TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS perpus (id {pk}, judul TEXT NOT NULL, penulis TEXT, kategori TEXT, ringkasan TEXT, isi TEXT, link TEXT, "
               "file_nama TEXT, file_mime TEXT, file_data TEXT, sampul_mime TEXT, sampul TEXT, dibuat TEXT, diubah TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS stok (id {pk}, nama TEXT NOT NULL, jumlah {big} NOT NULL DEFAULT 0, satuan TEXT, kategori TEXT, minimum {big} NOT NULL DEFAULT 0, catatan TEXT, mime TEXT, gambar TEXT, diubah TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS laporan (id {pk}, minggu TEXT, dibuat TEXT, dari TEXT, sampai TEXT, sumber TEXT, isi TEXT, data TEXT)")
        sql(c, f"CREATE TABLE IF NOT EXISTS pemakaian (tanggal TEXT NOT NULL, model TEXT NOT NULL, masuk {big} NOT NULL DEFAULT 0, keluar {big} NOT NULL DEFAULT 0, pikir {big} NOT NULL DEFAULT 0, panggilan {big} NOT NULL DEFAULT 0, PRIMARY KEY (tanggal, model))")
        r = sql(c, "SELECT v FROM pengaturan WHERE k=?", ("s",)).fetchone()
        if r:
            SET.update(json.loads(r[0]))
        if not sql(c, "SELECT COUNT(*) FROM skills").fetchone()[0]:
            now = datetime.now(WIB).isoformat(timespec="seconds")
            for s in SKILL_SEED:
                sql(c, "INSERT INTO skills (nama,bot,deskripsi,pemicu,isi,dibuat) VALUES (?,?,?,?,?,?)", (*s, now))
    for kol in ("oleh TEXT", "oleh_role TEXT", "ip TEXT"):   # kolom pengunggah (Perpustakaan terbuka untuk semua)
        try:
            with db() as c:
                sql(c, f"ALTER TABLE perpus ADD COLUMN {kol}")
        except Exception:
            pass   # sudah ada
    skills_reload()
    if not PG:
        log.warning("DATABASE_URL kosong: memakai SQLite sementara, data kas, skill, dan pemakaian HILANG saat restart Render.")


def set_save():
    with db() as c:
        sql(c, "INSERT INTO pengaturan (k,v) VALUES (?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", ("s", json.dumps(SET)))


def kas_add(rows, sumber):
    ids, now = [], datetime.now(WIB).isoformat(timespec="seconds")
    q = "INSERT INTO kas (tanggal,keterangan,kategori,masuk,keluar,sumber,dibuat) VALUES (?,?,?,?,?,?,?)"
    with db() as c:
        for r in rows:
            p = (r["tanggal"], r["keterangan"], r["kategori"], r["masuk"], r["keluar"], sumber, now)
            ids.append(sql(c, q + " RETURNING id", p).fetchone()[0] if PG else sql(c, q, p).lastrowid)
    return ids


def kas_del(i):
    with db() as c:
        return sql(c, "DELETE FROM kas WHERE id=?", (i,)).rowcount


def kas_all():
    with db() as c:
        rows = sql(c, "SELECT id,tanggal,keterangan,kategori,masuk,keluar FROM kas ORDER BY tanggal, id").fetchall()
    saldo, out = 0, []
    for i, t, k, g, m, x in rows:
        saldo += m - x
        out.append({"id": i, "tanggal": t, "keterangan": k, "kategori": g, "masuk": m, "keluar": x, "saldo": saldo})
    return out


def kas_summary(rows):
    m = sum(r["masuk"] for r in rows)
    x = sum(r["keluar"] for r in rows)
    kat = {}
    for r in rows:
        d = kat.setdefault(r["kategori"], {"masuk": 0, "keluar": 0})
        d["masuk"] += r["masuk"]
        d["keluar"] += r["keluar"]
    return {"masuk": m, "keluar": x, "saldo": m - x, "kategori": kat}


def rp(n):
    return "Rp " + f"{n:,}".replace(",", ".")


def kas_context():
    rows = kas_all()
    s = kas_summary(rows)
    baris = "\n".join(f"{r['tanggal']} | {r['keterangan']} | {r['kategori']} | +{r['masuk']} | -{r['keluar']}" for r in rows[-12:])
    return (f"[Data kas MCI] Total masuk {rp(s['masuk'])}, keluar {rp(s['keluar'])}, saldo {rp(s['saldo'])}. "
            f"12 catatan terakhir:\n{baris or '(kosong)'}\n\nTugas: ")


# ---------- SKILLS ----------
SKILLS = {}
NAMA_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


def skills_reload():
    with db() as c:
        rows = sql(c, "SELECT nama,bot,deskripsi,pemicu,isi FROM skills ORDER BY nama").fetchall()
    SKILLS.clear()
    for n, b, d, p, i in rows:
        pem = [w.strip().lower() for w in (p or "").split(",") if w.strip()]
        SKILLS[n] = {"nama": n, "bot": b, "deskripsi": d, "pemicu": ", ".join(pem), "isi": i,
                     "pola": re.compile(r"\b(" + "|".join(re.escape(w) for w in pem) + r")\b", re.I) if pem else None}


def skill_save(d):
    nama = str(d.get("nama", "")).strip().lower()
    bot = str(d.get("bot", "")).strip().lower()
    if not NAMA_OK.match(nama):
        raise ValueError("Nama skill: huruf kecil, angka, tanda minus, 2-40 karakter")
    if bot not in AGENTS:
        raise ValueError("Bot tidak dikenal")
    desk = str(d.get("deskripsi", "")).strip()[:200]
    isi = str(d.get("isi", "")).strip()[:2000]
    pem = str(d.get("pemicu", "")).strip()[:300]
    if not desk or not isi:
        raise ValueError("Deskripsi dan isi wajib diisi")
    with db() as c:
        sql(c, "INSERT INTO skills (nama,bot,deskripsi,pemicu,isi,dibuat) VALUES (?,?,?,?,?,?) ON CONFLICT (nama) DO UPDATE SET bot=excluded.bot, deskripsi=excluded.deskripsi, pemicu=excluded.pemicu, isi=excluded.isi",
            (nama, bot, desk, pem, isi, datetime.now(WIB).isoformat(timespec="seconds")))
    skills_reload()
    return nama


def skill_del(nama):
    with db() as c:
        n = sql(c, "DELETE FROM skills WHERE nama=?", (nama,)).rowcount
    skills_reload()
    return n


def skill_match(text):
    for s in SKILLS.values():
        if s["pola"] and s["pola"].search(text):
            return s
    return None


def skills_public():
    return [{k: v for k, v in s.items() if k != "pola"} for s in SKILLS.values()]


# ---------- PEMAKAIAN TOKEN, CACHE, PEMBATAS ----------
USAGE = {}
CACHE = OrderedDict()
CACHE_TTL = 900
STATS = {"cache_hit": 0, "router_hemat": 0, "boss_dipanggil": 0}
NO_THINK = set()


def today():
    return datetime.now(WIB).strftime("%Y-%m-%d")


def usage_load():
    with db() as c:
        rows = sql(c, "SELECT model,masuk,keluar,pikir,panggilan FROM pemakaian WHERE tanggal=?", (today(),)).fetchall()
    USAGE[today()] = {m: {"masuk": a, "keluar": b, "pikir": p, "panggilan": n} for m, a, b, p, n in rows}


def usage_add(model, masuk, keluar, pikir):
    d = USAGE.setdefault(today(), {}).setdefault(model, {"masuk": 0, "keluar": 0, "pikir": 0, "panggilan": 0})
    d["masuk"] += masuk
    d["keluar"] += keluar
    d["pikir"] += pikir
    d["panggilan"] += 1
    try:
        with db() as c:
            sql(c, "INSERT INTO pemakaian (tanggal,model,masuk,keluar,pikir,panggilan) VALUES (?,?,?,?,?,1) ON CONFLICT (tanggal, model) DO UPDATE SET masuk=pemakaian.masuk+excluded.masuk, keluar=pemakaian.keluar+excluded.keluar, pikir=pemakaian.pikir+excluded.pikir, panggilan=pemakaian.panggilan+1",
                (today(), model, masuk, keluar, pikir))
    except Exception:
        log.exception("Gagal menyimpan pemakaian token")


def usage_today_total():
    return sum(v["masuk"] + v["keluar"] + v["pikir"] for v in USAGE.get(today(), {}).values())


def usage_report():
    per = USAGE.get(today(), {})
    return {"tanggal": today(), "total": usage_today_total(), "batas": DAILY_LIMIT, "rpm": RPM,
            "model": per, "model_utama": MODEL, "model_lite": MODEL_LITE, **STATS}


class Limiter:
    def __init__(self):
        self.q = {}
        self.lock = asyncio.Lock()

    async def acquire(self, model):
        while True:
            async with self.lock:
                dq = self.q.setdefault(model, deque())
                now = time.monotonic()
                while dq and now - dq[0] > 60:
                    dq.popleft()
                if len(dq) < RPM:
                    dq.append(now)
                    return
                wait = 60 - (now - dq[0]) + .05
            await asyncio.sleep(min(wait, 5))


LIMITER = None


def cache_get(k):
    v = CACHE.get(k)
    if v and time.time() - v[0] < CACHE_TTL:
        CACHE.move_to_end(k)
        STATS["cache_hit"] += 1
        return v[1]
    CACHE.pop(k, None)
    return None


def cache_put(k, val):
    CACHE[k] = (time.time(), val)
    while len(CACHE) > 200:
        CACHE.popitem(last=False)


def think_cfg(model):
    if model in NO_THINK or not hasattr(types, "ThinkingConfig"):
        return None
    lvl = "minimal" if "lite" in model else "low"
    for v in (lvl.upper(), lvl):
        try:
            return types.ThinkingConfig(thinking_level=v)
        except Exception:
            continue
    NO_THINK.add(model)
    return None


# ---------- PEMANGGIL GEMINI ----------
_cl = {}


def gkey(key):
    return os.getenv(f"GEMINI_{key.upper()}") or os.getenv("GEMINI_API_KEY")


def client(key):
    g = gkey(key)
    if not g:
        return None
    if g not in _cl:
        _cl[g] = genai.Client(api_key=g)
    return _cl[g]


def model_for(key):
    a = AGENTS[key]
    return MODEL_LITE if (a["cepat"] or a["boss"]) else MODEL


MIN_SHOW = 8  # detik minimal bot tampil "bekerja" di kantor 3D, supaya animasi lari selalu terlihat


def _begin(k, act, room=None):
    s = STATE[k]
    if not s["active"]:
        s["since"] = time.time()
    s["active"] += 1
    s["status"], s["activity"] = "WORKING", act
    if room and not s.get("dw"):   # sidang dewan selalu menang
        s["mode"] = room


def _end(k):
    s = STATE[k]
    s["active"] = max(0, s["active"] - 1)
    if not s["active"]:
        s["hold"], s["last_act"], s["hold_mode"] = s.get("since", 0) + MIN_SHOW, s["activity"], s["mode"]
        s["status"], s["activity"] = "IDLE", "Siaga"
        if s["mode"] in ROOMS:
            s["mode"] = "meja"


def office_status(s):
    """Status untuk web: tugas yang selesai sangat cepat tetap tampil WORKING minimal MIN_SHOW detik."""
    if s["active"]:
        return "WORKING", s["activity"], s["mode"]
    if time.time() < s.get("hold", 0):
        return "WORKING", s.get("last_act") or "Bekerja", s.get("hold_mode", s["mode"])
    return "IDLE", "Siaga", s["mode"]


@contextlib.asynccontextmanager
async def mcp_tools():
    if not (SET["mcp"] and MCP_SERVERS and ClientSession):
        yield None
        return
    async with contextlib.AsyncExitStack() as st:
        tools = []
        for s in MCP_SERVERS:
            try:
                r, w, _ = await st.enter_async_context(streamablehttp_client(s["url"], headers=s.get("headers")))
                ss = await st.enter_async_context(ClientSession(r, w))
                await ss.initialize()
                tools.append(ss)
            except Exception:
                log.exception("MCP %s gagal tersambung", s.get("name"))
        yield tools or None


async def ask(key, text, history=None, parts=None, schema=None, model=None, act="Memproses permintaan",
              use_mcp=True, skill=None, brief=False, cache=True, show=None, room=None):
    cl = client(key)
    if not cl:
        raise RuntimeError(f"Key Gemini untuk {AGENTS[key]['nama']} belum diisi (GEMINI_{key.upper()} atau GEMINI_API_KEY).")
    if DAILY_LIMIT and usage_today_total() >= DAILY_LIMIT:
        raise RuntimeError(f"Batas token harian ({DAILY_LIMIT:,}) sudah tercapai. Coba lagi besok atau naikkan DAILY_TOKEN_LIMIT.".replace(",", "."))
    model = model or model_for(key)
    sysp = AGENTS[key]["prompt"]
    if skill and skill in SKILLS:
        sysp += f"\n\n[SKILL: {skill}]\n{SKILLS[skill]['isi']}"
    if SET["caveman"] or brief:
        sysp += CAVEMAN
    contents = (history or []) + [types.Content(role="user", parts=[types.Part(text=text)] + (parts or []))]
    ck = None
    if cache and not history and not parts:
        ck = hashlib.sha256(f"{model}|{sysp}|{text}|{getattr(schema, '__name__', schema)}".encode()).hexdigest()
        hit = cache_get(ck)
        if hit is not None:
            return hit
    extra = {"response_mime_type": "application/json", "response_schema": schema} if schema else {}

    async def call(tools, think=True):
        kw = dict(system_instruction=sysp, tools=tools, **extra)
        tc = think_cfg(model) if think else None
        if tc is not None:
            kw["thinking_config"] = tc
        await LIMITER.acquire(model)
        return await cl.aio.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**kw))

    async def call_safe(tools):
        try:
            return await call(tools)
        except Exception as e:
            msg = str(e)
            if "thinking" in msg.lower() and model not in NO_THINK:
                NO_THINK.add(model)
                log.warning("Model %s menolak pengaturan thinking, dimatikan untuk model ini", model)
                return await call(tools, think=False)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                log.warning("Kena batas Gemini (429), tunggu 12 detik lalu ulangi sekali")
                await asyncio.sleep(12)
                return await call(tools)
            raise

    _begin(show or key, act, room)  # show = bot yang tampil bekerja di kantor 3D
    try:
        async with (mcp_tools() if use_mcp and not schema else contextlib.nullcontext(None)) as tools:
            try:
                r = await call_safe(tools)
            except Exception:
                if not tools:
                    raise
                log.exception("Panggilan dengan MCP gagal, ulangi tanpa MCP")
                r = await call_safe(None)
        u = r.usage_metadata
        masuk = getattr(u, "prompt_token_count", 0) or 0
        keluar = getattr(u, "candidates_token_count", 0) or 0
        pikir = getattr(u, "thoughts_token_count", 0) or 0
        total = getattr(u, "total_token_count", 0) or (masuk + keluar + pikir)
        if total and not (masuk or keluar or pikir):
            masuk = total  # hanya total yang dilaporkan: tetap dihitung untuk batas harian
        s = STATE[key]
        s["calls"] += 1
        s["tokens"] += total
        await asyncio.to_thread(usage_add, model, masuk, keluar, pikir)
        out = (r.text or "").strip()
        if ck and out:
            cache_put(ck, out)
        return out
    finally:
        _end(show or key)


# ---------- ROUTER LOKAL (tanpa token) ----------
def score_bots(text):
    sk = {}
    for k, a in AGENTS.items():
        if a["pola"]:
            n = len(a["pola"].findall(text))
            if n:
                sk[k] = n
    return sk


def route_local(text):
    """Kembalikan key bot jika tujuan jelas, None jika perlu Boss."""
    t = text.lower()
    if MULTI.search(t) or len(t) > 700:
        return None
    named = [k for k in AGENTS if k != "apex" and re.search(rf"(@|\b){k}\b", t)]
    if len(named) == 1:
        return named[0]
    if named:
        return None
    sk = score_bots(t)
    if len(sk) == 1:
        return next(iter(sk))
    if len(sk) > 1:
        best = sorted(sk.items(), key=lambda x: -x[1])
        if best[0][1] > best[1][1]:
            return best[0][0]
    return None


# ---------- BOSS ----------
class Tugas(BaseModel):
    bot: str
    perintah: str
    skill: str


class Rencana(BaseModel):
    tugas: list[Tugas]
    langsung: str


def _log(bot, tugas, status, jalur):
    e = {"t": datetime.now(WIB).strftime("%H:%M"), "bot": bot, "tugas": tugas[:90], "status": status, "jalur": jalur}
    TASKLOG.appendleft(e)
    if status in FINAL:
        akt_save(e)
    return e


async def run_task(bot, perintah, skill=None, jalur="boss", sem=None):
    e = _log(bot, perintah, "Bekerja", jalur)
    async with (sem or contextlib.nullcontext()):
        try:
            need_kas = bot == "ledger" and re.search(r"\b(kas|saldo|uang|rekap|keuangan|pemasukan|pengeluaran)\b", perintah, re.I)
            ctx = await asyncio.to_thread(kas_context) if need_kas else ""
            room, rctx, note, ract = await room_prep(perintah, bot)
            h = await ask(bot, ctx + rctx + perintah, skill=skill, act=("Skill " + skill) if skill else (ract or "Mengerjakan tugas"), room=room)
            if note and h:
                h += f"\n\n— {note}"
            e["status"] = "Selesai"
        except Exception as ex:
            log.exception("Tugas %s gagal", bot)
            e["status"] = "Gagal"
            h = f"(gagal: {ex})" if isinstance(ex, RuntimeError) else "(gagal, cek log server)"
        await asyncio.to_thread(akt_save, e)
    return {"bot": bot, "perintah": perintah, "skill": skill or "", "hasil": h}


async def smart_run(perintah):
    """Jalur termurah dulu: skill, router lokal, baru Boss."""
    if MULTI.search(perintah):
        return await boss_run(perintah)
    sk = skill_match(perintah)
    if sk:
        STATS["router_hemat"] += 1
        r = await run_task(sk["bot"], perintah, sk["nama"], "skill")
        return {"jalur": "skill", "keterangan": f"Skill {sk['nama']} dijalankan {AGENTS[sk['bot']]['nama']} tanpa Boss", "rencana": [r], "jawaban": r["hasil"]}
    lok = route_local(perintah)
    if lok:
        STATS["router_hemat"] += 1
        r = await run_task(lok, perintah, None, "langsung")
        return {"jalur": "langsung", "keterangan": f"Langsung ke {AGENTS[lok]['nama']} tanpa Boss", "rencana": [r], "jawaban": r["hasil"]}
    return await boss_run(perintah)


async def boss_run(perintah):
    STATS["boss_dipanggil"] += 1
    daftar = "\n".join(f"- {k}: {a['peran']}" for k, a in AGENTS.items() if k != "apex")
    sdaftar = "\n".join(f"- {s['nama']} ({s['bot']}): {s['deskripsi']}" for s in SKILLS.values()) or "(tidak ada)"
    raw = await ask("apex", f"Perintah: {perintah}\n\nBot:\n{daftar}\n\nSkill:\n{sdaftar}\n\n"
                    f"Pecah jadi 1-{MAX_TASKS} tugas, tiap tugas ke SATU bot paling cocok dengan perintah jelas dan mandiri. "
                    "Isi skill dengan nama skill yang cocok atau kosong. Jika cukup dijawab singkat tanpa bot, kosongkan tugas dan isi langsung.",
                    schema=Rencana, act="Membagi tugas", use_mcp=False)
    try:
        plan = json.loads(raw)
    except ValueError:
        plan = {"tugas": [], "langsung": raw}
    tugas, seen = [], set()
    for t in (plan.get("tugas") or [])[:MAX_TASKS]:
        b = str(t.get("bot", "")).lower()
        if b in AGENTS and b != "apex" and b not in seen:
            seen.add(b)
            sk = str(t.get("skill") or "").strip().lower()
            tugas.append((b, str(t.get("perintah") or perintah)[:1500], sk if sk in SKILLS else None))
    if not tugas:
        return {"jalur": "boss", "keterangan": "Dijawab langsung oleh Boss", "rencana": [], "jawaban": plan.get("langsung") or "Tidak ada tugas yang bisa dibagi."}
    sem = asyncio.Semaphore(3)
    res = await asyncio.gather(*(run_task(b, p, s, "boss", sem) for b, p, s in tugas))
    ket = "Boss membagi tugas ke " + ", ".join(AGENTS[r["bot"]]["nama"] for r in res)
    if len(res) == 1:
        return {"jalur": "boss", "keterangan": ket, "rencana": res, "jawaban": res[0]["hasil"]}
    isi = "\n\n".join(f"[{AGENTS[r['bot']]['nama']}]\n{r['hasil']}" for r in res)
    if len(isi) < 1500:  # hasil pendek: gabung tanpa AI, hemat token
        return {"jalur": "boss", "keterangan": ket, "rencana": res, "jawaban": isi}
    jawab = await ask("apex", f"Perintah awal: {perintah}\n\nHasil bot:\n{isi}\n\nGabungkan jadi satu jawaban akhir yang rapi dan ringkas. Jangan ulang detail yang sama.",
                      act="Menyusun jawaban akhir", use_mcp=False)
    return {"jalur": "boss", "keterangan": ket, "rencana": res, "jawaban": jawab}


# ---------- SIDANG DEWAN (COUNCIL) ----------
def council_members(pertanyaan, anggota=None):
    if anggota:
        m = [k for k in anggota if k in AGENTS and k != "apex"]
    else:
        sk = score_bots(pertanyaan.lower())
        m = [k for k, _ in sorted(sk.items(), key=lambda x: -x[1])]
        for k in ("aegis", "atlas", "nexus"):
            if len(m) >= 2:
                break
            if k not in m:
                m.append(k)
        if "aegis" not in m:
            m.append("aegis")  # selalu ada pengkritik
    return m[:COUNCIL_MAX]


async def council_run(pertanyaan, anggota=None):
    m = council_members(pertanyaan, anggota)
    for k in m + ["apex"]:
        STATE[k]["dw"] = STATE[k].get("dw", 0) + 1  # hitung sidang yang sedang berjalan
        STATE[k]["mode"] = "dewan"
    for k in m + ["apex"]:
        _begin(k, "Menuju sidang dewan")  # semua langsung lari ke ruang dewan, tetap di sana sampai putusan
    e = _log("dewan", pertanyaan, "Sidang", "dewan")
    try:
        async def one(k):
            try:
                return await ask(k, f"[SIDANG DEWAN MCI] Pertanyaan: {pertanyaan}\nBeri pendapat dari sudut keahlianmu, maksimal 80 kata. Sebut 1 risiko atau keberatan jika ada.",
                                 brief=True, act="Sidang dewan", cache=False, use_mcp=False)
            except Exception as ex:
                return f"(tidak hadir: {ex})" if isinstance(ex, RuntimeError) else "(tidak hadir)"
        ops = await asyncio.gather(*(one(k) for k in m))
        pend = [{"bot": k, "nama": AGENTS[k]["nama"], "isi": o} for k, o in zip(m, ops)]
        ring = "\n\n".join(f"[{p['nama']}]\n{p['isi']}" for p in pend)
        putusan = await ask("apex", f"Pertanyaan: {pertanyaan}\n\nPendapat dewan:\n{ring}\n\n"
                            "Tulis PUTUSAN DEWAN: 1) keputusan, 2) poin sepakat, 3) poin beda pendapat, 4) langkah berikut. Maksimal 150 kata.",
                            act="Membacakan putusan", use_mcp=False, cache=False)
        e["status"] = "Selesai"
        await asyncio.to_thread(akt_save, e)
        return {"jalur": "dewan", "keterangan": "Sidang dewan: " + ", ".join(p["nama"] for p in pend), "pendapat": pend, "jawaban": putusan, "rencana": []}
    except Exception:
        e["status"] = "Gagal"
        await asyncio.to_thread(akt_save, e)
        raise
    finally:
        for k in m + ["apex"]:
            _end(k)
            STATE[k]["dw"] = max(0, STATE[k].get("dw", 0) - 1)
            STATE[k]["mode"] = "dewan" if STATE[k]["dw"] else "meja"


# ---------- LEDGER (olah teks/foto jadi tabel kas) ----------
class Entri(BaseModel):
    tanggal: str
    keterangan: str
    kategori: str
    masuk: int
    keluar: int


def _tgl(s, default):
    try:
        datetime.strptime(str(s), "%Y-%m-%d")
        return str(s)
    except ValueError:
        return default


async def kas_parse(teks, foto=None, mime="image/jpeg"):
    hari = datetime.now(WIB).strftime("%Y-%m-%d")
    p = (f"Hari ini {hari}. Ekstrak SEMUA transaksi kas dari teks/foto berikut, satu objek per transaksi. "
         "tanggal format YYYY-MM-DD (jika tak disebut pakai hari ini). Isi masuk ATAU keluar (rupiah bulat, '500rb'=500000, "
         f"'1,5 juta'=1500000), yang lain 0. kategori salah satu dari: {', '.join(KAT)}. Jika tidak ada transaksi, kembalikan [].")
    parts = [types.Part.from_bytes(data=foto, mime_type=mime)] if foto else None
    raw = await ask("ledger", p + "\n\n" + (teks or "(lihat foto)"), parts=parts, schema=list[Entri], act="Mengolah kas", use_mcp=False)
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for r in data if isinstance(data, list) else []:
        try:
            m, x = max(0, int(r.get("masuk", 0))), max(0, int(r.get("keluar", 0)))
        except (TypeError, ValueError, AttributeError):
            continue
        if (m == 0 and x == 0) or m > 10**12 or x > 10**12:
            continue
        kat = r.get("kategori")
        out.append({"tanggal": _tgl(r.get("tanggal"), hari), "keterangan": str(r.get("keterangan", ""))[:200] or "-",
                    "kategori": kat if kat in KAT else "Lainnya", "masuk": m, "keluar": x})
    return out[:50]


# ---------- STUDIO GAMBAR (Cloudflare Workers AI) ----------
GAL_IDS = []   # 4 gambar terbaru, dipajang di Galeri Karya LT 1 (kantor 3D, admin)


def gal_load():
    try:
        with db() as c:
            GAL_IDS[:] = [r[0] for r in sql(c, "SELECT id FROM gambar ORDER BY id DESC LIMIT 4").fetchall()]
    except Exception:
        log.exception("Gagal memuat galeri")


def img_save(prompt, en, sumber, mime, data):
    now = datetime.now(WIB).isoformat(timespec="seconds")
    q = "INSERT INTO gambar (prompt,prompt_en,sumber,mime,data,dibuat) VALUES (?,?,?,?,?,?)"
    p = (prompt[:1000], en[:1500], sumber, mime, base64.b64encode(data).decode(), now)
    with db() as c:
        gid = sql(c, q + " RETURNING id", p).fetchone()[0] if PG else sql(c, q, p).lastrowid
        # galeri dibatasi supaya database gratis (1 GB) tidak penuh
        sql(c, f"DELETE FROM gambar WHERE id NOT IN (SELECT id FROM gambar ORDER BY id DESC LIMIT {IMG_KEEP})")
    GAL_IDS[:] = ([gid] + GAL_IDS)[:4]
    return gid


def img_list():
    with db() as c:
        rows = sql(c, "SELECT id,prompt,prompt_en,sumber,mime,dibuat FROM gambar ORDER BY id DESC").fetchall()
    return [{"id": i, "prompt": p, "prompt_en": e, "sumber": s, "mime": m, "dibuat": d} for i, p, e, s, m, d in rows]


def img_get(i):
    with db() as c:
        r = sql(c, "SELECT mime,data FROM gambar WHERE id=?", (i,)).fetchone()
    return (r[0], base64.b64decode(r[1])) if r else None


def img_del(i):
    with db() as c:
        n = sql(c, "DELETE FROM gambar WHERE id=?", (i,)).rowcount
    gal_load()
    return n


def img_today():
    u = USAGE.get(today(), {})
    return sum(u.get(m, {}).get("panggilan", 0) for m in {CF_MODEL, CF_EDIT_MODEL})


def img_status():
    return {"aktif": bool(CF_ACCOUNT and CF_TOKEN), "hari_ini": img_today(), "batas": IMG_DAILY, "model": CF_MODEL, "model_edit": CF_EDIT_MODEL}


async def cf_run(model, payload):
    if not (CF_ACCOUNT and CF_TOKEN):
        raise RuntimeError("Studio Gambar belum aktif: isi CF_ACCOUNT_ID dan CF_API_TOKEN di Render.")
    url = f"{CF_BASE}/accounts/{CF_ACCOUNT}/ai/run/{model}"
    payload = dict(payload)
    for _ in range(4):
        try:
            async with httpx.AsyncClient(timeout=120) as h:
                r = await h.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"}, json=payload)
        except httpx.HTTPError:
            log.exception("Cloudflare tidak bisa dihubungi")
            raise RuntimeError("Server gambar Cloudflare tidak bisa dihubungi, coba lagi sebentar.")
        # model menolak parameter tertentu: buang parameter itu lalu ulangi (skema model bisa berubah)
        bad = re.search(r"properties '/(\w+)'", r.text) if r.status_code == 400 else None
        if not (bad and bad.group(1) in payload and bad.group(1) != "prompt"):
            break
        log.warning("Cloudflare menolak parameter %s, diulang tanpa parameter itu", bad.group(1))
        payload.pop(bad.group(1))
    ct = r.headers.get("content-type", "")
    if r.status_code != 200:
        try:
            msg = "; ".join(str(e.get("message", "")) for e in r.json().get("errors", []))
        except ValueError:
            msg = r.text[:200]
        log.warning("Cloudflare AI %s gagal: HTTP %s %s", model, r.status_code, msg)
        low = msg.lower()
        if r.status_code == 429 or "neuron" in low or "quota" in low or "limit" in low:
            raise RuntimeError("Jatah gambar gratis Cloudflare hari ini habis. Reset tiap jam 07.00 WIB.")
        if r.status_code in (401, 403):
            raise RuntimeError("Token Cloudflare ditolak. Cek CF_ACCOUNT_ID, CF_API_TOKEN, dan izin Workers AI.")
        if "nsfw" in low or "safety" in low or "flagged" in low:
            raise RuntimeError("Prompt ditolak filter keamanan Cloudflare. Coba kata-kata lain.")
        raise RuntimeError("Cloudflare gagal membuat gambar, coba lagi.")
    if ct.startswith("image/"):
        return r.content, ct.split(";")[0]
    try:
        img = (r.json().get("result") or {}).get("image")
    except ValueError:
        img = None
    if not img:
        raise RuntimeError("Cloudflare tidak mengirim gambar. Coba prompt lain.")
    return base64.b64decode(img), "image/jpeg"


_EN = re.compile(r"\b(the|a|an|of|with|in|on|and|is|are|style|wearing|holding|photo|portrait)\b", re.I)
_ID = re.compile(r"\b(di|ke|dari|yang|dan|dengan|gaya|warna|lagi|sedang|untuk|pakai|sebuah|seekor|buat|gambar|jadikan|minum|makan)\b", re.I)


def _english(p):
    """Prompt dianggap sudah bahasa Inggris kalau ASCII, ada kata Inggris umum, dan tanpa kata Indonesia umum."""
    return bool(re.fullmatch(r"[\x00-\x7F]*", p)) and bool(_EN.search(p)) and not _ID.search(p)


async def img_prompt(p):
    """FLUX paling paham bahasa Inggris: prompt Indonesia diterjemahkan dulu pakai model Lite (murah)."""
    if _english(p):
        return p
    try:
        out = await ask("herald", "Ubah deskripsi gambar berikut jadi prompt bahasa Inggris untuk AI pembuat gambar. "
                        "Satu paragraf, maksimal 60 kata, jelaskan objek, gaya, warna, pencahayaan, dan komposisi. "
                        "Tulis promptnya saja tanpa pembuka.\n\nDeskripsi: " + p,
                        model=MODEL_LITE, act="Menyiapkan prompt gambar", use_mcp=False, show="canvas")
        out = out.strip().strip('"').strip()
        return out[:1500] or p
    except Exception:
        log.warning("Terjemah prompt gambar gagal, pakai prompt asli")
        return p


def _dim(v):
    try:
        return max(256, min(1024, int(int(v) // 64 * 64)))
    except (TypeError, ValueError):
        return None


async def make_image(prompt, foto=None, kekuatan=0.6, w=None, h=None, sumber="web"):
    prompt = (prompt or "").strip()[:1000]
    if not prompt:
        raise RuntimeError("Tulis dulu gambar yang mau dibuat.")
    if IMG_DAILY and img_today() >= IMG_DAILY:
        raise RuntimeError(f"Batas {IMG_DAILY} gambar per hari sudah tercapai. Coba lagi besok.")
    if not (CF_ACCOUNT and CF_TOKEN):
        raise RuntimeError("Studio Gambar belum aktif: isi CF_ACCOUNT_ID dan CF_API_TOKEN di Render.")
    en = await img_prompt(prompt)
    _begin("canvas", "Mengedit foto" if foto else "Menggambar")
    try:
        if foto:
            try:
                k = max(0.1, min(1.0, float(kekuatan)))
            except (TypeError, ValueError):
                k = 0.6
            body = {"prompt": en, "image_b64": base64.b64encode(foto).decode(), "strength": k, "num_steps": 20,
                    "negative_prompt": "blurry, low quality, distorted, watermark"}
            if _dim(w) and _dim(h):
                body["width"], body["height"] = _dim(w), _dim(h)
            model = CF_EDIT_MODEL
        else:
            body = {"prompt": en, "steps": 4}
            model = CF_MODEL
        data, mime = await cf_run(model, body)
    finally:
        _end("canvas")
    await asyncio.to_thread(usage_add, model, 0, 0, 0)
    gid = await asyncio.to_thread(img_save, prompt, en, sumber, mime, data)
    _log("canvas", ("Edit foto: " if foto else "Gambar: ") + prompt, "Selesai", "gambar")
    return {"id": gid, "mime": mime, "data": data, "prompt": prompt, "prompt_en": en}


# ---------- MEMORI (riwayat chat yang dipilih untuk disimpan) ----------
SIMPAN = ("tidak", "72", "permanen")


def _now():
    return datetime.now(WIB).isoformat(timespec="seconds")


def mem_clean():
    with db() as c:
        sql(c, "DELETE FROM memori WHERE kadaluarsa IS NOT NULL AND kadaluarsa < ?", (_now(),))


def mem_save(sumber, bot, jalur, perintah, jawaban, simpan):
    if simpan not in ("72", "permanen") or not perintah:
        return None
    exp = None if simpan == "permanen" else (datetime.now(WIB) + timedelta(hours=72)).isoformat(timespec="seconds")
    q = "INSERT INTO memori (waktu,sumber,bot,jalur,perintah,jawaban,kadaluarsa) VALUES (?,?,?,?,?,?,?)"
    p = (_now(), sumber, bot[:200], jalur, perintah[:4000], (jawaban or "")[:20000], exp)
    try:
        with db() as c:
            return sql(c, q + " RETURNING id", p).fetchone()[0] if PG else sql(c, q, p).lastrowid
    except Exception:
        log.exception("Gagal menyimpan memori")
        return None


def mem_list(q="", sumber=""):
    mem_clean()
    where, args = [], []
    if sumber in ("web", "telegram"):
        where.append("sumber=?"); args.append(sumber)
    if q:
        where.append("(LOWER(perintah) LIKE ? OR LOWER(jawaban) LIKE ? OR LOWER(bot) LIKE ?)"); args += ["%" + q.lower()[:100] + "%"] * 3
    sqlq = "SELECT id,waktu,sumber,bot,jalur,perintah,jawaban,kadaluarsa FROM memori" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT 300"
    with db() as c:
        rows = sql(c, sqlq, tuple(args)).fetchall()
    return [dict(zip(("id", "waktu", "sumber", "bot", "jalur", "perintah", "jawaban", "kadaluarsa"), r)) for r in rows]


def mem_keep(i):
    with db() as c:
        return sql(c, "UPDATE memori SET kadaluarsa=NULL WHERE id=?", (i,)).rowcount


def mem_del(i=None):
    with db() as c:
        return sql(c, "DELETE FROM memori" + ("" if i is None else " WHERE id=?"), () if i is None else (i,)).rowcount


def mem_from_result(h):
    """Ubah hasil Boss/Dewan jadi teks jawaban yang enak dibaca di halaman Memori."""
    if h.get("jalur") == "dewan":
        isi = "\n\n".join(f"[{p['nama']}]\n{p['isi']}" for p in h.get("pendapat", []))
        return ", ".join(p["bot"] for p in h.get("pendapat", [])) + ", apex", (isi + "\n\nPUTUSAN DEWAN\n" + h.get("jawaban", "")).strip()
    bots = ", ".join(r["bot"] for r in h.get("rencana", [])) or "apex"
    return bots, h.get("jawaban", "")


# ---------- TENTANG KAMI (konten diedit admin dari web) ----------
TENTANG_DEFAULT = {"judul": "Tentang MCI", "semboyan": "", "deskripsi": "Cerita singkat tentang MCI akan ditulis di sini.", "sejarah": [], "anggota": []}


def tentang_get():
    with db() as c:
        r = sql(c, "SELECT v FROM pengaturan WHERE k=?", ("tentang",)).fetchone()
    try:
        return {**TENTANG_DEFAULT, **json.loads(r[0])} if r else dict(TENTANG_DEFAULT)
    except ValueError:
        return dict(TENTANG_DEFAULT)


def tentang_save(d):
    t = lambda v, n: str(v or "").strip()[:n]
    out = {"judul": t(d.get("judul"), 100) or "Tentang MCI", "semboyan": t(d.get("semboyan"), 150), "deskripsi": t(d.get("deskripsi"), 4000),
           "sejarah": [{"tahun": t(x.get("tahun"), 20), "judul": t(x.get("judul"), 120), "isi": t(x.get("isi"), 1500)}
                       for x in (d.get("sejarah") or [])[:60] if isinstance(x, dict) and (x.get("judul") or x.get("isi"))],
           "anggota": [{"nama": t(x.get("nama"), 80), "peran": t(x.get("peran"), 80), "bio": t(x.get("bio"), 600)}
                       for x in (d.get("anggota") or [])[:80] if isinstance(x, dict) and x.get("nama")]}
    with db() as c:
        sql(c, "INSERT INTO pengaturan (k,v) VALUES (?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", ("tentang", json.dumps(out)))
    return out


# ---------- LAB IOT (data sensor dari ESP32) ----------
IOT_LAST, IOT_HIST, IOT_SEEN, RULE_HIT, TG_APPS = {}, {}, {}, {}, {}
IOT_RULES = []
_IOT_N = [0]
PRIV = re.compile(r"(lat|lon|lng|gps)", re.I)   # data lokasi hanya untuk admin
DEV_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
KEY_OK = re.compile(r"^[a-z0-9_]{1,24}$")


def iot_clean(d):
    out = {}
    for k, v in list((d or {}).items())[:16]:
        k = str(k).lower()
        if not KEY_OK.match(k):
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            if isinstance(v, float) and v != v:   # NaN
                continue
            out[k] = v if isinstance(v, bool) else round(float(v), 4)
        elif isinstance(v, str):
            out[k] = v[:40]
    return out


def iot_load():
    """Saat start, isi grafik dari database (maks 240 titik terakhir per perangkat)."""
    try:
        with db() as c:
            rows = sql(c, "SELECT ts,perangkat,data FROM iot ORDER BY id DESC LIMIT 3000").fetchall()
            r = sql(c, "SELECT v FROM pengaturan WHERE k=?", ("iot_rules",)).fetchone()
        for ts, pr, data in reversed(rows):
            d = json.loads(data)
            IOT_HIST.setdefault(pr, deque(maxlen=240)).append((ts, d))
            IOT_LAST[pr] = {"ts": ts, "data": d}
        IOT_RULES[:] = json.loads(r[0]) if r else []
    except Exception:
        log.exception("Gagal memuat data IoT")


def iot_save(pr, ts, d):
    with db() as c:
        sql(c, "INSERT INTO iot (ts,perangkat,data) VALUES (?,?,?)", (ts, pr, json.dumps(d)))
        _IOT_N[0] += 1
        if _IOT_N[0] % 200 == 0:   # buang data lama supaya database gratis tidak penuh
            sql(c, "DELETE FROM iot WHERE ts < ?", (int(time.time()) - IOT_DAYS * 86400,))


def iot_public(admin, full=False):
    now, out = time.time(), []
    for pr in sorted(IOT_LAST):
        last = IOT_LAST[pr]
        hide = (lambda k: False) if admin else (lambda k: bool(PRIV.search(k)))
        data = {k: v for k, v in last["data"].items() if not hide(k)}
        item = {"perangkat": pr, "ts": last["ts"], "online": now - last["ts"] < 60, "data": data}
        if full:
            hist = {}
            for ts, d in list(IOT_HIST.get(pr, []))[-120:]:
                for k, v in d.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and not hide(k):
                        hist.setdefault(k, []).append([ts, v])
            item["hist"] = hist
        out.append(item)
    return out


def _cek(v, op, n):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    return v < n if op == "<" else v > n if op == ">" else v == n


async def iot_alert(pr, rule, val):
    teks = f"⚠️ ALARM {pr}: {rule['metrik']} = {val} ({rule['op']} {rule['nilai']})" + (f"\n{rule['pesan']}" if rule.get("pesan") else "")
    _begin("aegis", "Cek alarm IoT")
    try:
        _log("aegis", teks.replace("\n", " "), "Alarm", "iot")
        app_ = TG_APPS.get("aegis") or next(iter(TG_APPS.values()), None)
        for uid in ALLOWED if app_ else []:
            try:
                await app_.bot.send_message(chat_id=uid, text=teks)
            except Exception:
                log.warning("Alarm IoT tidak terkirim ke %s (sudah /start bot Aegis?)", uid)
        await asyncio.sleep(2)
    finally:
        _end("aegis")


def iot_rules_check(pr, d):
    now = time.time()
    for i, r in enumerate(IOT_RULES):
        if r.get("perangkat") not in ("*", pr) or r.get("metrik") not in d:
            continue
        if _cek(d[r["metrik"]], r.get("op"), float(r.get("nilai", 0))):
            key = (i, pr)
            if now - RULE_HIT.get(key, 0) > 600 and LOOP:   # jeda 10 menit per aturan
                RULE_HIT[key] = now
                asyncio.run_coroutine_threadsafe(iot_alert(pr, r, d[r["metrik"]]), LOOP)


# ---------- HUTANG ANGGOTA (khusus Bendahara + admin) ----------
# Aturan: maks HUTANG_MAX per anggota per bulan kalender, jatuh tempo H+HUTANG_HARI.
# Lewat jatuh tempo tanpa konfirmasi tambahan dari Bendahara -> anggota otomatis tercatat DIKELUARKAN.
def _nama_key(n):
    return re.sub(r"\s+", " ", str(n or "")).strip().lower()


def hutang_cek_telat():
    """Tandai anggota yang menunggak lewat jatuh tempo (tanpa konfirmasi) sebagai dikeluarkan. Return nama baru."""
    hari = today()
    baru = []
    with db() as c:
        rows = sql(c, "SELECT nama, jatuh_tempo FROM hutang WHERE status='belum' AND jatuh_tempo < ?", (hari,)).fetchall()
        sudah = {_nama_key(r[0]) for r in sql(c, "SELECT nama FROM dikeluarkan").fetchall()}
        for nama, jt in rows:
            k = _nama_key(nama)
            if k in sudah:
                continue
            sql(c, "INSERT INTO dikeluarkan (nama,tanggal,alasan) VALUES (?,?,?)", (nama, hari, f"Hutang lewat jatuh tempo {jt} tanpa konfirmasi tambahan"))
            sudah.add(k); baru.append(nama)
    return baru


def hutang_data():
    hutang_cek_telat()
    bulan, hari = today()[:7], today()
    with db() as c:
        rows = sql(c, "SELECT id,nama,jumlah,tanggal,jatuh_tempo,status,lunas,konfirmasi,catatan FROM hutang ORDER BY id DESC LIMIT 500").fetchall()
        keluar = sql(c, "SELECT nama,tanggal,alasan FROM dikeluarkan ORDER BY tanggal DESC").fetchall()
    items = [{"id": int(i), "nama": n, "jumlah": int(j or 0), "tanggal": t, "jatuh_tempo": jt, "status": st, "lunas": lu, "konfirmasi": bool(ko),
              "catatan": ca or "", "telat": st == "belum" and jt < hari} for i, n, j, t, jt, st, lu, ko, ca in rows]
    per = {}
    for x in items:
        if x["tanggal"][:7] == bulan:
            per[_nama_key(x["nama"])] = per.get(_nama_key(x["nama"]), 0) + x["jumlah"]
    belum = [x for x in items if x["status"] == "belum"]
    return {"items": items, "bulan": bulan, "pakai_bulan_ini": per, "batas": HUTANG_MAX, "hari": HUTANG_HARI,
            "total_belum": sum(x["jumlah"] for x in belum), "telat": sum(1 for x in belum if x["telat"]),
            "dikeluarkan": [{"nama": n, "tanggal": t, "alasan": a} for n, t, a in keluar]}


def hutang_tambah(nama, jumlah, catatan="", catat_kas=True):
    nama = re.sub(r"\s+", " ", str(nama or "")).strip()[:60]
    if not nama:
        raise ValueError("Nama anggota wajib diisi")
    try:
        jumlah = int(float(jumlah))
    except (TypeError, ValueError):
        raise ValueError("Jumlah harus angka")
    if jumlah <= 0:
        raise ValueError("Jumlah harus lebih dari 0")
    hutang_cek_telat()
    k, bulan = _nama_key(nama), today()[:7]
    with db() as c:
        if any(_nama_key(r[0]) == k for r in sql(c, "SELECT nama FROM dikeluarkan").fetchall()):
            raise ValueError(f"{nama} sudah dikeluarkan karena menunggak, tidak bisa pinjam lagi")
        rows = sql(c, "SELECT nama, jumlah, tanggal FROM hutang").fetchall()
        dipakai = sum(int(j or 0) for n, j, t in rows if _nama_key(n) == k and str(t)[:7] == bulan)
        if dipakai + jumlah > HUTANG_MAX:
            raise ValueError(f"Melebihi batas {rp(HUTANG_MAX)} per bulan. {nama} sudah pinjam {rp(dipakai)} bulan ini, sisa {rp(max(0, HUTANG_MAX - dipakai))}.")
        tgl = today()
        jt = (datetime.now(WIB) + timedelta(days=HUTANG_HARI)).strftime("%Y-%m-%d")
        sql(c, "INSERT INTO hutang (nama,jumlah,tanggal,jatuh_tempo,status,catatan,dibuat) VALUES (?,?,?,?,?,?,?)",
            (nama, jumlah, tgl, jt, "belum", str(catatan or "")[:200], _now()))
    if catat_kas:
        kas_add([{"tanggal": tgl, "keterangan": f"Pinjaman ke {nama} (jatuh tempo {jt})", "kategori": "Lainnya", "masuk": 0, "keluar": jumlah}], "hutang")
    return jt


def hutang_aksi(i, aksi, catat_kas=True):
    with db() as c:
        r = sql(c, "SELECT nama, jumlah, status, konfirmasi, jatuh_tempo FROM hutang WHERE id=?", (i,)).fetchone()
        if not r:
            raise ValueError("Data hutang tidak ditemukan")
        nama, jumlah, status, konf, jt = r
        if aksi == "lunas":
            if status == "lunas":
                raise ValueError("Sudah lunas")
            sql(c, "UPDATE hutang SET status='lunas', lunas=? WHERE id=?", (today(), i))
        elif aksi == "konfirmasi":
            if status == "lunas":
                raise ValueError("Sudah lunas")
            if konf:
                raise ValueError("Konfirmasi tambahan hanya bisa sekali")
            baru = (datetime.strptime(max(jt, today()), "%Y-%m-%d") + timedelta(days=HUTANG_HARI)).strftime("%Y-%m-%d")
            sql(c, "UPDATE hutang SET konfirmasi=1, jatuh_tempo=? WHERE id=?", (baru, i))
        elif aksi == "hapus":
            sql(c, "DELETE FROM hutang WHERE id=?", (i,))
        else:
            raise ValueError("Aksi tidak dikenal")
    if aksi == "lunas" and catat_kas:
        kas_add([{"tanggal": today(), "keterangan": f"Pelunasan pinjaman {nama}", "kategori": "Lainnya", "masuk": int(jumlah), "keluar": 0}], "hutang")
    return True


def hutang_pulihkan(nama):
    """Admin memulihkan anggota. Syarat: tidak ada pinjaman yang masih telat (lunasi / beri konfirmasi dulu)."""
    k = _nama_key(nama)
    with db() as c:
        telat = [r for r in sql(c, "SELECT nama FROM hutang WHERE status='belum' AND jatuh_tempo < ?", (today(),)).fetchall() if _nama_key(r[0]) == k]
        if telat:
            raise ValueError("Masih ada pinjaman yang telat. Tandai lunas atau beri konfirmasi tambahan dulu, baru pulihkan.")
        return sum(sql(c, "DELETE FROM dikeluarkan WHERE nama=?", (r[0],)).rowcount for r in sql(c, "SELECT nama FROM dikeluarkan").fetchall() if _nama_key(r[0]) == k)


# ---------- STOK BARANG (dipegang MCI.Nexus, ruang Storage Barang LT 3) ----------
# Semua orang boleh melihat; tambah/ubah/hapus khusus admin (dev). Foto barang ditempel di kardus 3D.
STOK = []   # cache tanpa data gambar


def stok_load():
    with db() as c:
        rows = sql(c, "SELECT id,nama,jumlah,satuan,kategori,minimum,catatan,diubah, CASE WHEN gambar IS NULL OR gambar='' THEN 0 ELSE 1 END FROM stok ORDER BY LOWER(nama)").fetchall()
    STOK[:] = [{"id": int(i), "nama": n, "jumlah": int(j or 0), "satuan": sa or "pcs", "kategori": k or "Umum", "minimum": int(mi or 0),
                "catatan": ca or "", "diubah": d or "", "foto": bool(g), "v": abs(hash(d or "")) % 100000} for i, n, j, sa, k, mi, ca, d, g in rows]
    return STOK


def stok_save(d, foto=None, mime=None, hapus_foto=False):
    nama = str(d.get("nama", "")).strip()[:80]
    if not nama:
        raise ValueError("Nama barang wajib diisi")
    try:
        jumlah, minimum = int(float(d.get("jumlah") or 0)), int(float(d.get("minimum") or 0))
    except (TypeError, ValueError):
        raise ValueError("Jumlah dan minimum harus angka")
    if not (0 <= jumlah <= 10**9 and 0 <= minimum <= 10**9):
        raise ValueError("Jumlah tidak masuk akal")
    vals = (nama, jumlah, str(d.get("satuan") or "pcs").strip()[:20], str(d.get("kategori") or "Umum").strip()[:40], minimum,
            str(d.get("catatan") or "").strip()[:300], _now())
    i = str(d.get("id") or "")
    with db() as c:
        if i.isdigit():
            sql(c, "UPDATE stok SET nama=?,jumlah=?,satuan=?,kategori=?,minimum=?,catatan=?,diubah=? WHERE id=?", (*vals, int(i)))
            sid = int(i)
        else:
            if len(STOK) >= 300:
                raise ValueError("Maksimal 300 jenis barang")
            sql(c, "INSERT INTO stok (nama,jumlah,satuan,kategori,minimum,catatan,diubah) VALUES (?,?,?,?,?,?,?)", vals)
            sid = int(sql(c, "SELECT MAX(id) FROM stok").fetchone()[0])
        if foto:
            sql(c, "UPDATE stok SET mime=?, gambar=? WHERE id=?", (mime or "image/jpeg", base64.b64encode(foto).decode(), sid))
        elif hapus_foto:
            sql(c, "UPDATE stok SET mime=NULL, gambar=NULL WHERE id=?", (sid,))
    stok_load()
    return sid


class StokFoto(BaseModel):
    nama: str
    kategori: str
    jumlah: int
    satuan: str
    catatan: str
    cocok_id: int


async def stok_parse(foto, mime="image/jpeg", petunjuk="", idx=0):
    """MCI.Nexus menghitung barang di foto. Hasilnya usulan; Dev memeriksa dulu sebelum disimpan."""
    stok_load()
    ada = "\n".join(f"- id {x['id']}: {x['nama']} ({x['jumlah']} {x['satuan']}, kategori {x['kategori']})" for x in STOK[:150]) or "(stok masih kosong)"
    kat = sorted({x["kategori"] for x in STOK if x["kategori"] != "Umum"})
    p = ("Kamu petugas gudang MCI. Lihat foto dan catat SETIAP jenis barang yang terlihat, satu objek per jenis barang.\n"
         "- jumlah: hitung unit yang benar-benar terlihat dengan teliti (hitung satu per satu, jangan menebak). Kalau ada kemasan/dus "
         "berlabel isi (mis. 'isi 10'), pakai angka di label dan sebutkan di catatan. Kalau barang sebagian tertutup, hitung yang terlihat saja dan beri catatan.\n"
         "- nama: nama barang yang spesifik dalam bahasa Indonesia, sertakan merek/tipe/ukuran kalau terbaca (mis. 'ESP32 DevKit V4', 'Kabel jumper male-female 20 cm').\n"
         "- satuan: pcs, buah, unit, lembar, roll, pack, dus, meter, botol, set, dll yang paling cocok.\n"
         "- kategori: singkat (mis. Mikrokontroler, Sensor, Kabel, Alat tulis, Perkakas, Konsumsi, Kebersihan). "
         + (f"Utamakan kategori yang sudah dipakai: {', '.join(kat)}. " if kat else "")
         + "\n- catatan: kondisi/warna/keterangan penting, maksimal 1 kalimat; kosongkan kalau tidak perlu.\n"
         "- cocok_id: kalau barang ini SAMA dengan salah satu barang di stok berikut, isi id-nya; kalau barang baru isi 0.\n"
         f"Stok yang sudah tercatat:\n{ada}\n"
         "Abaikan latar, meja, tangan, dan benda yang jelas bukan barang inventaris. Kalau tidak ada barang, kembalikan [].")
    if petunjuk:
        p += f"\n\nPetunjuk dari Dev: {petunjuk}"
    raw = await ask("nexus", p, parts=[types.Part.from_bytes(data=foto, mime_type=mime)], schema=list[StokFoto],
                    act="Menghitung stok dari foto", use_mcp=False, room="gudang", cache=False)
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    ids = {x["id"]: x for x in STOK}
    out = []
    for r in data if isinstance(data, list) else []:
        try:
            nama, jml = str(r.get("nama", "")).strip()[:80], int(r.get("jumlah", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if not nama or not 0 <= jml <= 10**6:
            continue
        cid = r.get("cocok_id") if r.get("cocok_id") in ids else 0
        lama = ids.get(cid)
        out.append({"nama": lama["nama"] if lama else nama, "nama_ai": nama, "kategori": (lama or {}).get("kategori") or str(r.get("kategori") or "Umum").strip()[:40] or "Umum",
                    "jumlah": jml, "satuan": (lama or {}).get("satuan") or str(r.get("satuan") or "pcs").strip()[:20] or "pcs",
                    "catatan": str(r.get("catatan") or "").strip()[:300], "cocok_id": cid, "jumlah_lama": lama["jumlah"] if lama else None,
                    "foto": [idx]})
    return out[:40]


STOK_FOTO_MAKS = 10


async def stok_parse_banyak(fotos, petunjuk=""):
    """Maks 10 foto sekaligus, dibaca paralel. Barang yang sama di beberapa foto digabung (jumlahnya dijumlahkan)."""
    hasil = await asyncio.gather(*(stok_parse(f, m, petunjuk, i) for i, (f, m) in enumerate(fotos)), return_exceptions=True)
    gagal = [i + 1 for i, h in enumerate(hasil) if isinstance(h, Exception)]
    for h in hasil:
        if isinstance(h, Exception):
            log.warning("Foto stok gagal dibaca: %s", h)
    if len(gagal) == len(hasil):
        raise hasil[0]
    gab = {}
    for h in hasil:
        for r in h if isinstance(h, list) else []:
            k = ("id", r["cocok_id"]) if r["cocok_id"] else ("nama", re.sub(r"[^a-z0-9]", "", r["nama"].lower()))
            if k in gab:
                g = gab[k]
                g["jumlah"] += r["jumlah"]
                g["foto"] = sorted(set(g["foto"] + r["foto"]))
                if r["catatan"] and r["catatan"] not in g["catatan"]:
                    g["catatan"] = (g["catatan"] + "; " + r["catatan"]).strip("; ")[:300]
            else:
                gab[k] = dict(r)
    return list(gab.values())[:60], gagal


def stok_simpan_ai(rows, fotos=None):
    """rows: [{cocok_id, mode: ganti|tambah, nama, kategori, jumlah, satuan, catatan, pakai_foto, foto_idx}] → simpan semuanya.
    fotos: daftar (bytes, mime) sesuai urutan foto yang diunggah; foto_idx menunjuk foto mana yang ditempel di kardus."""
    fotos = fotos or []
    stok_load()
    ids, n = {x["id"]: x for x in STOK}, 0
    for r in rows[:60]:   # periksa semua dulu supaya tidak tersimpan setengah
        try:
            float(r.get("jumlah") or 0)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Jumlah harus angka")
        if not str(r.get("nama") or "").strip() and not ids.get(int(r.get("cocok_id") or 0)):
            raise ValueError("Ada barang yang namanya kosong")
    for r in rows[:60]:
        try:
            jml = int(float(r.get("jumlah") or 0))
        except (TypeError, ValueError):
            raise ValueError("Jumlah harus angka")
        lama = ids.get(int(r.get("cocok_id") or 0))
        try:
            fi = int(r.get("foto_idx") or 0)
        except (TypeError, ValueError):
            fi = 0
        pf, mime = fotos[fi] if r.get("pakai_foto") and 0 <= fi < len(fotos) else (None, None)
        if lama:
            baru = lama["jumlah"] + jml if r.get("mode") == "tambah" else jml
            stok_save({"id": lama["id"], "nama": r.get("nama") or lama["nama"], "kategori": r.get("kategori") or lama["kategori"], "jumlah": baru,
                       "satuan": r.get("satuan") or lama["satuan"], "minimum": lama["minimum"], "catatan": r.get("catatan") or lama["catatan"]}, pf, mime)
        else:
            stok_save({"nama": r.get("nama"), "kategori": r.get("kategori"), "jumlah": jml, "satuan": r.get("satuan"), "minimum": 0,
                       "catatan": r.get("catatan")}, pf, mime)
        n += 1
    return n


def stok_jumlah(i, jumlah):
    with db() as c:
        n = sql(c, "UPDATE stok SET jumlah=?, diubah=? WHERE id=?", (max(0, int(jumlah)), _now(), i)).rowcount
    stok_load()
    return n


def stok_del(i):
    with db() as c:
        n = sql(c, "DELETE FROM stok WHERE id=?", (i,)).rowcount
    stok_load()
    return n


def stok_img(i):
    with db() as c:
        r = sql(c, "SELECT mime, gambar FROM stok WHERE id=?", (i,)).fetchone()
    return (r[0], base64.b64decode(r[1])) if r and r[1] else None


def stok_menipis():
    return [x for x in STOK if x["minimum"] and x["jumlah"] <= x["minimum"]]


def stok_teks(maks=40):
    if not STOK:
        return "Belum ada data stok."
    low = {x["id"] for x in stok_menipis()}
    out = [f"• {x['nama']}: {x['jumlah']} {x['satuan']}" + (f" ⚠️ menipis (min {x['minimum']})" if x["id"] in low else "")
           + (f" [{x['kategori']}]" if x["kategori"] != "Umum" else "") for x in STOK[:maks]]
    if len(STOK) > maks:
        out.append(f"… dan {len(STOK) - maks} barang lain (lihat web → Stok Barang)")
    return "\n".join(out)


def gudang_ctx(text):
    stok_load()
    if not STOK:
        return "", "📦 Storage Barang (stok masih kosong)"
    low = stok_menipis()
    return (f"[DATA STOK BARANG MCI dari Storage Barang, diperbarui {max(x['diubah'] for x in STOK)[:16].replace('T', ' ')}]\n{stok_teks(80)}\n"
            f"Barang menipis: {', '.join(x['nama'] for x in low) or 'tidak ada'}.\n"
            "Pakai data asli ini untuk menjawab (cek ketersediaan, hitung kebutuhan, saran belanja). Jangan mengarang barang yang tidak ada.\n\n",
            f"📦 data Storage Barang ({len(STOK)} jenis)")


# ---------- RUANG KERJA SAYAP TIMUR ----------
# Prompt yang cocok membuat bot bekerja di ruangan khusus DAN mendapat data asli:
#   lab    = Lab Komputer & IoT : data sensor ESP32 live (nilai terakhir + tren)
#   perpus = Perpustakaan       : arsip Memori, Skills, dan sejarah MCI (Tentang Kami)
#   observ = Observatorium      : cuaca, laut, matahari & fase bulan (Open-Meteo, gratis tanpa key)
ROOMS = ("lab", "perpus", "observ", "gudang")
ROOM_NAMA = {"lab": "Lab Komputer", "perpus": "Perpustakaan", "observ": "Observatorium", "gudang": "Storage Barang"}
R_GUDANG = re.compile(r"\b(stok|stock|inventaris|inventori|inventory|gudang|storage|persediaan|sisa barang|barang|komponen tersedia|ada berapa|tersisa|restock|belanja komponen|kurang berapa)\b", re.I)
R_IOT = re.compile(r"\b(sensor|iot|perangkat|device|jarak|suhu|temperatur|kelembap\w*|baterai|battery|tegangan|volt|rssi|sinyal|tangki|level air|ketinggian air|data (live|terakhir|terbaru)|alarm|pembacaan|bacaan|nilai terakhir|online|offline)\b", re.I)
R_LAB = re.compile(r"\b(kode|coding|ngoding|program\w*|sketch|sketsa|script|skrip|debug|error|bug|compile|kompil\w*|upload|firmware|library|arduino|esp32|esp8266|nano|uno|raspberry|micropython|python|c\+\+|javascript|html|mqtt|wifi|bluetooth|lora|i2c|spi|uart|serial|gpio|pinout|pin|wiring|rangkaian|solder|pcb|servo|motor|relay|lcd|oled|printer 3d|3d print\w*|drone)\b", re.I)
R_PERPUS = re.compile(r"\b(riset|research|rangkum\w*|ringkas\w*|resume|laporan|proposal|dokumen|referensi|jurnal|artikel|materi|belajar|pelajari|teori|definisi|apa itu|sejarah|histori|arsip|catatan|ingat|kemarin|sebelumnya|tadi|minggu lalu|bulan lalu|pernah|keputusan|putusan|hasil sidang|anggota|profil mci|tentang mci|semboyan)\b", re.I)
R_OBSERV = re.compile(r"\b(cuaca|hujan|gerimis|badai|petir|angin|mendung|cerah|prakiraan|ramalan|ombak|gelombang|laut|pasang|surut|berlayar|pelayaran|kapal|roket|antariksa|radjawali|peluncuran|luncur\w*|bintang|fase bulan|bulan purnama|purnama|gerhana|matahari|sunrise|sunset|terbit|terbenam|astronomi|langit|satelit|orbit|gps|navigasi|koordinat|lintang|bujur|visi|strategi|jangka panjang|roadmap)\b", re.I)
MCI_LAT = float(os.getenv("MCI_LAT", "-6.2088"))
MCI_LON = float(os.getenv("MCI_LON", "106.8456"))
MCI_LOKASI = os.getenv("MCI_LOKASI", "Jakarta")
_WX = {}
STOP = set("yang dan atau untuk dengan dari pada dalam akan sudah bisa tidak gimana bagaimana kenapa apakah tolong buat bikin kasih tentang lagi juga saja aja dong deh nya ini itu kita kamu saya aku".split())


def room_for(text, bot=None):
    """Pilih ruang kerja dari isi prompt. Ledger & Apex tetap di mejanya (ruang kas / CEO)."""
    if bot in ("ledger",):
        return None
    t = text or ""
    devs = [d for d in IOT_LAST if d in t.lower()]
    sk = {"lab": len(R_LAB.findall(t)) + 2 * (len(R_IOT.findall(t)) if IOT_LAST else 0) + 3 * len(devs),
          "perpus": len(R_PERPUS.findall(t)), "observ": len(R_OBSERV.findall(t)), "gudang": 2 * len(R_GUDANG.findall(t)) + (bot == "nexus")}
    best = max(sk, key=sk.get)
    if not sk[best]:
        return None
    if not sum(v for k, v in sk.items() if k != "gudang") and sk["gudang"] == 1:   # Nexus tanpa kata stok tetap di mejanya
        return None
    if bot == "apex" and best != "observ":
        return None
    return best


def _ago(ts):
    s = max(0, int(time.time() - ts))
    return f"{s} detik lalu" if s < 90 else f"{s // 60} menit lalu" if s < 5400 else f"{s // 3600} jam lalu"


def lab_ctx(text):
    if not IOT_LAST:
        return "", ""
    t = text.lower()
    if not (R_IOT.search(t) or any(d in t for d in IOT_LAST) or any(re.search(rf"\b{re.escape(k)}\b", t) for v in IOT_LAST.values() for k in v["data"] if len(k) > 3)):
        return "", ""
    baris = []
    for pr in sorted(IOT_LAST):
        last = IOT_LAST[pr]
        on = "online" if time.time() - last["ts"] < 60 else "OFFLINE"
        nilai = ", ".join(f"{k}={v}" for k, v in last["data"].items())
        baris.append(f"- {pr} ({on}, kirim terakhir {_ago(last['ts'])}, {datetime.fromtimestamp(last['ts'], WIB):%d/%m %H:%M} WIB): {nilai}")
        hist = list(IOT_HIST.get(pr, []))[-120:]
        if len(hist) > 2:
            span = max(1, hist[-1][0] - hist[0][0])
            for k in last["data"]:
                vs = [d[k] for _, d in hist if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool)]
                if len(vs) > 2:
                    arah = "naik" if vs[-1] > vs[0] else "turun" if vs[-1] < vs[0] else "stabil"
                    baris.append(f"    tren {k} ({span // 60} menit terakhir, {len(vs)} titik): min {min(vs):g}, maks {max(vs):g}, rata {sum(vs) / len(vs):.2f}, {arah}")
    if IOT_RULES:
        baris.append("Aturan alarm aktif: " + "; ".join(f"{r['perangkat']} {r['metrik']} {r['op']} {r['nilai']:g}" for r in IOT_RULES))
    return ("[DATA SENSOR LIVE dari Lab IoT MCI]\n" + "\n".join(baris) +
            "\nPakai data ini bila relevan: sebut angka, satuan, dan waktunya. Jangan mengarang nilai sensor lain.\n\n"), "📡 data sensor Lab IoT (live)"


def _kata(t):
    return {w for w in re.findall(r"[a-z0-9]{4,}", (t or "").lower()) if w not in STOP}


def perpus_ctx(text):
    q, parts, n = _kata(text), [], 0
    try:
        mem_clean()
        with db() as c:
            rows = sql(c, "SELECT waktu,bot,perintah,jawaban FROM memori ORDER BY id DESC LIMIT 300").fetchall()
        hits = sorted(((len(q & _kata(r[2] + " " + r[3][:3000])), r) for r in rows), key=lambda x: -x[0])
        top = [r for s, r in hits if s >= max(2, min(3, len(q) // 3))][:3]
        for w, b, p, j in top:
            parts.append(f"- [{w[:16].replace('T', ' ')}, {b}] Perintah: {p[:200]}\n  Jawaban: {j[:600]}")
        n = len(top)
    except Exception:
        log.exception("Perpustakaan: gagal cari memori")
    sk = [s for s in SKILLS.values() if len(q & _kata(s["nama"].replace("-", " ") + " " + s["deskripsi"] + " " + s.get("pemicu", ""))) >= 1][:3]
    for s in sk:
        parts.append(f"- Skill {s['nama']} ({s['bot']}): {s['deskripsi']}")
    if re.search(r"\b(mci|mahardika|sejarah|histori|anggota|semboyan|founder|pendiri|tim kita)\b", text, re.I):
        try:
            tk = tentang_get()
            sej = "; ".join(f"{x['tahun']} {x['judul']}" for x in sorted(tk.get("sejarah", []), key=lambda x: x.get("tahun", ""))[:20])
            ang = ", ".join(f"{x['nama']} ({x['peran']})" if x.get("peran") else x["nama"] for x in tk.get("anggota", [])[:30])
            parts.append(f"- Tentang MCI: {tk.get('judul')}. Semboyan: {tk.get('semboyan') or '-'}. {tk.get('deskripsi', '')[:500]}\n  Sejarah: {sej or '-'}\n  Anggota: {ang or '-'}")
            n += 1
        except Exception:
            log.exception("Perpustakaan: gagal baca Tentang Kami")
    if not parts:
        return "", ""
    note = f"📚 arsip Perpustakaan ({n} catatan)" if n else "📚 katalog skill Perpustakaan"
    return ("[ARSIP PERPUSTAKAAN MCI: catatan lama yang mungkin relevan]\n" + "\n".join(parts) +
            "\nGunakan bila relevan dan sebut kalau jawaban mengacu ke catatan lama. Abaikan bila tidak terkait.\n\n"), note


def _fase_bulan(dt):
    hari = (dt - datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)).total_seconds() / 86400 % 29.530588853
    nama = ["bulan baru", "sabit awal", "kuartal pertama", "cembung awal", "purnama", "cembung akhir", "kuartal akhir", "sabit akhir"]
    return nama[int((hari / 29.530588853) * 8 + .5) % 8], hari


def _lokasi():
    for pr, v in IOT_LAST.items():
        d = v["data"]
        la = next((d[k] for k in d if re.fullmatch(r"lat\w*", k) and isinstance(d[k], (int, float))), None)
        lo = next((d[k] for k in d if re.fullmatch(r"(lon|lng)\w*", k) and isinstance(d[k], (int, float))), None)
        if la is not None and lo is not None and -90 <= la <= 90 and -180 <= lo <= 180 and (la or lo):
            return la, lo, f"posisi GPS {pr}"
    return MCI_LAT, MCI_LON, MCI_LOKASI


WMO = {0: "cerah", 1: "cerah berawan", 2: "berawan sebagian", 3: "mendung", 45: "kabut", 48: "kabut", 51: "gerimis ringan", 53: "gerimis", 55: "gerimis lebat",
       61: "hujan ringan", 63: "hujan sedang", 65: "hujan lebat", 80: "hujan lokal ringan", 81: "hujan lokal", 82: "hujan lokal lebat", 95: "badai petir", 96: "badai petir + es", 99: "badai petir + es"}


async def observ_ctx(text):
    la, lo, nama = _lokasi()
    laut = bool(re.search(r"\b(ombak|gelombang|laut|pasang|surut|berlayar|pelayaran|kapal)\b", text, re.I))
    key = (round(la, 2), round(lo, 2), laut)
    if key in _WX and time.time() - _WX[key][0] < 600:
        return _WX[key][1]
    baris = []
    try:
        async with httpx.AsyncClient(timeout=8) as h:
            r = await h.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": la, "longitude": lo, "timezone": "Asia/Jakarta", "forecast_days": 2,
                "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_direction_10m,cloud_cover",
                "hourly": "precipitation_probability,weather_code,temperature_2m",
                "daily": "sunrise,sunset,uv_index_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max"})
            w = r.json()
            c = w["current"]
            baris.append(f"Sekarang ({c['time'].replace('T', ' ')} WIB): {WMO.get(c['weather_code'], 'kode ' + str(c['weather_code']))}, {c['temperature_2m']}°C, "
                         f"lembap {c['relative_humidity_2m']}%, awan {c['cloud_cover']}%, hujan {c['precipitation']} mm, angin {c['wind_speed_10m']} km/j dari {c['wind_direction_10m']}°")
            hr, now = w["hourly"], datetime.now(WIB).strftime("%Y-%m-%dT%H")
            i0 = next((i for i, t in enumerate(hr["time"]) if t.startswith(now)), 0)
            jam = [f"{hr['time'][i][11:16]} {WMO.get(hr['weather_code'][i], '?')} {hr['temperature_2m'][i]}°C hujan {hr['precipitation_probability'][i]}%"
                   for i in range(i0, min(len(hr["time"]), i0 + 12), 3)]
            baris.append("12 jam ke depan: " + "; ".join(jam))
            d = w["daily"]
            for i, lbl in enumerate(("Hari ini", "Besok")):
                baris.append(f"{lbl}: matahari terbit {d['sunrise'][i][11:]}, terbenam {d['sunset'][i][11:]}, UV maks {d['uv_index_max'][i]}, "
                             f"peluang hujan {d['precipitation_probability_max'][i]}%, total hujan {d['precipitation_sum'][i]} mm, angin maks {d['wind_speed_10m_max'][i]} km/j")
            if laut:
                m = (await h.get("https://marine-api.open-meteo.com/v1/marine", params={
                    "latitude": la, "longitude": lo, "timezone": "Asia/Jakarta", "forecast_days": 1,
                    "current": "wave_height,wave_direction,wave_period,swell_wave_height",
                    "daily": "wave_height_max"})).json()
                if "current" in m:
                    mc = m["current"]
                    baris.append(f"Laut: tinggi gelombang {mc.get('wave_height')} m (maks hari ini {m.get('daily', {}).get('wave_height_max', ['?'])[0]} m), "
                                 f"periode {mc.get('wave_period')} dtk, arah {mc.get('wave_direction')}°, swell {mc.get('swell_wave_height')} m")
                else:
                    baris.append("Laut: data gelombang tidak tersedia untuk titik ini (mungkin di darat).")
    except Exception as e:
        log.warning("Observatorium: cuaca gagal diambil (%s)", e)
        baris.append("(data cuaca sedang tidak bisa diambil)")
    fase, umur = _fase_bulan(datetime.now(timezone.utc))
    baris.append(f"Bulan: {fase} (umur {umur:.1f} hari dari 29,5)")
    out = (f"[DATA OBSERVATORIUM MCI · {nama} ({la:.3f}, {lo:.3f}) · sumber Open-Meteo]\n" + "\n".join(baris) +
           "\nPakai data ini bila relevan; sebut waktu dan lokasinya. Ini prakiraan, bukan kepastian.\n\n", f"🔭 data Observatorium (cuaca {nama})")
    _WX[key] = (time.time(), out)
    return out


async def room_prep(text, bot):
    """-> (ruang, konteks, catatan sumber, label aktivitas)"""
    room = room_for(text, bot)
    if not room:
        return None, "", "", None
    try:
        if room == "lab":
            ctx, note = await asyncio.to_thread(lab_ctx, text)
            act = "Cek data sensor di Lab" if ctx else "Ngoding di Lab Komputer"
        elif room == "perpus":
            ctx, note = await asyncio.to_thread(perpus_ctx, text)
            act = "Buka arsip di Perpustakaan"
        elif room == "gudang":
            ctx, note = await asyncio.to_thread(gudang_ctx, text)
            act = "Cek stok di Storage Barang"
        else:
            ctx, note = await observ_ctx(text)
            act = "Analisis di Observatorium"
    except Exception:
        log.exception("Ruang %s gagal menyiapkan data", room)
        ctx, note, act = "", "", "Bekerja di " + ROOM_NAMA[room]
    return room, ctx, note, act


# ---------- LAPORAN MINGGUAN (otomatis tiap Jumat) ----------
# Isi: kegiatan bot 7 hari terakhir, rekap kas minggu ini, pemakaian token/gambar/IoT, dan ide pengembangan baru.
# Scribe menyusun naskahnya di Perpustakaan; Apex mengirim ke Telegram semua ALLOWED_USER_IDS.
LAP_HARI = max(0, min(6, int(os.getenv("LAPORAN_HARI", "4"))))     # 0=Senin ... 4=Jumat
LAP_JAM = max(0, min(23, int(os.getenv("LAPORAN_JAM", "17"))))     # jam WIB
HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
FINAL = ("Selesai", "Gagal", "Alarm")


class Ide(BaseModel):
    judul: str
    kenapa: str
    langkah: str
    level: str


class NaskahLaporan(BaseModel):
    ringkasan: str
    catatan_kas: str
    sorotan: list[str]
    ide: list[Ide]


def akt_save(e):
    try:
        with db() as c:
            sql(c, "INSERT INTO aktivitas (waktu,bot,tugas,status,jalur) VALUES (?,?,?,?,?)",
                (_now(), e["bot"], e["tugas"][:300], e["status"], e["jalur"]))
    except Exception:
        log.exception("Gagal menyimpan aktivitas")


def _minggu_key(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def laporan_data(hari=7):
    now = datetime.now(WIB)
    t0 = now - timedelta(days=hari)
    d0, d1 = t0.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
    out = {"dari": d0, "sampai": d1, "minggu": _minggu_key(now)}
    with db() as c:
        akt = sql(c, "SELECT waktu,bot,tugas,status,jalur FROM aktivitas WHERE waktu >= ? ORDER BY id DESC", (t0.isoformat(timespec="seconds"),)).fetchall()
        kas = sql(c, "SELECT tanggal,keterangan,kategori,masuk,keluar FROM kas WHERE tanggal >= ? AND tanggal <= ? ORDER BY tanggal, id", (d0, d1)).fetchall()
        tot = sql(c, "SELECT COALESCE(SUM(masuk),0), COALESCE(SUM(keluar),0) FROM kas").fetchone()
        pk = sql(c, "SELECT COALESCE(SUM(masuk+keluar+pikir),0), COALESCE(SUM(panggilan),0) FROM pemakaian WHERE tanggal >= ? AND tanggal <= ?", (d0, d1)).fetchone()
        gb = sql(c, "SELECT COUNT(*) FROM gambar WHERE dibuat >= ?", (t0.isoformat(timespec="seconds"),)).fetchone()[0]
        mm = sql(c, "SELECT COUNT(*) FROM memori WHERE waktu >= ?", (t0.isoformat(timespec="seconds"),)).fetchone()[0]
        iot = sql(c, "SELECT perangkat, COUNT(*) FROM iot WHERE ts >= ? GROUP BY perangkat", (int(t0.timestamp()),)).fetchall()
        ide_lama = sql(c, "SELECT data FROM laporan ORDER BY id DESC LIMIT 4").fetchall()
    # Postgres mengembalikan SUM sebagai Decimal: ubah ke int supaya bisa dihitung & disimpan sebagai JSON
    tot, pk = [int(x or 0) for x in tot], [int(x or 0) for x in pk]
    gb, mm, iot = int(gb or 0), int(mm or 0), [(p, int(n)) for p, n in iot]
    kas = [(t, k, g, int(m or 0), int(x or 0)) for t, k, g, m, x in kas]
    per_bot, per_jalur, gagal = {}, {}, 0
    for w, b, t, s, j in akt:
        nama = AGENTS[b]["nama"] if b in AGENTS else ("MCI.Canvas" if b == "canvas" else "Sidang Dewan" if b == "dewan" else b)
        per_bot[nama] = per_bot.get(nama, 0) + 1
        per_jalur[j] = per_jalur.get(j, 0) + 1
        gagal += s == "Gagal"
    out["aktivitas"] = {"total": len(akt), "gagal": gagal, "per_bot": dict(sorted(per_bot.items(), key=lambda x: -x[1])), "per_jalur": per_jalur,
                        "sidang": [t for w, b, t, s, j in akt if j == "dewan"][:8], "alarm": [t for w, b, t, s, j in akt if s == "Alarm"][:8],
                        "contoh": [{"waktu": w[5:16].replace("T", " "), "bot": b, "tugas": t, "status": s} for w, b, t, s, j in akt[:40]]}
    rows = [{"tanggal": t, "keterangan": k, "kategori": g, "masuk": m, "keluar": x} for t, k, g, m, x in kas]
    s = kas_summary(rows)
    out["kas"] = {**s, "transaksi": len(rows), "saldo_total": tot[0] - tot[1], "terbesar_keluar": sorted([r for r in rows if r["keluar"]], key=lambda r: -r["keluar"])[:3],
                  "terbesar_masuk": sorted([r for r in rows if r["masuk"]], key=lambda r: -r["masuk"])[:3], "rows": rows[-30:]}
    out["lain"] = {"token": pk[0], "panggilan_ai": pk[1], "gambar": gb, "memori": mm, "iot": {p: n for p, n in iot}}
    lama = []
    for (d,) in ide_lama:
        try:
            lama += [i["judul"] for i in json.loads(d).get("ide", [])]
        except (ValueError, TypeError, KeyError):
            pass
    out["ide_lama"] = list(dict.fromkeys(lama))[:20]
    try:
        h = hutang_data()
        out["hutang"] = {"total_belum": h["total_belum"], "telat": h["telat"], "dikeluarkan": [x["nama"] for x in h["dikeluarkan"]][:10]}
    except Exception:
        log.exception("ringkasan hutang gagal")
    stok_load()
    out["stok"] = {"jenis": len(STOK), "unit": sum(x["jumlah"] for x in STOK), "menipis": [f"{x['nama']} ({x['jumlah']} {x['satuan']})" for x in stok_menipis()],
                   "teks": stok_teks(60)}
    return out


def laporan_teks(d, n):
    a, k, l = d["aktivitas"], d["kas"], d["lain"]
    T = [f"📋 LAPORAN MINGGUAN MCI HQ", f"Periode {d['dari']} s/d {d['sampai']} ({d['minggu']})", ""]
    if n.get("ringkasan"):
        T += ["📝 RINGKASAN", n["ringkasan"], ""]
    T += ["🤖 KEGIATAN BOT", f"Total {a['total']} tugas · {a['gagal']} gagal"]
    T += [f"• {b}: {c}" for b, c in list(a["per_bot"].items())[:11]]
    if a["sidang"]:
        T += ["", "🏛 Sidang dewan:"] + [f"• {x[:90]}" for x in a["sidang"]]
    if a["alarm"]:
        T += ["", "⚠️ Alarm IoT:"] + [f"• {x[:90]}" for x in a["alarm"]]
    if n.get("sorotan"):
        T += ["", "✨ Sorotan:"] + [f"• {x}" for x in n["sorotan"][:6]]
    T += ["", "💰 REKAP KAS MINGGU INI", f"Masuk {rp(k['masuk'])} · Keluar {rp(k['keluar'])} · Selisih {rp(k['saldo'])}",
          f"Saldo total kas sekarang: {rp(k['saldo_total'])} ({k['transaksi']} transaksi minggu ini)"]
    for g, v in sorted(k["kategori"].items(), key=lambda x: -(x[1]["masuk"] + x[1]["keluar"]))[:6]:
        T.append(f"• {g}: " + " / ".join(x for x in (f"+{rp(v['masuk'])}" if v["masuk"] else "", f"-{rp(v['keluar'])}" if v["keluar"] else "") if x))
    if k["terbesar_keluar"]:
        T.append("Pengeluaran terbesar: " + "; ".join(f"{r['keterangan']} {rp(r['keluar'])}" for r in k["terbesar_keluar"]))
    if n.get("catatan_kas"):
        T += [f"💡 {n['catatan_kas']}"]
    hu = d.get("hutang") or {}
    if hu:
        T += ["", "🧾 HUTANG ANGGOTA", f"Belum lunas {rp(hu['total_belum'])} · telat bayar {hu['telat']}" + (f" · dikeluarkan: {', '.join(hu['dikeluarkan'])}" if hu["dikeluarkan"] else "")]
    st = d.get("stok") or {}
    if st:
        T += ["", "📦 SISA STOK BARANG (dicek MCI.Nexus di Storage Barang)", f"{st['jenis']} jenis barang · total {st['unit']:,} unit".replace(",", "."), st["teks"]]
        if st["menipis"]:
            T.append("⚠️ Perlu restock: " + ", ".join(st["menipis"]))
    T += ["", "📊 PEMAKAIAN", f"Token AI {l['token']:,} ({l['panggilan_ai']} panggilan) · Gambar {l['gambar']} · Memori disimpan {l['memori']}".replace(",", ".")]
    if l["iot"]:
        T.append("Data IoT: " + ", ".join(f"{p} {c} kiriman" for p, c in l["iot"].items()))
    if n.get("ide"):
        T += ["", "🚀 IDE PENGEMBANGAN BARU"]
        for i, x in enumerate(n["ide"][:5], 1):
            T += [f"{i}. {x['judul']} [{x['level']}]", f"   Kenapa: {x['kenapa']}", f"   Mulai dari: {x['langkah']}"]
    return "\n".join(T)


async def laporan_buat(kirim=True, sumber="otomatis"):
    d = await asyncio.to_thread(laporan_data)
    a, k = d["aktivitas"], d["kas"]
    ringkas = (f"Periode {d['dari']} s/d {d['sampai']}.\nKegiatan: {a['total']} tugas, {a['gagal']} gagal. Per bot: {a['per_bot']}. Jalur: {a['per_jalur']}.\n"
               f"Contoh tugas: " + "; ".join(f"{x['bot']}: {x['tugas'][:80]} ({x['status']})" for x in a["contoh"][:25]) +
               f"\nSidang dewan: {a['sidang']}\nAlarm IoT: {a['alarm']}\n"
               f"Kas minggu ini: masuk {k['masuk']}, keluar {k['keluar']}, selisih {k['saldo']}, saldo total {k['saldo_total']}, per kategori {k['kategori']}.\n"
               f"Transaksi: " + "; ".join(f"{r['tanggal']} {r['keterangan']} +{r['masuk']} -{r['keluar']}" for r in k["rows"]) +
               f"\nStok barang: {d['stok']['jenis']} jenis, menipis: {d['stok']['menipis'] or 'tidak ada'}"
               f"\nLain: {d['lain']}\nIde minggu-minggu lalu (JANGAN diulang): {d['ide_lama'] or '-'}")
    try:
        tk = await asyncio.to_thread(tentang_get)
        profil = f"{tk.get('judul')}: {tk.get('deskripsi', '')[:600]} Sejarah: " + "; ".join(f"{x['tahun']} {x['judul']}" for x in tk.get("sejarah", [])[:15])
    except Exception:
        profil = ""
    naskah = {}
    try:
        raw = await ask("scribe", f"[LAPORAN MINGGUAN MCI]\nProfil MCI: {profil}\nData minggu ini:\n{ringkas}\n\n"
                        "Tulis dalam bahasa Indonesia santai tapi rapi:\n"
                        "- ringkasan: 3-4 kalimat apa yang terjadi minggu ini (pakai angka asli, jangan mengarang).\n"
                        "- catatan_kas: 1-2 kalimat insight kas (pola, risiko, saran hemat). Kalau tidak ada transaksi, bilang begitu.\n"
                        "- sorotan: 2-4 poin pencapaian/temuan penting dari data.\n"
                        "- ide: TEPAT 5 ide pengembangan BARU untuk MCI (hardware/IoT/AI/web/kapal monitoring/radar/roket/komunitas), "
                        "beda dari ide minggu lalu, konkret dan bisa dikerjakan tim kecil dengan budget pelajar. "
                        "Tiap ide: judul singkat, kenapa (manfaat), langkah (langkah pertama yang bisa dimulai minggu ini), level (Mudah/Sedang/Menantang).",
                        schema=NaskahLaporan, model=MODEL, act="Menyusun laporan mingguan", use_mcp=False, cache=False, room="perpus")
        naskah = json.loads(raw)
    except Exception:
        log.exception("Naskah laporan AI gagal, laporan tetap dibuat dari data")
    teks = laporan_teks(d, naskah)
    simpan = {**{k2: v for k2, v in d.items() if k2 != "ide_lama"}, **naskah}
    with db() as c:
        q = "INSERT INTO laporan (minggu,dibuat,dari,sampai,sumber,isi,data) VALUES (?,?,?,?,?,?,?)"
        p = (d["minggu"], _now(), d["dari"], d["sampai"], sumber, teks, json.dumps(simpan, default=lambda o: float(o) if hasattr(o, "as_integer_ratio") else str(o)))
        lid = sql(c, q + " RETURNING id", p).fetchone()[0] if PG else sql(c, q, p).lastrowid
    terkirim = 0
    if kirim:
        terkirim = await laporan_kirim(teks)
    _log("scribe", f"Laporan mingguan {d['minggu']}" + (f" (terkirim ke {terkirim} orang)" if kirim else ""), "Selesai", "laporan")
    return {"id": lid, "minggu": d["minggu"], "isi": teks, "terkirim": terkirim}


async def laporan_kirim(teks):
    app_ = TG_APPS.get("apex") or TG_APPS.get("scribe") or next(iter(TG_APPS.values()), None)
    n = 0
    if not app_:
        return 0
    for uid in ALLOWED:
        try:
            for i in range(0, len(teks), 4000):
                await app_.bot.send_message(chat_id=uid, text=teks[i:i + 4000])
            n += 1
        except Exception:
            log.warning("Laporan tidak terkirim ke %s (sudah /start bot Apex?)", uid)
    return n


def laporan_list():
    with db() as c:
        rows = sql(c, "SELECT id,minggu,dibuat,dari,sampai,sumber,isi FROM laporan ORDER BY id DESC LIMIT 30").fetchall()
    return [dict(zip(("id", "minggu", "dibuat", "dari", "sampai", "sumber", "isi"), r)) for r in rows]


def _lap_jatuh_tempo(now):
    """True kalau minggu ini sudah lewat jadwal (mis. Jumat 17:00) dan laporan otomatis belum dibuat.
    Kalau server sempat mati saat jadwal, laporan tetap dibuat begitu server hidup lagi (Sabtu/Minggu)."""
    if not SET.get("laporan_on", True):
        return False
    if now.weekday() < LAP_HARI or (now.weekday() == LAP_HARI and now.hour < LAP_JAM):
        return False
    return SET.get("laporan_terakhir") != _minggu_key(now)


async def laporan_jadwal():
    await asyncio.sleep(60)
    while True:
        try:
            now = datetime.now(WIB)
            if _lap_jatuh_tempo(now):
                SET["laporan_terakhir"] = _minggu_key(now)   # tandai dulu supaya tidak dobel
                await asyncio.to_thread(set_save)
                log.info("Membuat laporan mingguan %s", SET["laporan_terakhir"])
                await laporan_buat(kirim=True, sumber="otomatis")
        except Exception:
            log.exception("Jadwal laporan mingguan gagal")
        try:   # cek hutang telat tiap 5 menit; anggota yang lewat H+3 tanpa konfirmasi otomatis dikeluarkan
            baru = await asyncio.to_thread(hutang_cek_telat)
            app_ = TG_APPS.get("apex")
            for nama in baru:
                log.info("Anggota dikeluarkan (hutang telat): %s", nama)
                for aid in ALLOWED if app_ else []:
                    try:
                        await app_.bot.send_message(aid, f"🧾 {nama} otomatis DIKELUARKAN: hutang lewat jatuh tempo tanpa konfirmasi tambahan. Admin bisa memulihkan di web → Hutang.")
                    except Exception:
                        pass
        except Exception:
            log.exception("Cek hutang telat gagal")
        await asyncio.sleep(300)


# ---------- WEB ----------
app = Flask(__name__, static_folder=None)


@app.after_request
def _gzip(resp):
    """Kompres JSON besar: hemat kuota bandwidth Render (5 GB/bulan) saat banyak viewer membuka web."""
    if (resp.status_code == 200 and not resp.direct_passthrough and resp.mimetype == "application/json"
            and "gzip" in request.headers.get("Accept-Encoding", "") and "Content-Encoding" not in resp.headers):
        data = resp.get_data()
        if len(data) > 800:
            resp.set_data(gzip.compress(data, 5))
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Vary"] = "Accept-Encoding"
    return resp


_PAGE = {}


def page_bytes():
    """index.html dibaca sekali lalu disimpan versi asli + gzip di memori."""
    if not _PAGE:
        raw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html"), "rb").read()
        _PAGE.update(raw=raw, gz=gzip.compress(raw, 6), etag='"' + hashlib.md5(raw).hexdigest()[:16] + '"')
    return _PAGE


_OFFICE_CACHE = {}
FAILS = {}
_KLOCK = threading.Lock()


def member_of(r):
    if r and r.startswith("m:"):
        m = MEMBERS.get(int(r[2:]))
        return m if m and m["aktif"] else None
    return None


def perms_of(r):
    if r == "admin":
        return {"*"}
    m = member_of(r)
    if not m:
        return set()
    out = set()
    for j in m["jabatan"]:
        out |= JABATAN_PERM.get(j, set())
    for d in m["divisi"]:
        out |= DIVISI_PERM.get(d, set())
    return out


def can(perm, r):
    p = perms_of(r)
    return "*" in p or perm in p


def is_admin(r):
    return "*" in perms_of(r)


def mid_of(r):
    """id anggota untuk suara/catatan; token admin master = 0 (Dev)."""
    m = member_of(r)
    return m["id"] if m else (0 if r == "admin" else None)


def nama_peran(r):
    if r == "admin":
        return "Admin"
    m = member_of(r)
    if m:
        return m["nama"]
    return "Viewer" if r == "viewer" else "-"


def jabatan_teks(m):
    return ", ".join(JABATAN.get(j, j) for j in m["jabatan"]) or "-"


def struktur_baca():
    with db() as c:
        r = sql(c, "SELECT v FROM pengaturan WHERE k=?", ("struktur",)).fetchone()
    try:
        d = json.loads(r[0]) if r else {}
    except ValueError:
        d = {}
    return {"divisi": dict(d.get("divisi") or {}), "jabatan": dict(d.get("jabatan") or {})}


def struktur_load():
    """Bangun ulang DIVISI/JABATAN beserta izinnya: bawaan + tambahan/ubahan dari Dev (diubah di tempat)."""
    st = struktur_baca()
    for jenis, nama, izin in (("divisi", DIVISI, DIVISI_PERM), ("jabatan", JABATAN, JABATAN_PERM)):
        dn, di = _DEF[jenis]
        nama.clear(); nama.update(dn)
        izin.clear(); izin.update({k: set(v) for k, v in di.items()})
        for k, v in st[jenis].items():
            if k == "admin" or not isinstance(v, dict):
                continue
            nama[k] = str(v.get("nama") or nama.get(k) or k)[:40]
            izin[k] = {x for x in (v.get("izin") or []) if x in IZIN_PILIHAN}
    return st


def struktur_out():
    def daftar(jenis, nama, izin):
        bawaan = _DEF[jenis][0]
        return [{"key": k, "nama": v, "izin": sorted(izin.get(k, set())), "bawaan": k in bawaan, "kunci": k == "admin",
                 "dipakai": sum(1 for m in MEMBERS.values() if k in m[jenis])} for k, v in nama.items()]
    return {"divisi": daftar("divisi", DIVISI, DIVISI_PERM), "jabatan": daftar("jabatan", JABATAN, JABATAN_PERM),
            "izin": IZIN_PILIHAN, "maks": STRUKTUR_MAKS}


def _slug(nama):
    return re.sub(r"[^a-z0-9]+", "_", nama.lower()).strip("_")[:24] or "baru"


def struktur_simpan(d):
    jenis = d.get("jenis")
    if jenis not in ("divisi", "jabatan"):
        raise ValueError("Jenis harus divisi atau jabatan")
    daftar = DIVISI if jenis == "divisi" else JABATAN
    nama = re.sub(r"\s+", " ", str(d.get("nama", ""))).strip()[:40]
    if not nama:
        raise ValueError("Nama wajib diisi")
    key = str(d.get("key") or "")
    if key == "admin":
        raise ValueError("Admin/Dev tidak bisa diubah")
    if any(v.lower() == nama.lower() and k != key for k, v in daftar.items()):
        raise ValueError(f"{nama} sudah ada")
    if key and key not in daftar:
        raise ValueError("Tidak ditemukan")
    if not key:
        if len(daftar) >= STRUKTUR_MAKS[jenis]:
            raise ValueError(f"Maksimal {STRUKTUR_MAKS[jenis]} {jenis}")
        key, n = _slug(nama), 2
        base = key
        while key in daftar or key == "admin":
            key, n = f"{base}_{n}", n + 1
    izin = sorted({x for x in (d.get("izin") or []) if x in IZIN_PILIHAN})
    st = struktur_baca()
    st[jenis][key] = {"nama": nama, "izin": izin}
    with db() as c:
        sql(c, "INSERT INTO pengaturan (k,v) VALUES (?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", ("struktur", json.dumps(st)))
    struktur_load()
    members_load()


def struktur_hapus(jenis, key):
    if jenis not in ("divisi", "jabatan"):
        raise ValueError("Jenis harus divisi atau jabatan")
    if key in _DEF[jenis][0]:
        raise ValueError("Divisi/jabatan bawaan tidak bisa dihapus (izinnya tetap bisa diubah)")
    st = struktur_baca()
    if key not in st[jenis]:
        raise ValueError("Tidak ditemukan")
    st[jenis].pop(key)
    with db() as c:
        sql(c, "INSERT INTO pengaturan (k,v) VALUES (?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", ("struktur", json.dumps(st)))
        for m in MEMBERS.values():   # lepas dari anggota yang memakainya
            if key in m[jenis]:
                baru = [x for x in m[jenis] if x != key] or (["anggota"] if jenis == "jabatan" else [])
                sql(c, f"UPDATE anggota SET {jenis}=? WHERE id=?", (json.dumps(baru), m["id"]))
    struktur_load()
    members_load()


def members_load():
    with db() as c:
        rows = sql(c, "SELECT id,nama,token,divisi,jabatan,aktif FROM anggota ORDER BY id").fetchall()
    MEMBERS.clear()
    for i, n, t, d, j, a in rows:
        try:
            dv, jb = json.loads(d or "[]"), json.loads(j or "[]")
        except ValueError:
            dv, jb = [], []
        MEMBERS[int(i)] = {"id": int(i), "nama": n, "token": t, "divisi": [x for x in dv if x in DIVISI][:2],
                           "jabatan": [x for x in jb if x in JABATAN][:3], "aktif": bool(a)}
    return MEMBERS


def token_baru(nama):
    depan = re.sub(r"[^a-z0-9]", "", (str(nama).strip().split() or ["anggota"])[0].lower()) or "anggota"
    return f"{depan}-" + "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(6))


def member_simpan(d):
    nama = re.sub(r"\s+", " ", str(d.get("nama", ""))).strip()[:60]
    if not nama:
        raise ValueError("Nama wajib diisi")
    dv = [x for x in dict.fromkeys(d.get("divisi") or []) if x in DIVISI]
    jb = [x for x in dict.fromkeys(d.get("jabatan") or []) if x in JABATAN]
    if len(dv) > 2:
        raise ValueError("Maksimal 2 divisi per orang")
    if not jb:
        jb = ["anggota"]
    if len(jb) > 3:
        raise ValueError("Maksimal 3 jabatan per orang")
    aktif = 0 if d.get("aktif") is False else 1
    i = str(d.get("id") or "")
    with db() as c:
        if i.isdigit():
            sql(c, "UPDATE anggota SET nama=?, divisi=?, jabatan=?, aktif=? WHERE id=?", (nama, json.dumps(dv), json.dumps(jb), aktif, int(i)))
        else:
            sql(c, "INSERT INTO anggota (nama,token,divisi,jabatan,aktif,dibuat) VALUES (?,?,?,?,?,?)",
                (nama, token_baru(nama), json.dumps(dv), json.dumps(jb), aktif, _now()))
    members_load()


def member_token_ulang(i):
    m = MEMBERS.get(i)
    if not m:
        raise ValueError("Anggota tidak ditemukan")
    with db() as c:
        sql(c, "UPDATE anggota SET token=? WHERE id=?", (token_baru(m["nama"]), i))
    members_load()


def kuota_pakai(r, jenis, cek_saja=False):
    """True kalau masih ada jatah hari ini (lalu dihitung). Admin tanpa batas. Jatah = per orang per hari WIB."""
    if is_admin(r):
        return True
    with _KLOCK:
        k = SET.setdefault("kuota", {})
        if k.get("tgl") != today():
            k.clear()
            k["tgl"] = today()
        n = k.get(f"{r}:{jenis}", 0)
        if n >= KUOTA[jenis]:
            return False
        if not cek_saja:
            k[f"{r}:{jenis}"] = n + 1
    if not cek_saja:
        try:
            set_save()
        except Exception:
            log.exception("simpan kuota gagal")
    return True


def kuota_info(r):
    if is_admin(r) or not member_of(r):
        return {}
    k = SET.get("kuota", {})
    tgl = k.get("tgl") == today()
    out = {"boss": {"pakai": k.get(f"{r}:boss", 0) if tgl else 0, "batas": KUOTA["boss"]}}
    if can("gambar_buat", r):
        out["gambar"] = {"pakai": k.get(f"{r}:gambar", 0) if tgl else 0, "batas": KUOTA["gambar"]}
    return out


def kuota_teks(r, jenis):
    return f"Jatah {'Boss bagi tugas' if jenis == 'boss' else 'Studio Gambar'} kamu ({nama_peran(r)}) hari ini sudah habis ({KUOTA[jenis]}/{KUOTA[jenis]}). Reset jam 00.00 WIB."


def _eq(a, b):
    return bool(b) and secrets.compare_digest(a.encode(), b.encode())


def role_of(t):
    if _eq(t, ADMIN_TOKEN):
        return "admin"
    if t:
        for m in MEMBERS.values():
            if m["aktif"] and _eq(t, m["token"]):
                return f"m:{m['id']}"
    return "viewer" if _eq(t, VIEW_TOKEN) else None


def role():
    return role_of(request.cookies.get("mci_auth", ""))


def _setcookie(resp, token):
    resp.set_cookie("mci_auth", token, max_age=30 * 86400, httponly=True, samesite="Strict", secure=bool(os.getenv("RENDER")))
    return resp


def _ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?")


def _try_login(token):
    now, ip = time.time(), _ip()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 600]
    if len(FAILS[ip]) >= 8:
        abort(429)
    if role_of(token):
        return _setcookie(make_response(redirect("/")), token)
    FAILS[ip].append(now)
    return None


LOGIN = """<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCI</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0A2A6B;font-family:system-ui,sans-serif;color:#fff}
form{width:min(360px,90vw);display:grid;gap:14px;padding:28px;border:1px solid #D4AF5A;border-radius:18px;background:rgba(255,255,255,.06)}
b{font-size:28px;letter-spacing:.14em;color:#F1D58A}input,button{font:inherit;padding:13px;border-radius:12px;border:0;min-height:44px}
button{background:#D4AF5A;color:#0A2A6B;font-weight:800;cursor:pointer}p{margin:0;color:#FFB4B4;font-size:14px}</style></head>
<body><form method="post" action="/login"><b>MCI</b><label for="t">Token akses (admin atau viewer)</label><input id="t" name="t" type="password" autocomplete="current-password" required>%s<button>Masuk</button></form></body></html>"""


@app.before_request
def guard():
    if request.method not in ("GET", "HEAD") and request.path != "/login" and request.headers.get("X-MCI") != "1":
        abort(403)


def need(r):
    cur = role()
    if not cur:
        abort(401)
    if r == "admin" and not is_admin(cur):
        abort(403)


def need_perm(p):
    cur = role()
    if not cur:
        abort(401)
    if not can(p, cur):
        abort(403)
    return cur


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        resp = _try_login(request.form.get("t", ""))
        return resp or (LOGIN % "<p>Token salah.</p>", 401)
    return LOGIN % ""


@app.route("/api/masuk", methods=["POST"])
def api_masuk():
    """Pintu rahasia admin (logo / kotak Hemat token diketuk 7x). Dibatasi 8 percobaan salah per 10 menit per IP."""
    now, ip = time.time(), _ip()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 600]
    if len(FAILS[ip]) >= 8:
        return jsonify({"error": "Terlalu banyak percobaan. Coba lagi 10 menit lagi."}), 429
    tok = str((request.get_json(silent=True) or {}).get("token", "")).strip()
    r = role_of(tok)
    if r and r != "viewer":   # admin atau token pribadi anggota
        return _setcookie(jsonify({"role": "admin" if is_admin(r) else "anggota", "nama": nama_peran(r)}), tok)
    FAILS[ip].append(now)
    return jsonify({"error": "Token salah."}), 401


@app.route("/logout")
def logout():
    """Hapus cookie admin/divisi lalu kembali sebagai viewer (link biasa = mode lihat saja)."""
    resp = make_response(redirect("/" if VIEW_TOKEN else "/login"))
    resp.delete_cookie("mci_auth", samesite="Strict", secure=bool(os.getenv("RENDER")), httponly=True)
    return resp


@app.route("/")
def index():
    k = request.args.get("k")
    if k:
        return _try_login(k) or abort(404)
    auto = not role()
    if auto and not VIEW_TOKEN:
        return redirect("/login")
    pg = page_bytes()
    if not auto and request.headers.get("If-None-Match") == pg["etag"]:
        return Response(status=304, headers={"ETag": pg["etag"]})
    gz = "gzip" in request.headers.get("Accept-Encoding", "")
    r = Response(pg["gz"] if gz else pg["raw"], mimetype="text/html")
    r.headers.update({"ETag": pg["etag"], "Cache-Control": "no-cache", "Vary": "Accept-Encoding"})
    if gz:
        r.headers["Content-Encoding"] = "gzip"
    return _setcookie(r, VIEW_TOKEN) if auto else r   # siapa pun yang membuka link otomatis jadi viewer


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/api/me")
def api_me():
    need("viewer")
    r = role()
    m = member_of(r)
    return jsonify({"role": "admin" if is_admin(r) else ("anggota" if m else r), "nama_peran": nama_peran(r), "izin": sorted(perms_of(r)), "kuota": kuota_info(r),
                    "anggota": {"id": m["id"], "nama": m["nama"], "divisi": [DIVISI[x] for x in m["divisi"]], "jabatan": [JABATAN[x] for x in m["jabatan"]],
                                "divisi_key": m["divisi"]} if m else None, "daftar_divisi": DIVISI,
                    **{k: v for k, v in SET.items() if k not in ("kuota", "tg_users", "tg_izin")}, "mcp_siap": bool(MCP_SERVERS and ClientSession), "db_permanen": PG, "lantai": LANTAI,
                    "gambar": img_status()})


@app.route("/api/office")
def api_office():
    need("viewer")
    r_ = role()
    adm = is_admin(r_)
    ck = adm
    c = _OFFICE_CACHE.get(ck)
    if c and time.time() - c[0] < 0.8:   # banyak viewer = satu hitungan per 0,8 detik
        return Response(c[1], mimetype="application/json")
    ags = [{"key": k, "name": a["nama"], "peran": a["peran"], "boss": a["boss"], "lantai": a["lantai"], "divisi": a["divisi"],
            "status": o[0], "activity": o[1], "mode": o[2], "model": model_for(k),
            "gemini": bool(gkey(k)), "telegram": bool(os.getenv(f"TOKEN_{k.upper()}")),
            **({"calls": s["calls"], "tokens": s["tokens"]} if adm else {})} for k, a in AGENTS.items() for s in [STATE[k]] for o in [office_status(s)]]
    s, o = STATE["canvas"], office_status(STATE["canvas"])
    ags.append({"key": "canvas", "name": CANVAS["nama"], "peran": CANVAS["peran"], "boss": False, "lantai": 1, "divisi": CANVAS["divisi"],
                "status": o[0], "activity": o[1], "mode": o[2], "model": "cloudflare-flux", "gemini": bool(CF_TOKEN),
                "telegram": bool(os.getenv("TOKEN_GAMBAR")), **({"calls": img_today(), "tokens": 0} if adm else {})})
    body = json.dumps({"agents": ags, "log": list(TASKLOG) if adm else [], "lantai": LANTAI, "iot": iot_public(adm), **({"galeri": GAL_IDS[:4]} if adm else {}),
                       "stok": [[x["id"], x["nama"], x["jumlah"], x["satuan"], x["minimum"], x["foto"], x["v"]] for x in STOK[:18]]})   # kardus 3D terlihat semua peran
    _OFFICE_CACHE[ck] = (time.time(), body)
    return Response(body, mimetype="application/json")


def start_job(coro):
    if not LOOP:
        coro.close()
        raise RuntimeError("Mesin AI belum siap, coba lagi sebentar.")
    jid = secrets.token_urlsafe(8)
    JOBS[jid] = {"status": "jalan"}
    while len(JOBS) > 50:
        JOBS.pop(next(iter(JOBS)))
    fut = asyncio.run_coroutine_threadsafe(coro, LOOP)

    def done(f):
        try:
            JOBS[jid] = {"status": "selesai", "hasil": f.result()}
        except Exception as e:
            log.exception("job gagal")
            JOBS[jid] = {"status": "gagal", "error": str(e) if isinstance(e, RuntimeError) else "Terjadi kesalahan, cek log server."}
    fut.add_done_callback(done)
    return jid


@app.route("/api/boss", methods=["POST"])
def api_boss():
    r = need_perm("boss")
    d = request.get_json(silent=True) or {}
    if d.get("mode") == "dewan" and not is_admin(r):
        return jsonify({"error": "Sidang Dewan khusus admin/dev."}), 403
    teks = str(d.get("perintah", "")).strip()[:3000]
    if not teks:
        return jsonify({"error": "Perintah kosong"}), 400
    simpan = str(d.get("simpan", "72"))
    simpan = simpan if simpan in SIMPAN else "72"

    async def jalan(coro):
        h = await coro
        bots, jawab = mem_from_result(h)
        h["memori"] = await asyncio.to_thread(mem_save, "web", bots, h.get("jalur", ""), teks, jawab, simpan)
        return h
    if not kuota_pakai(r, "boss"):
        return jsonify({"error": kuota_teks(r, "boss")}), 429
    try:
        if d.get("mode") == "dewan":
            ang = [str(x).lower() for x in (d.get("anggota") or [])][:COUNCIL_MAX]
            return jsonify({"job": start_job(jalan(council_run(teks, ang or None)))})
        return jsonify({"job": start_job(jalan(smart_run(teks)))})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/job/<jid>")
def api_job(jid):
    need_perm("boss")
    return jsonify(JOBS.get(jid) or {"status": "tidak ada"})


@app.route("/api/kas")
def api_kas():
    need_perm("kas")
    rows = kas_all()
    return jsonify({"rows": rows[-300:][::-1], "ringkas": kas_summary(rows), "kategori": KAT})


@app.route("/api/kas/olah", methods=["POST"])
def api_kas_olah():
    need_perm("kas_edit")
    teks = request.form.get("teks", "")[:3000]
    f, foto, mime = request.files.get("foto"), None, "image/jpeg"
    if f:
        foto, mime = f.read(4_000_001), (f.mimetype or "image/jpeg")
        if len(foto) > 4_000_000 or not mime.startswith("image/"):
            return jsonify({"error": "Foto harus gambar, maksimal 4 MB"}), 400
    if not teks.strip() and not foto:
        return jsonify({"error": "Isi catatan atau unggah foto"}), 400
    if not LOOP:
        return jsonify({"error": "Mesin AI belum siap"}), 503
    try:
        rows = asyncio.run_coroutine_threadsafe(kas_parse(teks, foto, mime), LOOP).result(75)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception:
        log.exception("olah kas gagal")
        return jsonify({"error": "AI gagal membaca catatan, coba lagi"}), 502
    return jsonify({"rows": rows})


@app.route("/api/kas/simpan", methods=["POST"])
def api_kas_simpan():
    need_perm("kas_edit")
    rows = []
    for r in ((request.get_json(silent=True) or {}).get("rows") or [])[:50]:
        try:
            m, x = int(r["masuk"]), int(r["keluar"])
            kat = r.get("kategori")
            if m < 0 or x < 0 or (m == 0 and x == 0) or max(m, x) > 10**12:
                continue
            rows.append({"tanggal": _tgl(r.get("tanggal"), datetime.now(WIB).strftime("%Y-%m-%d")),
                         "keterangan": str(r.get("keterangan", ""))[:200] or "-", "kategori": kat if kat in KAT else "Lainnya",
                         "masuk": m, "keluar": x})
        except (KeyError, TypeError, ValueError):
            continue
    return jsonify({"disimpan": len(kas_add(rows, "web")) if rows else 0})


@app.route("/api/kas/<int:i>", methods=["DELETE"])
def api_kas_del(i):
    need_perm("kas_edit")
    return jsonify({"dihapus": kas_del(i)})


@app.route("/api/kas.csv")
def api_kas_csv():
    need_perm("kas")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["tanggal", "keterangan", "kategori", "masuk", "keluar", "saldo"])
    for r in kas_all():
        ket = r["keterangan"]
        w.writerow([r["tanggal"], "'" + ket if ket[:1] in "=+-@" else ket, r["kategori"], r["masuk"], r["keluar"], r["saldo"]])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=kas-mci.csv"})


@app.route("/api/skills", methods=["GET", "POST"])
def api_skills():
    need_perm("skills")
    if request.method == "POST":
        need("admin")
        try:
            return jsonify({"disimpan": skill_save(request.get_json(silent=True) or {})})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"skills": skills_public(), "bots": [{"key": k, "nama": a["nama"]} for k, a in AGENTS.items()]})


@app.route("/api/skills/<nama>", methods=["DELETE"])
def api_skill_del(nama):
    need("admin")
    return jsonify({"dihapus": skill_del(nama)})


@app.route("/api/gambar", methods=["GET", "POST"])
def api_gambar():
    r = need_perm("gambar_lihat")
    if request.method == "GET":
        return jsonify({"items": img_list(), **img_status(), "kuota": kuota_info(r).get("gambar")})
    if not can("gambar_buat", r):
        return jsonify({"error": "Divisi kamu hanya bisa melihat galeri."}), 403
    if not kuota_pakai(r, "gambar", cek_saja=True):
        return jsonify({"error": kuota_teks(r, "gambar")}), 429
    f, foto = request.files.get("foto"), None
    if f:
        foto = f.read(4_000_001)
        if len(foto) > 4_000_000 or not (f.mimetype or "").startswith("image/"):
            return jsonify({"error": "Foto harus gambar, maksimal 4 MB"}), 400
    if not LOOP:
        return jsonify({"error": "Mesin AI belum siap"}), 503
    coro = make_image(request.form.get("prompt", ""), foto, request.form.get("kekuatan", 0.6),
                      request.form.get("w"), request.form.get("h"), "web")
    try:
        r = asyncio.run_coroutine_threadsafe(coro, LOOP).result(150)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        log.exception("buat gambar gagal")
        return jsonify({"error": "Gagal membuat gambar, coba lagi"}), 502
    kuota_pakai(role(), "gambar")   # dihitung hanya kalau gambar berhasil
    return jsonify({"kuota": kuota_info(role()).get("gambar"), "id": r["id"], "prompt": r["prompt"], "prompt_en": r["prompt_en"], "url": f"/api/gambar/{r['id']}", **img_status()})


@app.route("/api/gambar/<int:i>", methods=["GET", "DELETE"])
def api_gambar_one(i):
    need_perm("gambar_lihat")
    if request.method == "DELETE":
        need("admin")
        return jsonify({"dihapus": img_del(i)})
    g = img_get(i)
    if not g:
        abort(404)
    ext = "png" if g[0] == "image/png" else "jpg"
    h = {"Cache-Control": "private, max-age=86400"}
    if request.args.get("unduh"):
        h["Content-Disposition"] = f"attachment; filename=mci-gambar-{i}.{ext}"
    return Response(g[1], mimetype=g[0], headers=h)


@app.route("/api/memori", methods=["GET", "DELETE"])
def api_memori():
    need_perm("memori")
    if request.method == "DELETE":
        need("admin")
        return jsonify({"dihapus": mem_del()})
    return jsonify({"items": mem_list(request.args.get("q", "").strip(), request.args.get("sumber", "")), "mem_tg": SET.get("mem_tg", "tidak")})


@app.route("/api/memori/<int:i>", methods=["POST", "DELETE"])
def api_memori_one(i):
    need("admin")
    if request.method == "DELETE":
        return jsonify({"dihapus": mem_del(i)})
    return jsonify({"permanen": mem_keep(i)})


@app.route("/api/tentang", methods=["GET", "POST"])
def api_tentang():
    need("viewer")
    if request.method == "POST":
        need("admin")
        return jsonify(tentang_save(request.get_json(silent=True) or {}))
    return jsonify(tentang_get())


@app.route("/api/iot", methods=["GET", "POST"])
def api_iot():
    if request.method == "POST":   # dipanggil ESP32: header X-MCI: 1 dan X-IOT-KEY
        if not (IOT_KEY and _eq(request.headers.get("X-IOT-KEY", ""), IOT_KEY)):
            return jsonify({"error": "kunci IoT salah"}), 401
        d = request.get_json(silent=True) or {}
        pr = str(d.get("perangkat", "")).strip().lower()
        if not DEV_OK.match(pr):
            return jsonify({"error": "nama perangkat: huruf kecil, angka, minus, maks 32"}), 400
        if pr not in IOT_LAST and len(IOT_LAST) >= 12:
            return jsonify({"error": "maksimal 12 perangkat"}), 400
        now = time.time()
        if now - IOT_SEEN.get(pr, 0) < 1.0:
            return jsonify({"error": "terlalu cepat, kirim maks 1x per detik"}), 429
        IOT_SEEN[pr] = now
        data = iot_clean(d.get("data") if isinstance(d.get("data"), dict) else {})
        if not data:
            return jsonify({"error": "data kosong"}), 400
        ts = int(now)
        IOT_LAST[pr] = {"ts": ts, "data": {**IOT_LAST.get(pr, {}).get("data", {}), **data}}   # nilai terakhir tiap metrik
        IOT_HIST.setdefault(pr, deque(maxlen=240)).append((ts, data))
        try:
            iot_save(pr, ts, data)
        except Exception:
            log.exception("Gagal simpan data IoT")
        iot_rules_check(pr, data)
        return jsonify({"ok": True})
    need("viewer")
    adm = is_admin(role())
    out = {"devices": iot_public(adm, full=True), "aktif": bool(IOT_KEY)}
    if adm:
        out.update(rules=IOT_RULES, key=IOT_KEY, endpoint=request.host_url.rstrip("/") + "/api/iot", hari=IOT_DAYS)
    return jsonify(out)


@app.route("/api/iot/rules", methods=["POST"])
def api_iot_rules():
    need("admin")
    rules = []
    for r in ((request.get_json(silent=True) or {}).get("rules") or [])[:30]:
        try:
            m = str(r.get("metrik", "")).lower()
            if not KEY_OK.match(m) or r.get("op") not in ("<", ">", "="):
                continue
            pr = str(r.get("perangkat", "*")).lower() or "*"
            rules.append({"perangkat": pr if pr == "*" or DEV_OK.match(pr) else "*", "metrik": m, "op": r["op"],
                          "nilai": float(r.get("nilai", 0)), "pesan": str(r.get("pesan", ""))[:120]})
        except (TypeError, ValueError, AttributeError):
            continue
    IOT_RULES[:] = rules
    with db() as c:
        sql(c, "INSERT INTO pengaturan (k,v) VALUES (?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", ("iot_rules", json.dumps(rules)))
    return jsonify({"rules": rules})


@app.route("/api/iot/<perangkat>", methods=["DELETE"])
def api_iot_del(perangkat):
    need("admin")
    pr = perangkat.lower()
    IOT_LAST.pop(pr, None); IOT_HIST.pop(pr, None)
    with db() as c:
        n = sql(c, "DELETE FROM iot WHERE perangkat=?", (pr,)).rowcount
    return jsonify({"dihapus": n})


@app.route("/api/laporan", methods=["GET", "POST"])
def api_laporan():
    need_perm("laporan_lihat")
    if request.method == "POST":
        need("admin")
        d = request.get_json(silent=True) or {}
        return jsonify({"job": start_job(laporan_buat(kirim=bool(d.get("kirim")), sumber="web"))})
    return jsonify({"items": laporan_list(), "aktif": SET.get("laporan_on", True), "hari": HARI_ID[LAP_HARI], "jam": LAP_JAM,
                    "terakhir": SET.get("laporan_terakhir"), "penerima": len(ALLOWED), "telegram": bool(TG_APPS)})


@app.route("/api/laporan/atur", methods=["POST"])
def api_laporan_atur():
    need("admin")
    SET["laporan_on"] = bool((request.get_json(silent=True) or {}).get("aktif"))
    set_save()
    return jsonify({"aktif": SET["laporan_on"]})


@app.route("/api/laporan/<int:i>", methods=["DELETE"])
def api_laporan_del(i):
    need("admin")
    with db() as c:
        return jsonify({"dihapus": sql(c, "DELETE FROM laporan WHERE id=?", (i,)).rowcount})


@app.route("/api/usage")
def api_usage():
    need_perm("tim")
    return jsonify(usage_report())


def _member_out(m, token=False):
    tg = [k for k, v in SET.get("tg_users", {}).items() if v.get("anggota") == m["id"]]
    return {"id": m["id"], "nama": m["nama"], "divisi": m["divisi"], "jabatan": m["jabatan"], "aktif": m["aktif"], "telegram": tg,
            **({"token": m["token"]} if token else {})}


@app.route("/api/anggota", methods=["GET", "POST"])
def api_anggota():
    """Khusus admin/dev: daftar anggota + token pribadi. POST {id?, nama, divisi[], jabatan[], aktif}."""
    need("admin")
    if request.method == "POST":
        try:
            member_simpan(request.get_json(silent=True) or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"items": [_member_out(m, True) for m in MEMBERS.values()], "divisi": DIVISI, "jabatan": JABATAN})


@app.route("/api/struktur", methods=["GET", "POST"])
def api_struktur():
    """Khusus Dev: daftar divisi & jabatan beserta izinnya. POST {jenis, key?, nama, izin[]} untuk tambah/ubah."""
    need("admin")
    if request.method == "POST":
        try:
            struktur_simpan(request.get_json(silent=True) or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify(struktur_out())


@app.route("/api/struktur/<jenis>/<key>", methods=["DELETE"])
def api_struktur_del(jenis, key):
    need("admin")
    try:
        struktur_hapus(jenis, key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(struktur_out())


@app.route("/api/anggota/<int:i>/token", methods=["POST"])
def api_anggota_token(i):
    need("admin")
    try:
        member_token_ulang(i)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"items": [_member_out(m, True) for m in MEMBERS.values()]})


@app.route("/api/anggota/<int:i>", methods=["DELETE"])
def api_anggota_del(i):
    need("admin")
    with db() as c:
        sql(c, "DELETE FROM anggota WHERE id=?", (i,))
    members_load()
    for k in [k for k, v in SET.get("tg_users", {}).items() if v.get("anggota") == i]:
        SET["tg_users"].pop(k, None)
    set_save()
    return jsonify({"items": [_member_out(m, True) for m in MEMBERS.values()]})


# Catatan: pribadi (hanya pemilik) atau bersama (semua anggota). Catatan bersama boleh diubah pemilik, Sekretaris, atau Dev.
def catatan_list(r):
    me = mid_of(r)
    with db() as c:
        rows = sql(c, "SELECT id,pemilik,judul,isi,bersama,sematkan,dibuat,diubah FROM catatan WHERE bersama=1 OR pemilik=? ORDER BY sematkan DESC, id DESC LIMIT 300", (me,)).fetchall()
    nm = lambda i: "Dev" if i == 0 else (MEMBERS.get(i) or {}).get("nama", "?")
    return [{"id": int(i), "pemilik": nm(int(p)), "milik_saya": int(p) == me, "judul": j or "", "isi": s_ or "", "bersama": bool(b), "sematkan": bool(se),
             "dibuat": d, "diubah": u, "bisa_ubah": int(p) == me or (bool(b) and can("catatan_kelola", r))} for i, p, j, s_, b, se, d, u in rows]


@app.route("/api/catatan", methods=["GET", "POST"])
def api_catatan():
    r = need_perm("catatan")
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        judul, isi = str(d.get("judul", "")).strip()[:120], str(d.get("isi", "")).strip()[:5000]
        if not (judul or isi):
            return jsonify({"error": "Catatan kosong"}), 400
        bersama, sem = 1 if d.get("bersama") else 0, 1 if d.get("sematkan") and can("catatan_kelola", r) else 0
        i = str(d.get("id") or "")
        with db() as c:
            if i.isdigit():
                row = sql(c, "SELECT pemilik, bersama FROM catatan WHERE id=?", (int(i),)).fetchone()
                if not row or not (int(row[0]) == mid_of(r) or (row[1] and can("catatan_kelola", r))):
                    return jsonify({"error": "Tidak boleh mengubah catatan ini"}), 403
                sql(c, "UPDATE catatan SET judul=?, isi=?, bersama=?, sematkan=?, diubah=? WHERE id=?", (judul, isi, bersama, sem, _now(), int(i)))
            else:
                sql(c, "INSERT INTO catatan (pemilik,judul,isi,bersama,sematkan,dibuat,diubah) VALUES (?,?,?,?,?,?,?)", (mid_of(r), judul, isi, bersama, sem, _now(), _now()))
    return jsonify({"items": catatan_list(r), "kelola": can("catatan_kelola", r)})


@app.route("/api/catatan/<int:i>", methods=["DELETE"])
def api_catatan_del(i):
    r = need_perm("catatan")
    with db() as c:
        row = sql(c, "SELECT pemilik, bersama FROM catatan WHERE id=?", (i,)).fetchone()
        if not row or not (int(row[0]) == mid_of(r) or (row[1] and can("catatan_kelola", r))):
            return jsonify({"error": "Tidak boleh menghapus catatan ini"}), 403
        sql(c, "DELETE FROM catatan WHERE id=?", (i,))
    return jsonify({"items": catatan_list(r), "kelola": can("catatan_kelola", r)})


# Blacklist: khusus tim Secret Service (+ Dev)
def blacklist_list():
    with db() as c:
        rows = sql(c, "SELECT id,nama,alasan,keterangan,oleh,tanggal FROM blacklist ORDER BY id DESC").fetchall()
    return [{"id": int(i), "nama": n, "alasan": a or "", "keterangan": k or "", "oleh": o or "", "tanggal": t or ""} for i, n, a, k, o, t in rows]


@app.route("/api/blacklist", methods=["GET", "POST"])
def api_blacklist():
    r = need_perm("blacklist")
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        nama = str(d.get("nama", "")).strip()[:80]
        if not nama:
            return jsonify({"error": "Nama wajib diisi"}), 400
        with db() as c:
            sql(c, "INSERT INTO blacklist (nama,alasan,keterangan,oleh,tanggal) VALUES (?,?,?,?,?)",
                (nama, str(d.get("alasan", "")).strip()[:200], str(d.get("keterangan", "")).strip()[:500], nama_peran(r), today()))
    return jsonify({"items": blacklist_list()})


@app.route("/api/blacklist/<int:i>", methods=["DELETE"])
def api_blacklist_del(i):
    need_perm("blacklist")
    with db() as c:
        sql(c, "DELETE FROM blacklist WHERE id=?", (i,))
    return jsonify({"items": blacklist_list()})


# Polling: 1 anggota = 1 suara, anonim. Hasil terlihat setelah ditutup / lewat batas waktu. Pemenang ditetapkan Dev manual.
def _poll_boleh(r, div):
    if is_admin(r) or not div:
        return True
    m = member_of(r)
    return bool(m and div in m["divisi"])


def polling_list(r):
    me, now = mid_of(r), datetime.now(WIB).strftime("%Y-%m-%dT%H:%M")
    with db() as c:
        rows = sql(c, "SELECT id,judul,deskripsi,opsi,pembuat,pembuat_nama,divisi,status,batas,dibuat,ditutup FROM polling ORDER BY id DESC LIMIT 100").fetchall()
        votes = sql(c, "SELECT poll,pemilih,pilihan FROM suara").fetchall()
    out = []
    for i, j, ds, op, pb, pn, dv, st, bt, db_, dt_ in rows:
        if not _poll_boleh(r, dv):
            continue
        tutup = st != "buka" or bool(bt and bt <= now)
        opsi = json.loads(op or "[]")
        vs = [v for v in votes if int(v[0]) == int(i)]
        mine = next((int(v[2]) for v in vs if int(v[1]) == me), None)
        hasil = [sum(1 for v in vs if int(v[2]) == k) for k in range(len(opsi))] if tutup else None
        out.append({"id": int(i), "judul": j, "deskripsi": ds or "", "opsi": opsi, "pembuat": pn or "", "divisi": dv, "divisi_nama": DIVISI.get(dv, "Divisi dihapus") if dv else "Semua anggota",
                    "tutup": tutup, "batas": bt, "dibuat": db_, "pemilih": len(vs), "pilihan_saya": mine, "hasil": hasil,
                    "bisa_tutup": not tutup and (is_admin(r) or int(pb or -1) == me)})
    return out


@app.route("/api/polling", methods=["GET", "POST"])
def api_polling():
    r = need_perm("polling")
    if request.method == "POST":
        if not can("polling_buat", r):
            return jsonify({"error": "Membuat polling khusus Dev, Manager, dan PJ Divisi"}), 403
        d = request.get_json(silent=True) or {}
        judul = str(d.get("judul", "")).strip()[:150]
        opsi = [str(x).strip()[:80] for x in (d.get("opsi") or []) if str(x).strip()][:12]
        if not judul or len(opsi) < 2:
            return jsonify({"error": "Isi judul dan minimal 2 pilihan"}), 400
        div = d.get("divisi") or None
        if div and div not in DIVISI:
            return jsonify({"error": "Divisi tidak dikenal"}), 400
        m = member_of(r)
        if not can("polling_semua", r) and (not div or div not in m["divisi"]):
            return jsonify({"error": "PJ Divisi hanya bisa membuat polling untuk divisinya sendiri"}), 403
        batas = str(d.get("batas") or "")[:16] or None
        with db() as c:
            sql(c, "INSERT INTO polling (judul,deskripsi,opsi,pembuat,pembuat_nama,divisi,status,batas,dibuat) VALUES (?,?,?,?,?,?,?,?,?)",
                (judul, str(d.get("deskripsi", "")).strip()[:500], json.dumps(opsi), mid_of(r), nama_peran(r), div, "buka", batas, _now()))
    return jsonify({"items": polling_list(r), "bisa_buat": can("polling_buat", r)})


@app.route("/api/polling/<int:i>/pilih", methods=["POST"])
def api_polling_pilih(i):
    r = need_perm("polling")
    k = (request.get_json(silent=True) or {}).get("pilihan")
    p = next((x for x in polling_list(r) if x["id"] == i), None)
    if not p:
        return jsonify({"error": "Polling tidak ditemukan"}), 404
    if p["tutup"]:
        return jsonify({"error": "Polling sudah ditutup"}), 400
    if p["pilihan_saya"] is not None:
        return jsonify({"error": "Kamu sudah memilih. 1 orang = 1 suara."}), 400
    if not isinstance(k, int) or not 0 <= k < len(p["opsi"]):
        return jsonify({"error": "Pilihan tidak valid"}), 400
    with db() as c:
        sql(c, "INSERT INTO suara (poll,pemilih,pilihan,waktu) VALUES (?,?,?,?)", (i, mid_of(r), k, _now()))
    return jsonify({"items": polling_list(r), "bisa_buat": can("polling_buat", r)})


@app.route("/api/polling/<int:i>", methods=["POST", "DELETE"])
def api_polling_aksi(i):
    r = need_perm("polling")
    with db() as c:
        row = sql(c, "SELECT pembuat FROM polling WHERE id=?", (i,)).fetchone()
        if not row:
            return jsonify({"error": "Polling tidak ditemukan"}), 404
        if request.method == "DELETE":
            if not is_admin(r):
                return jsonify({"error": "Hapus polling khusus Dev"}), 403
            sql(c, "DELETE FROM suara WHERE poll=?", (i,))
            sql(c, "DELETE FROM polling WHERE id=?", (i,))
        else:
            if not (is_admin(r) or int(row[0] or -1) == mid_of(r)):
                return jsonify({"error": "Yang bisa menutup: pembuat polling atau Dev"}), 403
            sql(c, "UPDATE polling SET status='tutup', ditutup=? WHERE id=?", (_now(), i))
    return jsonify({"items": polling_list(r), "bisa_buat": can("polling_buat", r)})


# Dokumentasi: album foto kegiatan. Upload/hapus = divisi Dokumentasi + Dev; lihat = semua anggota.
def dok_list():
    with db() as c:
        rows = sql(c, "SELECT id,judul,tanggal,keterangan,oleh,dibuat FROM dokumentasi ORDER BY tanggal DESC, id DESC LIMIT 300").fetchall()
    return [{"id": int(i), "judul": j, "tanggal": t or "", "keterangan": k or "", "oleh": o or "", "dibuat": d} for i, j, t, k, o, d in rows]


@app.route("/api/dokumentasi", methods=["GET", "POST"])
def api_dok():
    r = need_perm("dok_lihat")
    if request.method == "POST":
        if not can("dok_kelola", r):
            return jsonify({"error": "Upload dokumentasi khusus tim Dokumentasi dan Dev"}), 403
        f = request.files.get("foto")
        if not f:
            return jsonify({"error": "Pilih foto dulu"}), 400
        foto = f.read(2_000_001)
        if len(foto) > 2_000_000 or not (f.mimetype or "").startswith("image/"):
            return jsonify({"error": "Foto harus gambar, maksimal 2 MB"}), 400
        judul = request.form.get("judul", "").strip()[:120] or "Kegiatan MCI"
        with db() as c:
            n = sql(c, "SELECT COUNT(*) FROM dokumentasi").fetchone()[0]
            if int(n) >= 300:
                return jsonify({"error": "Album penuh (300 foto). Hapus foto lama dulu."}), 400
            sql(c, "INSERT INTO dokumentasi (judul,tanggal,keterangan,mime,data,oleh,dibuat) VALUES (?,?,?,?,?,?,?)",
                (judul, _tgl(request.form.get("tanggal"), today()), request.form.get("keterangan", "").strip()[:500], f.mimetype,
                 base64.b64encode(foto).decode(), nama_peran(r), _now()))
    return jsonify({"items": dok_list(), "kelola": can("dok_kelola", r)})


@app.route("/api/dokumentasi/<int:i>", methods=["GET", "DELETE"])
def api_dok_one(i):
    r = need_perm("dok_lihat")
    with db() as c:
        if request.method == "DELETE":
            if not can("dok_kelola", r):
                return jsonify({"error": "Khusus tim Dokumentasi dan Dev"}), 403
            sql(c, "DELETE FROM dokumentasi WHERE id=?", (i,))
            return jsonify({"items": dok_list(), "kelola": True})
        row = sql(c, "SELECT mime, data FROM dokumentasi WHERE id=?", (i,)).fetchone()
    if not row:
        abort(404)
    h = {"Cache-Control": "private, max-age=86400"}
    if request.args.get("unduh"):
        h["Content-Disposition"] = f"attachment; filename=mci-dokumentasi-{i}.jpg"
    return Response(base64.b64decode(row[1]), mimetype=row[0], headers=h)


@app.route("/api/hutang", methods=["GET", "POST"])
def api_hutang():
    """Khusus Bendahara + admin. POST {nama, jumlah, catatan?, kas?} = catat pinjaman baru."""
    need_perm("hutang")
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        try:
            jt = hutang_tambah(d.get("nama"), d.get("jumlah"), d.get("catatan", ""), d.get("kas", True) is not False)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"jatuh_tempo": jt, **hutang_data()})
    return jsonify(hutang_data())


@app.route("/api/hutang/<int:i>", methods=["POST"])
def api_hutang_aksi(i):
    """{aksi: lunas | konfirmasi | hapus}. Konfirmasi tambahan = perpanjang H+3 sekali. Hapus khusus admin."""
    r = need_perm("hutang")
    d = request.get_json(silent=True) or {}
    aksi = str(d.get("aksi", ""))
    if aksi == "hapus" and not is_admin(r):
        return jsonify({"error": "Hapus data hutang khusus admin/dev"}), 403
    try:
        hutang_aksi(i, aksi, d.get("kas", True) is not False)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(hutang_data())


@app.route("/api/hutang/pulihkan", methods=["POST"])
def api_hutang_pulihkan():
    need("admin")
    try:
        n = hutang_pulihkan((request.get_json(silent=True) or {}).get("nama", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"dipulihkan": n, **hutang_data()})


@app.route("/api/stok", methods=["GET", "POST"])
def api_stok():
    """GET admin + divisi (viewer TIDAK boleh). POST khusus admin: multipart nama, jumlah, satuan, kategori, minimum, catatan, foto, id (ubah), hapus_foto."""
    need_perm("stok")
    if request.method == "GET":
        return jsonify({"items": STOK, "menipis": len(stok_menipis()), "edit": is_admin(role())})
    need("admin")
    f, foto, mime = request.files.get("foto"), None, None
    if f:
        foto, mime = f.read(1_500_001), f.mimetype or "image/jpeg"
        if len(foto) > 1_500_000 or not mime.startswith("image/"):
            return jsonify({"error": "Foto harus gambar, maksimal 1,5 MB"}), 400
    try:
        sid = stok_save(request.form, foto, mime, request.form.get("hapus_foto") == "1")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": sid, "items": STOK})


def _fotos_req(maks=4_000_000, n=STOK_FOTO_MAKS):
    fs = [f for f in request.files.getlist("foto") if f and f.filename]
    if len(fs) > n:
        raise ValueError(f"Maksimal {n} foto sekali proses")
    out = []
    for f in fs:
        foto, mime = f.read(maks + 1), f.mimetype or "image/jpeg"
        if len(foto) > maks or not mime.startswith("image/"):
            raise ValueError(f"Tiap foto harus gambar, maksimal {maks // 1_000_000} MB")
        out.append((foto, mime))
    return out


@app.route("/api/stok/olah", methods=["POST"])
def api_stok_olah():
    """Khusus Dev: 1–10 foto barang → MCI.Nexus mengusulkan nama, kategori, jumlah, satuan (belum disimpan)."""
    need("admin")
    try:
        fotos = _fotos_req()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not fotos:
        return jsonify({"error": "Unggah foto barangnya dulu"}), 400
    if not LOOP:
        return jsonify({"error": "Mesin AI belum siap"}), 503
    try:
        rows, gagal = asyncio.run_coroutine_threadsafe(stok_parse_banyak(fotos, request.form.get("petunjuk", "")[:500]), LOOP).result(180)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception:
        log.exception("olah stok gagal")
        return jsonify({"error": "AI gagal membaca foto, coba lagi atau foto lebih terang/dekat"}), 502
    return jsonify({"rows": rows, "gagal": gagal, "jumlah_foto": len(fotos)})


@app.route("/api/stok/simpan", methods=["POST"])
def api_stok_simpan():
    """Khusus Dev: simpan hasil periksa AI. multipart: rows (JSON) + foto (maks 10, urutan sama dengan saat diproses)."""
    need("admin")
    try:
        rows = json.loads(request.form.get("rows") or "[]")
        n = stok_simpan_ai(rows if isinstance(rows, list) else [], _fotos_req(1_500_000))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"disimpan": n, "items": STOK})


@app.route("/api/stok/<int:i>", methods=["POST", "DELETE"])
def api_stok_one(i):
    need("admin")
    if request.method == "DELETE":
        return jsonify({"dihapus": stok_del(i), "items": STOK})
    try:
        j = int((request.get_json(silent=True) or {}).get("jumlah"))
    except (TypeError, ValueError):
        return jsonify({"error": "jumlah harus angka"}), 400
    return jsonify({"diubah": stok_jumlah(i, j), "items": STOK})


# ---------- PERPUSTAKAAN: semua yang bisa membuka web boleh membaca DAN mengunggah; ubah/hapus milik orang lain khusus Dev ----------
PERPUS_MAKS, PERPUS_FILE_MAKS = 300, 8_000_000
PERPUS_JATAH = {"viewer_ip": 3, "viewer_semua": 30, "anggota": 10}   # unggahan per hari (WIB); Dev tanpa batas
PERPUS_BYTE_MAKS = 150_000_000                                         # total file kiriman non-Dev (karakter base64)
PERPUS_EXT = {   # ekstensi -> tipe yang DISET SERVER (tipe dari browser tidak dipercaya)
    "pdf": "application/pdf", "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip", "odt": "application/vnd.oasis.opendocument.text", "rtf": "application/rtf",
    "txt": "text/plain", "md": "text/plain", "mobi": "application/x-mobipocket-ebook", "azw3": "application/vnd.amazon.ebook"}
_PERPUS_KOLOM = ("id,judul,penulis,kategori,ringkasan,link,file_nama,file_mime,dibuat,diubah, CASE WHEN sampul IS NULL OR sampul='' THEN 0 ELSE 1 END, "
                 "LENGTH(isi), LENGTH(file_data), oleh, oleh_role")


def perpus_list(r=None):
    with db() as c:
        rows = sql(c, f"SELECT {_PERPUS_KOLOM} FROM perpus ORDER BY LOWER(kategori), LOWER(judul)").fetchall()
    return [{"id": int(i), "judul": j, "penulis": pn or "", "kategori": k or "Umum", "ringkasan": r_ or "", "link": ln or "", "file_nama": fn or "",
             "file_mime": fm or "", "dibuat": d or "", "diubah": u or "", "sampul": bool(sp), "ada_isi": bool(li), "file_kb": round((lf or 0) * 3 / 4 / 1024),
             "oleh": ol or "Dev", "milikku": bool(r and r != "viewer" and orl == r),
             "v": abs(hash(u or "")) % 100000} for i, j, pn, k, r_, ln, fn, fm, d, u, sp, li, lf, ol, orl in rows]


def _url_aman(u):
    u = str(u or "").strip()[:500]
    if u and not re.match(r"^https?://", u, re.I):
        raise ValueError("Link harus diawali http:// atau https://")
    return u


def _gambar_mime(b):
    """Tipe gambar dari isi file (bukan dari browser). SVG sengaja tidak diterima: bisa membawa script."""
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"GIF8":
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def _zip_buka(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise ValueError("Isi file tidak cocok dengan ekstensinya (bukan dokumen yang sah)")
    inf = z.infolist()
    if len(inf) > 4000 or sum(i.file_size for i in inf) > 400_000_000:
        raise ValueError("Isi file terlalu besar setelah dibuka")
    return z


def _strip_tag(x):
    return _unesc(re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", x)))


def perpus_cek_file(nama, data):
    """Periksa ekstensi + isi file. Mengembalikan (nama_aman, mime, teks_bacaan). ValueError kalau ditolak."""
    ext = nama.rsplit(".", 1)[-1].lower() if "." in nama else ""
    if ext not in PERPUS_EXT:
        raise ValueError("Format belum didukung. Boleh: PDF, Word (doc/docx), e-book (epub/mobi/azw3), ODT, RTF, TXT. Format lain: taruh di Google Drive lalu isi kolom Link.")
    if not data:
        raise ValueError("File kosong")
    teks = ""
    if ext == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise ValueError("Isi file bukan PDF yang sah")
    elif ext == "doc":
        if data[:4] != b"\xd0\xcf\x11\xe0":
            raise ValueError("Isi file bukan dokumen Word (.doc) yang sah")
    elif ext == "rtf":
        if not data.lstrip()[:5] == b"{\\rtf":
            raise ValueError("Isi file bukan RTF yang sah")
    elif ext in ("mobi", "azw3"):
        if data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
            raise ValueError("Isi file bukan e-book Kindle/Mobi yang sah")
    elif ext in ("txt", "md"):
        if b"\x00" in data[:4096]:
            raise ValueError("Isi file bukan teks biasa")
        teks = data.decode("utf-8", "replace")
    else:   # docx, epub, odt = arsip zip
        z = _zip_buka(data)
        names = set(z.namelist())
        if ext == "docx":
            if "word/document.xml" not in names:
                raise ValueError("Isi file bukan dokumen Word (.docx) yang sah")
            x = z.open("word/document.xml").read(8_000_000).decode("utf-8", "ignore")
            x = re.sub(r"<w:instrText.*?</w:instrText>", "", x, flags=re.S)
            x = re.sub(r"<w:(?:br|cr)[^>]*/>", "\n", x).replace("</w:p>", "\n").replace("<w:tab/>", "\t")
            teks = _strip_tag(x)
        elif ext == "odt":
            if "content.xml" not in names:
                raise ValueError("Isi file bukan dokumen ODT yang sah")
            x = z.open("content.xml").read(8_000_000).decode("utf-8", "ignore")
            teks = _strip_tag(re.sub(r"</text:(?:p|h)>", "\n", x))
        else:
            if "META-INF/container.xml" not in names:
                raise ValueError("Isi file bukan e-book EPUB yang sah")
            bag = []
            for n in sorted(names):
                if n.lower().endswith((".xhtml", ".html", ".htm")):
                    x = z.open(n).read(2_000_000).decode("utf-8", "ignore")
                    x = re.sub(r"<(script|style)\b.*?</\1>", "", x, flags=re.S | re.I)
                    x = re.sub(r"</(p|div|h[1-6]|li|br)\s*>", "\n", x, flags=re.I)
                    bag.append(_strip_tag(x))
                    if sum(len(b) for b in bag) > 250_000:
                        break
            teks = "\n\n".join(bag)
    teks = re.sub(r"\n{3,}", "\n\n", teks).strip()[:200_000]
    aman = re.sub(r"[^A-Za-z0-9._\- ()]", "_", nama)[:120]
    return aman, PERPUS_EXT[ext], teks


def perpus_jatah_ok(r):
    """Batas unggah harian untuk non-Dev (cegah penyalahgunaan link viewer). Mengembalikan pesan error atau None."""
    if is_admin(r):
        return None
    tgl = today()
    with db() as c:
        total = sql(c, "SELECT COUNT(*) FROM perpus").fetchone()[0]
        if total >= PERPUS_MAKS:
            return f"Perpustakaan penuh ({PERPUS_MAKS} koleksi). Minta Dev merapikan dulu."
        byte = sql(c, "SELECT COALESCE(SUM(LENGTH(file_data)),0) FROM perpus WHERE oleh_role IS NOT NULL AND oleh_role<>'admin'").fetchone()[0] or 0
        if int(byte) > PERPUS_BYTE_MAKS:
            return "Kapasitas unggahan non-Dev sudah penuh. Hubungi Dev."
        if r == "viewer":
            n_ip = sql(c, "SELECT COUNT(*) FROM perpus WHERE oleh_role='viewer' AND ip=? AND dibuat>=?", (_ip(), tgl)).fetchone()[0]
            n_all = sql(c, "SELECT COUNT(*) FROM perpus WHERE oleh_role='viewer' AND dibuat>=?", (tgl,)).fetchone()[0]
            if n_ip >= PERPUS_JATAH["viewer_ip"] or n_all >= PERPUS_JATAH["viewer_semua"]:
                return "Jatah unggah viewer hari ini sudah habis. Coba lagi besok (reset 00.00 WIB)."
        else:
            n = sql(c, "SELECT COUNT(*) FROM perpus WHERE oleh_role=? AND dibuat>=?", (r, tgl)).fetchone()[0]
            if n >= PERPUS_JATAH["anggota"]:
                return f"Jatah unggah kamu hari ini sudah habis ({PERPUS_JATAH['anggota']}/hari)."
    return None


def perpus_simpan(d, file=None, sampul=None, r="admin", teks_auto=""):
    admin = is_admin(r)
    judul = re.sub(r"\s+", " ", str(d.get("judul", ""))).strip()[:150]
    if not judul:
        raise ValueError("Judul wajib diisi")
    isi = str(d.get("isi") or "").strip()[:200_000 if admin else 60_000] or teks_auto
    vals = (judul, str(d.get("penulis") or "").strip()[:100], str(d.get("kategori") or "Umum").strip()[:40] or "Umum",
            str(d.get("ringkasan") or "").strip()[:1000], isi, _url_aman(d.get("link")), _now())
    i = str(d.get("id") or "")
    with db() as c:
        if i.isdigit():
            if not admin:
                raise PermissionError
            n = sql(c, "UPDATE perpus SET judul=?,penulis=?,kategori=?,ringkasan=?,isi=?,link=?,diubah=? WHERE id=?", (*vals, int(i))).rowcount
            if not n:
                raise ValueError("Buku tidak ditemukan")
            pid = int(i)
        else:
            if not (file or vals[5] or isi):
                raise ValueError("Isi salah satu: file, link, atau teks bacaan")
            if sql(c, "SELECT COUNT(*) FROM perpus").fetchone()[0] >= PERPUS_MAKS:
                raise ValueError(f"Maksimal {PERPUS_MAKS} koleksi")
            oleh = "Dev" if r == "admin" else nama_peran(r)
            sql(c, "INSERT INTO perpus (judul,penulis,kategori,ringkasan,isi,link,diubah,dibuat,oleh,oleh_role,ip) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (*vals, _now(), oleh, r, _ip()))
            pid = int(sql(c, "SELECT MAX(id) FROM perpus").fetchone()[0])
        if file:
            sql(c, "UPDATE perpus SET file_nama=?, file_mime=?, file_data=? WHERE id=?", (file[0], file[1], base64.b64encode(file[2]).decode(), pid))
        elif d.get("hapus_file") == "1" and admin:
            sql(c, "UPDATE perpus SET file_nama=NULL, file_mime=NULL, file_data=NULL WHERE id=?", (pid,))
        if sampul:
            sql(c, "UPDATE perpus SET sampul_mime=?, sampul=? WHERE id=?", (sampul[0], base64.b64encode(sampul[1]).decode(), pid))
        elif d.get("hapus_sampul") == "1" and admin:
            sql(c, "UPDATE perpus SET sampul_mime=NULL, sampul=NULL WHERE id=?", (pid,))
    return pid


@app.route("/api/perpus", methods=["GET", "POST"])
def api_perpus():
    """GET: semua koleksi (anggota & viewer). POST multipart: tambah baru = semua yang sudah masuk (ada jatah harian); ubah (id) = Dev."""
    r = role()
    if not r:
        abort(401)
    if request.method == "POST":
        admin = is_admin(r)
        if str(request.form.get("id") or "").isdigit() and not admin:
            abort(403)
        if not str(request.form.get("id") or "").isdigit():
            msg = perpus_jatah_ok(r)
            if msg:
                return jsonify({"error": msg}), 429
        file = sampul = None
        teks = ""
        f = request.files.get("file")
        if f and f.filename:
            data = f.read(PERPUS_FILE_MAKS + 1)
            if len(data) > PERPUS_FILE_MAKS:
                return jsonify({"error": "File maksimal 8 MB. Untuk file besar, taruh di Google Drive lalu isi kolom Link."}), 400
            try:
                nama, mime, teks = perpus_cek_file(f.filename, data)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            file = (nama, mime, data)
        sp = request.files.get("sampul")
        if sp and sp.filename:
            data = sp.read(1_500_001)
            mime = _gambar_mime(data)
            if len(data) > 1_500_000 or not mime:
                return jsonify({"error": "Sampul harus gambar JPG, PNG, WEBP, atau GIF, maksimal 1,5 MB"}), 400
            sampul = (mime, data)
        try:
            perpus_simpan(request.form, file, sampul, r, teks)
        except PermissionError:
            abort(403)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"items": perpus_list(r), "edit": is_admin(r)})


@app.route("/api/perpus/<int:i>", methods=["GET", "DELETE"])
def api_perpus_one(i):
    """GET: isi bacaan lengkap. DELETE: Dev (semua) atau anggota untuk unggahannya sendiri."""
    if request.method == "DELETE":
        r = role()
        if not r:
            abort(401)
        with db() as c:
            row = sql(c, "SELECT oleh_role FROM perpus WHERE id=?", (i,)).fetchone()
            if not row:
                abort(404)
            if not is_admin(r) and not (r != "viewer" and row[0] == r):
                abort(403)
            sql(c, "DELETE FROM perpus WHERE id=?", (i,))
        return jsonify({"items": perpus_list(r), "edit": is_admin(r)})
    need("viewer")
    with db() as c:
        row = sql(c, "SELECT judul, isi FROM perpus WHERE id=?", (i,)).fetchone()
    if not row:
        abort(404)
    return jsonify({"id": i, "judul": row[0], "isi": row[1] or ""})


@app.route("/api/perpus/<int:i>/file")
def api_perpus_file(i):
    need("viewer")
    with db() as c:
        row = sql(c, "SELECT file_nama, file_mime, file_data FROM perpus WHERE id=?", (i,)).fetchone()
    if not row or not row[2]:
        abort(404)
    mime = row[1] if row[1] in PERPUS_EXT.values() else "application/octet-stream"
    aman = mime in ("application/pdf", "text/plain")   # selain PDF dan teks polos selalu diunduh
    inline = request.args.get("unduh") != "1" and aman
    nama = re.sub(r"[^A-Za-z0-9._\- ()]", "_", row[0] or f"perpus-{i}")
    h = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{nama}"', "X-Content-Type-Options": "nosniff",
         "Cache-Control": "private, max-age=3600"}
    if mime != "application/pdf":
        h["Content-Security-Policy"] = "sandbox"   # PDF viewer Chrome tidak jalan di dalam sandbox
    return Response(base64.b64decode(row[2]), mimetype=mime, headers=h)


@app.route("/api/perpus/<int:i>/sampul")
def api_perpus_sampul(i):
    need("viewer")
    with db() as c:
        row = sql(c, "SELECT sampul_mime, sampul FROM perpus WHERE id=?", (i,)).fetchone()
    if not row or not row[1]:
        abort(404)
    mime = row[0] if row[0] in ("image/jpeg", "image/png", "image/gif", "image/webp") else "image/jpeg"
    return Response(base64.b64decode(row[1]), mimetype=mime, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
                                                                        "Content-Security-Policy": "sandbox"})


@app.route("/api/stok/<int:i>/gambar")
def api_stok_img(i):
    need("viewer")   # foto ditempel di kardus 3D, viewer juga melihatnya (halaman Stok tetap tertutup)
    g = stok_img(i)
    if not g:
        abort(404)
    return Response(g[1], mimetype=g[0], headers={"Cache-Control": "private, max-age=86400"})


@app.route("/api/tg", methods=["GET", "POST"])
def api_tg():
    """Daftar pengguna Telegram (!izin) + yang menunggu. POST {id, peran: m<idAnggota> | cabut | tolak}."""
    need("admin")
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        uid, peran = str(d.get("id", "")), str(d.get("peran", ""))
        if not uid.isdigit() or not (peran in ("cabut", "tolak") or (peran[:1] == "m" and peran[1:].isdigit() and int(peran[1:]) in MEMBERS)):
            return jsonify({"error": "data tidak valid"}), 400
        izin_putus(uid, peran)
    users = {k: {**v, "nama_anggota": (MEMBERS.get(v.get("anggota")) or {}).get("nama", "?")} for k, v in SET.get("tg_users", {}).items()}
    return jsonify({"users": users, "menunggu": SET.get("tg_izin", {}), "email": bool(MAIL_WEBHOOK), "admin": len(ALLOWED),
                    "anggota": [{"id": m["id"], "nama": m["nama"]} for m in MEMBERS.values() if m["aktif"]]})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    need("admin")
    d = request.get_json(silent=True) or {}
    for k in ("caveman", "mcp"):
        if k in d:
            SET[k] = bool(d[k])
    if str(d.get("mem_tg", "")) in SIMPAN:
        SET["mem_tg"] = str(d["mem_tg"])
    set_save()
    return jsonify(SET)


def run_flask():
    try:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), use_reloader=False, threaded=True)
    except BaseException:
        log.exception("Server web mati. Proses dihentikan supaya Render me-restart otomatis.")
    finally:
        os._exit(1)  # jangan biarkan bot hidup tanpa web


# ---------- TELEGRAM ----------
HIST = {}
CANVAS_APP = {}


def tg_role(u):
    """admin = ID di ALLOWED_USER_IDS; divisi = disetujui lewat !izin; None = belum punya akses."""
    if u is None:
        return None
    if not ALLOWED or u.id in ALLOWED:
        return "admin"
    x = SET.get("tg_users", {}).get(str(u.id))
    r = f"m:{x['anggota']}" if x and x.get("anggota") in MEMBERS else None
    if r and is_admin(r):
        return "admin"
    return r if member_of(r) else None


def _ok(u):
    return tg_role(u) is not None


def _adm(u):
    return bool(ALLOWED) and tg_role(u) == "admin"


async def _gate(m, u, perm, kuota=None):
    """Cek hak + kuota sebelum perintah Telegram dijalankan. Balas alasannya kalau ditolak."""
    r = tg_role(u)
    if r is None:
        await m.reply_text(f"Akses ditolak. Kirim !izin untuk minta akses ke admin.\nID kamu: {u.id}")
        return None
    if not can(perm, r):
        await m.reply_text(f"Fitur ini tidak tersedia untuk jabatan/divisi kamu ({nama_peran(r)}).")
        return None
    if kuota and not await asyncio.to_thread(kuota_pakai, r, kuota):
        await m.reply_text(kuota_teks(r, kuota))
        return None
    return r


async def kirim_email(subjek, isi):
    """Lewat Google Apps Script (HTTP). Render gratis memblokir SMTP 25/465/587, jadi tidak bisa kirim Gmail langsung."""
    if not MAIL_WEBHOOK:
        log.info("MAIL_WEBHOOK kosong: email izin tidak dikirim (%s)", subjek)
        return False
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as h:
            r = await h.post(MAIL_WEBHOOK, json={"secret": MAIL_SECRET, "to": MAIL_TO, "subject": subjek, "body": isi})
        ok = r.status_code < 400 and "ok" in r.text.lower()
        if not ok:
            log.warning("Email izin gagal: %s %s", r.status_code, r.text[:200])
        return ok
    except Exception:
        log.exception("Email izin gagal")
        return False


def _tg_app(key):
    return TG_APPS.get(key) or CANVAS_APP.get("app")


def _izin_tombol(uid):
    """Admin memilih orang ini siapa di daftar anggota; jabatan & divisinya ikut dari data anggota."""
    btn = [InlineKeyboardButton(f"👤 {m['nama']}", callback_data=f"izin:{uid}:m{m['id']}") for m in MEMBERS.values() if m["aktif"]][:24]
    rows = [btn[i:i + 3] for i in range(0, len(btn), 3)] + [[InlineKeyboardButton("❌ Tolak", callback_data=f"izin:{uid}:tolak")]]
    return InlineKeyboardMarkup(rows)


async def izin_minta(m, c, key):
    u = m.from_user
    r = tg_role(u)
    if r:
        await m.reply_text(f"Kamu sudah punya akses sebagai {nama_peran(r)}. 👍")
        return
    if not ALLOWED:
        await m.reply_text("Admin belum mengisi ALLOWED_USER_IDS, jadi izin belum bisa diproses.")
        return
    tunggu = SET.setdefault("tg_izin", {})
    lama = tunggu.get(str(u.id))
    if lama and time.time() - lama.get("ts", 0) < 600:
        await m.reply_text("Permintaan izinmu sudah dikirim. Tunggu admin menyetujui ya.")
        return
    nama = " ".join(x for x in (u.first_name, u.last_name) if x) or "-"
    tunggu[str(u.id)] = {"nama": nama[:60], "user": u.username or "", "bot": key, "ts": int(time.time()),
                         "waktu": datetime.now(WIB).strftime("%Y-%m-%d %H:%M")}
    await asyncio.to_thread(set_save)
    await m.reply_text("📨 Permintaan izin terkirim ke admin MCI. Kamu akan dapat pesan di sini begitu disetujui.")
    info = (f"Permintaan izin Telegram MCI HQ\n\nNama: {nama}\nUsername: @{u.username or '-'}\nID Telegram: {u.id}\n"
            f"Lewat bot: {AGENTS[key]['nama'] if key in AGENTS else CANVAS['nama']}\nWaktu: {tunggu[str(u.id)]['waktu']} WIB")
    app_ = _tg_app("apex") or _tg_app(key)
    for aid in ALLOWED:
        try:
            await app_.bot.send_message(aid, "🔔 " + info + "\n\nPilih orang ini siapa:", reply_markup=_izin_tombol(u.id))
        except Exception:
            log.warning("Notif izin ke admin %s gagal (admin belum /start bot Apex?)", aid)
    asyncio.create_task(kirim_email(f"[MCI HQ] Permintaan izin Telegram: {nama}",
                                    info + "\n\nSetujui lewat tombol di chat bot MCI.Apex (admin), atau di web → Tim & Token."))


def izin_putus(uid, peran):
    """Simpan keputusan admin. Return teks status. Pesan ke pemohon dikirim terpisah (async)."""
    tunggu, users = SET.setdefault("tg_izin", {}), SET.setdefault("tg_users", {})
    data = tunggu.pop(uid, None) or users.get(uid) or {}
    if peran[:1] == "m" and peran[1:].isdigit():
        users[uid] = {"anggota": int(peran[1:]), "nama": data.get("nama", "-"), "user": data.get("user", ""), "bot": data.get("bot", "apex"),
                      "sejak": datetime.now(WIB).strftime("%Y-%m-%d %H:%M")}
    elif peran == "cabut":
        users.pop(uid, None)
    set_save()
    if LOOP:
        asyncio.run_coroutine_threadsafe(_izin_kabari(uid, peran, data.get("bot", "apex")), LOOP)
    return data


async def _izin_kabari(uid, peran, bot):
    app_ = _tg_app(bot) or _tg_app("apex")
    if not app_:
        return
    if peran[:1] == "m" and peran[1:].isdigit() and int(peran[1:]) in MEMBERS:
        mm, r = MEMBERS[int(peran[1:])], f"m:{peran[1:]}"
        batas = "Tanpa batas (Dev)" if is_admin(r) else f"Boss bagi tugas maks {KUOTA['boss']}x/hari" + (f", Studio Gambar maks {KUOTA['gambar']}x/hari" if can("gambar_buat", r) else "")
        t = f"✅ Izin diterima! Kamu terdaftar sebagai {mm['nama']} ({jabatan_teks(mm)}).\n{batas}.\nKetik /start untuk daftar perintah."
    elif peran == "cabut":
        t = "Akses Telegram MCI kamu sudah dicabut admin."
    else:
        t = "Maaf, permintaan izin kamu ditolak admin."
    try:
        await app_.bot.send_message(int(uid), t)
    except Exception:
        log.warning("Gagal mengabari %s soal izin", uid)


async def izin_klik(u, c):
    q = u.callback_query
    if not _adm(q.from_user):
        await q.answer("Khusus admin.", show_alert=True)
        return
    try:
        _, uid, peran = q.data.split(":")
    except ValueError:
        return await q.answer()
    data = await asyncio.to_thread(izin_putus, uid, peran)
    mm = MEMBERS.get(int(peran[1:])) if peran[:1] == "m" and peran[1:].isdigit() else None
    hasil = f"✅ Disetujui sebagai {mm['nama']} ({jabatan_teks(mm)})" if mm else "❌ Ditolak"
    await q.answer(hasil)
    try:
        await q.edit_message_text(f"{q.message.text.split(chr(10) + chr(10) + 'Pilih')[0]}\n\n{hasil} oleh {q.from_user.first_name}.")
    except Exception:
        pass


def _addressed(u, c):
    m = u.message
    if m.chat.type == "private":
        return True
    un = (c.bot.username or "").lower()
    if un and f"@{un}" in (m.text or "").lower():
        return True
    r = m.reply_to_message
    return bool(r and r.from_user and r.from_user.id == c.bot.id)


async def _send(m, text):
    text = text or "(kosong)"
    for i in range(0, len(text), 4000):
        await m.reply_text(text[i:i + 4000])


async def ledger_msg(text, foto=None):
    rows = await kas_parse(text, foto)
    if not rows:
        return await ask("ledger", await asyncio.to_thread(kas_context) + text)
    ids = await asyncio.to_thread(kas_add, rows, "telegram")
    b = "\n".join(f"#{i} {r['tanggal']} {r['keterangan']} [{r['kategori']}] " + (f"+{rp(r['masuk'])}" if r["masuk"] else f"-{rp(r['keluar'])}")
                  for i, r in zip(ids, rows))
    return f"✅ Tersimpan {len(rows)} catatan:\n{b}\n\nSalah? kirim /hapus <nomor>"


def fmt_result(h):
    if h["jalur"] == "dewan":
        p = "\n\n".join(f"🗣 {x['nama']}\n{x['isi']}" for x in h["pendapat"])
        return f"🏛 {h['keterangan']}\n\n{p}\n\n⚖️ PUTUSAN DEWAN\n{h['jawaban']}"
    return f"➡️ {h['keterangan']}\n\n{h['jawaban']}"


def budget_text():
    u = usage_report()
    lines = [f"📊 Pemakaian token {u['tanggal']}: {u['total']:,}".replace(",", ".") + (f" / {u['batas']:,}".replace(",", ".") if u["batas"] else " (tanpa batas)")]
    for m, v in u["model"].items():
        lines.append(f"• {m}: {v['panggilan']} panggilan, masuk {v['masuk']}, keluar {v['keluar']}, thinking {v['pikir']}")
    lines.append(f"Hemat: {u['router_hemat']} pesan tanpa Boss, {u['cache_hit']} dari cache. Boss dipanggil {u['boss_dipanggil']}x.")
    return "\n".join(lines)


def _tg_photo_pick(photos):
    """Ambil ukuran foto Telegram terbesar yang masih <= 1024 px (img2img cepat dan stabil)."""
    ok = [p for p in photos if max(p.width, p.height) <= 1024]
    return (ok or list(photos[:1]))[-1]


GAMBAR_HELP = ("Pakai: /gambar <deskripsi>\nContoh: /gambar robot oranye minum kopi di kantor futuristik, gaya 3D\n"
               "Edit foto: kirim foto dengan caption /gambar <perubahan>, contoh: /gambar jadikan gaya kartun anime")


async def tg_gambar(m, c, prompt, foto_msg=None):
    await c.bot.send_chat_action(chat_id=m.chat_id, action="upload_photo")
    try:
        foto = w = h = None
        if foto_msg is not None:
            ps = _tg_photo_pick(foto_msg.photo)
            f = await ps.get_file()
            foto, w, h = bytes(await f.download_as_bytearray()), ps.width, ps.height
        r = await make_image(prompt, foto, 0.6, w, h, "telegram")
        await m.reply_photo(photo=r["data"], caption=("🎨 " + r["prompt"])[:1000])
    except RuntimeError as e:
        await m.reply_text(str(e))
    except Exception:
        log.exception("gambar telegram gagal")
        await m.reply_text("⚠️ Gagal membuat gambar, coba lagi sebentar.")


def make_handlers(key):
    cfg = AGENTS[key]

    async def start(u, c):
        r = tg_role(u.effective_user)
        head = f"Halo, saya {cfg['nama']} (Lantai {cfg['lantai']}, {cfg['divisi']}).\n{cfg['peran']}.\n\n"
        if r is None:
            await u.message.reply_text(head + "Kamu belum punya akses. Kirim !izin untuk minta izin ke admin MCI.")
            return
        if r == "admin":
            menu = "/status  /id  /budget\n/caveman on|off\n/council <pertanyaan>\n/skills  /skill <nama> <teks>\n/gambar <deskripsi>\n/stok (sisa stok barang)\n/laporan (laporan mingguan, ke Apex)"
        else:
            k = kuota_info(r)
            menu = (f"Kamu: {nama_peran(r)} ({jabatan_teks(member_of(r))})\n/status  /id  /budget  /stok  /skills  /skill <nama> <teks>\n"
                    + ("/gambar <deskripsi>\n" if can("gambar_buat", r) else "") + ("Kirim catatan kas ke Ledger\n" if can("kas_edit", r) else "")
                    + f"Jatah hari ini: Boss {k['boss']['pakai']}/{k['boss']['batas']}" + (f", gambar {k['gambar']['pakai']}/{k['gambar']['batas']}" if "gambar" in k else ""))
        await u.message.reply_text(head + menu)

    async def myid(u, c):
        await u.message.reply_text(f"ID Telegram kamu: {u.effective_user.id}")

    async def status(u, c):
        if not _ok(u.effective_user):
            return
        s, up = STATE[key], int(time.time() - START)
        await u.message.reply_text(
            f"📊 {cfg['nama']} · Lantai {cfg['lantai']} · {cfg['divisi']}\n• Status: {s['status']} ({s['activity']})\n• Panggilan API: {s['calls']}\n• Token terpakai: {s['tokens']}\n"
            f"• Model: {model_for(key)}\n• Caveman: {'ON' if SET['caveman'] else 'OFF'}\n⚙️ CPU {psutil.cpu_percent()}% • RAM {psutil.virtual_memory().percent}% • Uptime {up // 3600}j {(up % 3600) // 60}m")

    async def budget(u, c):
        if _ok(u.effective_user):
            await u.message.reply_text(budget_text())

    async def caveman(u, c):
        if not ALLOWED or not _ok(u.effective_user):
            await u.message.reply_text("Isi ALLOWED_USER_IDS dulu untuk memakai perintah ini.")
            return
        a = (c.args[0].lower() if c.args else "")
        if a in ("on", "off") and _adm(u.effective_user):
            SET["caveman"] = a == "on"
            await asyncio.to_thread(set_save)
        await u.message.reply_text(f"Mode caveman: {'ON' if SET['caveman'] else 'OFF'} (berlaku untuk semua bot)")

    async def hapus(u, c):
        if not (ALLOWED and c.args and c.args[0].isdigit()) or not await _gate(u.message, u.effective_user, "kas_edit"):
            return
        n = await asyncio.to_thread(kas_del, int(c.args[0]))
        await u.message.reply_text("Dihapus." if n else "Nomor tidak ditemukan.")

    async def council(u, c):
        if not _ok(u.effective_user):
            return
        if not _adm(u.effective_user):
            await u.message.reply_text("Sidang Dewan khusus admin/dev.")
            return
        q = " ".join(c.args or []).strip()
        if not q:
            await u.message.reply_text("Pakai: /council <pertanyaan>\nContoh: /council pakai baterai LiPo atau 18650 untuk kapal monitoring?")
            return
        await c.bot.send_chat_action(chat_id=u.message.chat_id, action="typing")
        try:
            h = await council_run(q[:2000])
            await _send(u.message, fmt_result(h))
            if SET.get("mem_tg", "tidak") != "tidak":
                bots, jawab = mem_from_result(h)
                await asyncio.to_thread(mem_save, "telegram", bots, "dewan", q[:2000], jawab, SET["mem_tg"])
        except Exception as e:
            log.exception("council gagal")
            await u.message.reply_text(str(e) if isinstance(e, RuntimeError) else "⚠️ Sidang dewan gagal.")

    async def skills(u, c):
        if not _ok(u.effective_user):
            return
        t = "\n".join(f"• {s['nama']} ({AGENTS[s['bot']]['nama']}): {s['deskripsi']}" for s in SKILLS.values()) or "Belum ada skill."
        await u.message.reply_text("🧰 Skill MCI\n" + t + "\n\nPakai: /skill <nama> <teks>")

    async def skill(u, c):
        if not _ok(u.effective_user):
            return
        if not c.args or c.args[0].lower() not in SKILLS:
            await u.message.reply_text("Skill tidak ditemukan. Lihat /skills")
            return
        if not await _gate(u.message, u.effective_user, "boss", "boss"):
            return
        s = SKILLS[c.args[0].lower()]
        teks = " ".join(c.args[1:]).strip() or s["deskripsi"]
        await c.bot.send_chat_action(chat_id=u.message.chat_id, action="typing")
        r = await run_task(s["bot"], teks, s["nama"], "skill")
        await _send(u.message, f"🧰 {s['nama']} oleh {AGENTS[s['bot']]['nama']}\n\n{r['hasil']}")

    async def chat(u, c):
        m = u.message
        if not m or not m.text or not _addressed(u, c):
            return
        text = m.text.replace(f"@{c.bot.username}", "").strip() if c.bot.username else m.text
        if text.lower().startswith("!izin"):
            await izin_minta(m, c, key)
            return
        if not _ok(u.effective_user):
            await m.reply_text(f"Akses ditolak. Kirim !izin untuk minta akses ke admin.\nID kamu: {u.effective_user.id}")
            return
        if key == "ledger" and (m.chat.type != "private" or not ALLOWED):
            await m.reply_text("Ledger hanya di chat pribadi dan butuh ALLOWED_USER_IDS terisi.")
            return
        if not text:
            return
        if key == "ledger" and not can("kas_edit", tg_role(u.effective_user)):
            await m.reply_text("Catat kas khusus Bendahara dan admin. Buku kas bisa dilihat di web → Keuangan AI.")
            return
        if key != "ledger" and not await _gate(m, u.effective_user, "boss", "boss"):   # catat kas tidak memakai jatah Boss
            return
        t0 = time.time()
        room = None if key in ("apex", "ledger") else room_for(text, key)
        _begin(key, "Membaca pesan", room)  # bot langsung lari ke mejanya / ruang kerja begitu pesan masuk
        try:
            await c.bot.send_chat_action(chat_id=m.chat_id, action="typing")
            if key == "apex":
                ans = fmt_result(await smart_run(text))
            elif key == "ledger":
                ans = await ledger_msg(text)
            else:
                room, rctx, note, ract = await room_prep(text, key)
                act = ract or "Memproses permintaan"
                sk = skill_match(text)
                if sk and sk["bot"] == key:
                    ans = await ask(key, rctx + text, skill=sk["nama"], act=act, room=room)
                else:
                    h = HIST.setdefault((key, m.chat_id), [])
                    ans = await ask(key, rctx + text, history=h, act=act, room=room)
                    h += [types.Content(role="user", parts=[types.Part(text=text)]), types.Content(role="model", parts=[types.Part(text=ans)])]
                    del h[:-6]
                if note and ans:
                    ans += f"\n\n— {note}"
            ok = True
        except Exception as e:
            log.exception("[%s] gagal", cfg["nama"])
            ans = str(e) if isinstance(e, RuntimeError) else "⚠️ Ada gangguan saat menghubungi Gemini. Coba lagi sebentar."
            ok = False
        finally:
            _end(key)
        if key != "apex":   # Apex sudah tercatat lewat run_task / sidang
            await asyncio.to_thread(_log, key, text, "Selesai" if ok else "Gagal", "telegram")
        await asyncio.sleep(max(0.0, 1.5 - (time.time() - t0)))  # beri waktu animasi lari terlihat dulu
        await _send(m, ans)
        if SET.get("mem_tg", "tidak") != "tidak":
            await asyncio.to_thread(mem_save, "telegram", key, "chat", text, ans, SET["mem_tg"])

    async def gambar(u, c):
        if not _ok(u.effective_user):
            return
        q = " ".join(c.args or []).strip()
        if not q:
            await u.message.reply_text(GAMBAR_HELP)
            return
        if await _gate(u.message, u.effective_user, "gambar_buat", "gambar"):
            await tg_gambar(u.message, c, q)

    async def photo(u, c):
        m = u.message
        cap = (m.caption or "").strip()
        if re.match(r"/gambar(@\w+)?(\s|$)", cap, re.I):  # foto + caption /gambar = edit foto, semua bot
            if not _ok(u.effective_user):
                return
            if m.chat.type != "private" and key != "apex" and "@" not in cap.split()[0]:
                return
            q = re.sub(r"^/gambar(@\w+)?", "", cap, flags=re.I).strip()
            if not q:
                await m.reply_text(GAMBAR_HELP)
                return
            if await _gate(m, u.effective_user, "gambar_buat", "gambar"):
                await tg_gambar(m, c, q, m)
            return
        if key != "ledger" or m.chat.type != "private" or not ALLOWED or not can("kas_edit", tg_role(u.effective_user)):
            return
        try:
            f = await m.photo[-1].get_file()
            ans = await ledger_msg(m.caption or "", bytes(await f.download_as_bytearray()))
        except Exception:
            log.exception("foto ledger gagal")
            ans = "⚠️ Gagal membaca foto."
        await _send(m, ans)

    async def stok(u, c):
        if not _ok(u.effective_user):
            return
        await asyncio.to_thread(stok_load)
        low = stok_menipis()
        await _send(u.message, f"📦 STOK BARANG MCI ({len(STOK)} jenis, dipegang MCI.Nexus)\n{stok_teks(60)}"
                    + (f"\n\n⚠️ Perlu restock: {', '.join(x['nama'] for x in low)}" if low else "") + "\n\nUbah stok: web → Stok Barang (khusus dev).")

    async def laporan(u, c):
        if not _adm(u.effective_user):
            await u.message.reply_text("Laporan mingguan khusus admin/dev." if _ok(u.effective_user) else "Isi ALLOWED_USER_IDS dulu untuk memakai perintah ini.")
            return
        if key != "apex":
            await u.message.reply_text("Laporan mingguan dibuat oleh MCI.Apex. Kirim /laporan ke bot Apex ya.")
            return
        await u.message.reply_text("📋 Scribe sedang menyusun laporan 7 hari terakhir di Perpustakaan, tunggu sebentar…")
        await c.bot.send_chat_action(chat_id=u.message.chat_id, action="typing")
        try:
            r = await laporan_buat(kirim=False, sumber="telegram")
            await _send(u.message, r["isi"])
        except Exception as e:
            log.exception("laporan gagal")
            await u.message.reply_text(str(e) if isinstance(e, RuntimeError) else "⚠️ Laporan gagal dibuat, cek log server.")

    return {"start": start, "id": myid, "status": status, "tokens": status, "budget": budget, "caveman": caveman,
            "hapus": hapus, "council": council, "dewan": council, "skills": skills, "skill": skill, "gambar": gambar, "laporan": laporan, "stok": stok}, chat, photo


def _group_cmd(key, f):
    """Di grup, perintah tanpa @namabot hanya dijawab Apex. Tanpa ini, /council di grup
    dijalankan 10 bot sekaligus (10 sidang, token terbuang 10x)."""
    async def w(u, c):
        m = u.message
        if m and m.chat.type != "private" and key != "apex":
            first = (m.text or m.caption or "").split(maxsplit=1)[0] if (m.text or m.caption) else ""
            if "@" not in first:
                return
        return await f(u, c)
    return w


def pixel_handlers():
    """Bot Telegram terpisah khusus gambar (TOKEN_GAMBAR): setiap teks = prompt, foto + caption = edit."""
    async def start(u, c):
        await u.message.reply_text("Halo, saya MCI.Canvas, pelukis di Studio Gambar lantai 1.\nKirim deskripsi gambar, langsung saya buatkan.\n"
                                   "Kirim foto + caption untuk mengedit foto.\n/sisa = jatah hari ini, /id = ID Telegram kamu")

    async def myid(u, c):
        await u.message.reply_text(f"ID Telegram kamu: {u.effective_user.id}")

    async def sisa(u, c):
        if _ok(u.effective_user):
            s = img_status()
            await u.message.reply_text(f"Gambar hari ini: {s['hari_ini']} dari {s['batas'] or 'tanpa batas'}\n"
                                       f"Mesin: {'aktif' if s['aktif'] else 'BELUM aktif (isi CF_ACCOUNT_ID dan CF_API_TOKEN)'}")

    async def gambar(u, c):
        if not _ok(u.effective_user):
            return
        q = " ".join(c.args or []).strip()
        if not q:
            await u.message.reply_text(GAMBAR_HELP)
            return
        if await _gate(u.message, u.effective_user, "gambar_buat", "gambar"):
            await tg_gambar(u.message, c, q)

    async def teks(u, c):
        m = u.message
        if not m or not m.text or not _addressed(u, c):
            return
        q = m.text.replace(f"@{c.bot.username}", "").strip() if c.bot.username else m.text
        if q.lower().startswith("!izin"):
            await izin_minta(m, c, "canvas")
            return
        if not _ok(u.effective_user):
            await m.reply_text(f"Akses ditolak. Kirim !izin untuk minta akses ke admin.\nID kamu: {u.effective_user.id}")
            return
        if q and await _gate(m, u.effective_user, "gambar_buat", "gambar"):
            await tg_gambar(m, c, q)

    async def foto(u, c):
        m = u.message
        if not _ok(u.effective_user) or not await _gate(m, u.effective_user, "gambar_buat", "gambar"):
            return
        q = re.sub(r"^/gambar(@\w+)?", "", (m.caption or "").strip(), flags=re.I).strip()
        if not q:
            await m.reply_text("Tambahkan caption berisi perubahan yang kamu mau.\nContoh: jadikan gaya kartun anime")
            return
        await tg_gambar(m, c, q, m)

    return {"start": start, "id": myid, "sisa": sisa, "gambar": gambar, "teks": teks, "foto": foto}


async def on_error(update, context):
    log.error("Error Telegram: %s", context.error)


async def run_bots():
    global LOOP, LIMITER
    LOOP = asyncio.get_running_loop()
    LIMITER = Limiter()
    if not ALLOWED:
        log.warning("ALLOWED_USER_IDS kosong: siapa pun bisa memakai bot dan menghabiskan kuota Gemini kamu.")
    on = 0
    for key, cfg in AGENTS.items():
        tok = os.getenv(f"TOKEN_{key.upper()}")
        if not tok:
            log.info("[%s] tanpa Telegram (TOKEN_%s kosong), tetap aktif di web", cfg["nama"], key.upper())
            continue
        try:
            cmds, chat, photo = make_handlers(key)
            tg = ApplicationBuilder().token(tok).build()
            for n, f in cmds.items():
                tg.add_handler(CommandHandler(n, _group_cmd(key, f)))
            tg.add_handler(MessageHandler(filters.PHOTO, photo))
            tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
            tg.add_handler(CallbackQueryHandler(izin_klik, pattern=r"^izin:"))
            tg.add_error_handler(on_error)
            await tg.initialize()
            await tg.start()
            await tg.updater.start_polling(drop_pending_updates=True)
            TG_APPS[key] = tg
            on += 1
            log.info("[%s] online di Telegram (Lantai %s)", cfg["nama"], cfg["lantai"])
        except Exception:
            log.exception("[%s] gagal start", cfg["nama"])
    log.info("%d bot Telegram online dari %d agen AI (+ MCI.Canvas = 11 bot). Model utama %s, model lite %s, RPM %d", on, len(AGENTS), MODEL, MODEL_LITE, RPM)
    tok = os.getenv("TOKEN_GAMBAR")
    if tok:
        try:
            tg = ApplicationBuilder().token(tok).build()
            for n, f in pixel_handlers().items():
                tg.add_handler(CommandHandler(n, f) if n not in ("teks", "foto") else
                               MessageHandler(filters.PHOTO if n == "foto" else (filters.TEXT & ~filters.COMMAND), f))
            tg.add_handler(CallbackQueryHandler(izin_klik, pattern=r"^izin:"))
            tg.add_error_handler(on_error)
            await tg.initialize()
            await tg.start()
            await tg.updater.start_polling(drop_pending_updates=True)
            CANVAS_APP["app"] = tg
            log.info("[MCI Studio Gambar] bot gambar terpisah online (aktif=%s)", bool(CF_ACCOUNT and CF_TOKEN))
        except Exception:
            log.exception("[MCI Studio Gambar] gagal start")
    asyncio.create_task(laporan_jadwal())
    log.info("Laporan mingguan otomatis: setiap %s %02d:00 WIB (%s)", HARI_ID[LAP_HARI], LAP_JAM, "aktif" if SET.get("laporan_on", True) else "nonaktif")
    await asyncio.Event().wait()


if __name__ == "__main__":
    if not (ADMIN_TOKEN and VIEW_TOKEN):
        log.warning("ADMIN_TOKEN / VIEW_TOKEN belum diisi: web tidak bisa dibuka.")
    log.info("Anggota aktif dengan token pribadi: %d", sum(1 for m in MEMBERS.values() if m["aktif"]))
    try:
        db_init()
        usage_load()
        iot_load()
        gal_load()
        stok_load()
        struktur_load()
        members_load()
    except Exception:
        log.exception("Database gagal diinisialisasi (cek DATABASE_URL)")
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_bots())
