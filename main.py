"""MCI HQ: 10 agen AI (Gemini) di 4 lantai, Boss pembagi tugas, Sidang Dewan, Skills,
mode Caveman, MCP, keuangan AI, dan web kantor virtual 3D.

Prinsip hemat token (Gemini Tier 1):
1. Router lokal tanpa AI: pesan yang jelas tujuannya langsung ke bot yang tepat, Boss tidak dipanggil.
2. Skill dipicu kata kunci: instruksi lengkap skill hanya dikirim saat dipakai.
3. Boss & bot "cepat" memakai model Flash-Lite, thinking diset paling rendah.
4. Hasil 1 tugas tidak dirangkum ulang; hasil pendek digabung tanpa AI.
5. Cache jawaban identik 15 menit, pembatas RPM, dan batas token harian.
"""
import os, io, csv, json, re, time, asyncio, secrets, logging, sqlite3, threading, contextlib, hashlib
from collections import deque, OrderedDict
from datetime import datetime

import pytz, psutil
from flask import Flask, jsonify, abort, request, send_from_directory, make_response, redirect, Response
from pydantic import BaseModel
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

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
DATABASE_URL = os.getenv("DATABASE_URL", "")
PG = bool(DATABASE_URL and psycopg2)
DB_PATH = os.getenv("DB_PATH", "mci.db")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x.isdigit()}
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
STATE = {k: {"status": "IDLE", "activity": "Siaga", "active": 0, "calls": 0, "tokens": 0, "mode": "meja"} for k in AGENTS}
SET = {"caveman": False, "mcp": False}
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
        sql(c, f"CREATE TABLE IF NOT EXISTS pemakaian (tanggal TEXT NOT NULL, model TEXT NOT NULL, masuk {big} NOT NULL DEFAULT 0, keluar {big} NOT NULL DEFAULT 0, pikir {big} NOT NULL DEFAULT 0, panggilan {big} NOT NULL DEFAULT 0, PRIMARY KEY (tanggal, model))")
        r = sql(c, "SELECT v FROM pengaturan WHERE k=?", ("s",)).fetchone()
        if r:
            SET.update(json.loads(r[0]))
        if not sql(c, "SELECT COUNT(*) FROM skills").fetchone()[0]:
            now = datetime.now(WIB).isoformat(timespec="seconds")
            for s in SKILL_SEED:
                sql(c, "INSERT INTO skills (nama,bot,deskripsi,pemicu,isi,dibuat) VALUES (?,?,?,?,?,?)", (*s, now))
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


def _begin(k, act):
    s = STATE[k]
    s["active"] += 1
    s["status"], s["activity"] = "WORKING", act


def _end(k):
    s = STATE[k]
    s["active"] = max(0, s["active"] - 1)
    if not s["active"]:
        s["status"], s["activity"] = "IDLE", "Siaga"


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
              use_mcp=True, skill=None, brief=False, cache=True):
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

    _begin(key, act)
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
        _end(key)


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
    return e


async def run_task(bot, perintah, skill=None, jalur="boss", sem=None):
    e = _log(bot, perintah, "Bekerja", jalur)
    async with (sem or contextlib.nullcontext()):
        try:
            need_kas = bot == "ledger" and re.search(r"\b(kas|saldo|uang|rekap|keuangan|pemasukan|pengeluaran)\b", perintah, re.I)
            ctx = await asyncio.to_thread(kas_context) if need_kas else ""
            h = await ask(bot, ctx + perintah, skill=skill, act=("Skill " + skill) if skill else "Mengerjakan tugas")
            e["status"] = "Selesai"
        except Exception as ex:
            log.exception("Tugas %s gagal", bot)
            e["status"] = "Gagal"
            h = f"(gagal: {ex})" if isinstance(ex, RuntimeError) else "(gagal, cek log server)"
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
        STATE[k]["mode"] = "dewan"
    _log("dewan", pertanyaan, "Sidang", "dewan")
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
        return {"jalur": "dewan", "keterangan": "Sidang dewan: " + ", ".join(p["nama"] for p in pend), "pendapat": pend, "jawaban": putusan, "rencana": []}
    finally:
        for k in m + ["apex"]:
            STATE[k]["mode"] = "meja"


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


# ---------- WEB ----------
app = Flask(__name__, static_folder=None)
FAILS = {}


def _eq(a, b):
    return bool(b) and secrets.compare_digest(a.encode(), b.encode())


def role():
    t = request.cookies.get("mci_auth", "")
    return "admin" if _eq(t, ADMIN_TOKEN) else "viewer" if _eq(t, VIEW_TOKEN) else None


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
    if _eq(token, ADMIN_TOKEN) or _eq(token, VIEW_TOKEN):
        return _setcookie(make_response(redirect("/")), token)
    FAILS[ip].append(now)
    return None


LOGIN = """<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCI</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0A2A6B;font-family:system-ui,sans-serif;color:#fff}
form{width:min(360px,90vw);display:grid;gap:14px;padding:28px;border:1px solid #D4AF5A;border-radius:18px;background:rgba(255,255,255,.06)}
b{font-size:28px;letter-spacing:.14em;color:#F1D58A}input,button{font:inherit;padding:13px;border-radius:12px;border:0;min-height:44px}
button{background:#D4AF5A;color:#0A2A6B;font-weight:800;cursor:pointer}p{margin:0;color:#FFB4B4;font-size:14px}</style></head>
<body><form method="post" action="/login"><b>MCI</b><label for="t">Token akses</label><input id="t" name="t" type="password" autocomplete="current-password" required>%s<button>Masuk</button></form></body></html>"""


@app.before_request
def guard():
    if request.method not in ("GET", "HEAD") and request.path != "/login" and request.headers.get("X-MCI") != "1":
        abort(403)


def need(r):
    cur = role()
    if not cur:
        abort(401)
    if r == "admin" and cur != "admin":
        abort(403)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        resp = _try_login(request.form.get("t", ""))
        return resp or (LOGIN % "<p>Token salah.</p>", 401)
    return LOGIN % ""


@app.route("/")
def index():
    k = request.args.get("k")
    if k:
        return _try_login(k) or abort(404)
    return send_from_directory("web", "index.html") if role() else redirect("/login")


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/api/me")
def api_me():
    need("viewer")
    return jsonify({"role": role(), **SET, "mcp_siap": bool(MCP_SERVERS and ClientSession), "db_permanen": PG, "lantai": LANTAI})


@app.route("/api/office")
def api_office():
    need("viewer")
    adm = role() == "admin"
    ags = [{"key": k, "name": a["nama"], "peran": a["peran"], "boss": a["boss"], "lantai": a["lantai"], "divisi": a["divisi"],
            "status": s["status"], "activity": s["activity"], "mode": s["mode"], "model": model_for(k),
            "gemini": bool(gkey(k)), "telegram": bool(os.getenv(f"TOKEN_{k.upper()}")),
            **({"calls": s["calls"], "tokens": s["tokens"]} if adm else {})} for k, a in AGENTS.items() for s in [STATE[k]]]
    return jsonify({"agents": ags, "log": list(TASKLOG) if adm else [], "lantai": LANTAI})


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
    need("admin")
    d = request.get_json(silent=True) or {}
    teks = str(d.get("perintah", "")).strip()[:3000]
    if not teks:
        return jsonify({"error": "Perintah kosong"}), 400
    try:
        if d.get("mode") == "dewan":
            ang = [str(x).lower() for x in (d.get("anggota") or [])][:COUNCIL_MAX]
            return jsonify({"job": start_job(council_run(teks, ang or None))})
        return jsonify({"job": start_job(smart_run(teks))})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/job/<jid>")
def api_job(jid):
    need("admin")
    return jsonify(JOBS.get(jid) or {"status": "tidak ada"})


@app.route("/api/kas")
def api_kas():
    need("admin")
    rows = kas_all()
    return jsonify({"rows": rows[-300:][::-1], "ringkas": kas_summary(rows), "kategori": KAT})


@app.route("/api/kas/olah", methods=["POST"])
def api_kas_olah():
    need("admin")
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
    need("admin")
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
    need("admin")
    return jsonify({"dihapus": kas_del(i)})


@app.route("/api/kas.csv")
def api_kas_csv():
    need("admin")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["tanggal", "keterangan", "kategori", "masuk", "keluar", "saldo"])
    for r in kas_all():
        ket = r["keterangan"]
        w.writerow([r["tanggal"], "'" + ket if ket[:1] in "=+-@" else ket, r["kategori"], r["masuk"], r["keluar"], r["saldo"]])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=kas-mci.csv"})


@app.route("/api/skills", methods=["GET", "POST"])
def api_skills():
    need("admin")
    if request.method == "POST":
        try:
            return jsonify({"disimpan": skill_save(request.get_json(silent=True) or {})})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"skills": skills_public(), "bots": [{"key": k, "nama": a["nama"]} for k, a in AGENTS.items()]})


@app.route("/api/skills/<nama>", methods=["DELETE"])
def api_skill_del(nama):
    need("admin")
    return jsonify({"dihapus": skill_del(nama)})


@app.route("/api/usage")
def api_usage():
    need("admin")
    return jsonify(usage_report())


@app.route("/api/settings", methods=["POST"])
def api_settings():
    need("admin")
    d = request.get_json(silent=True) or {}
    for k in ("caveman", "mcp"):
        if k in d:
            SET[k] = bool(d[k])
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


def _ok(u):
    return not ALLOWED or (u is not None and u.id in ALLOWED)


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


def make_handlers(key):
    cfg = AGENTS[key]

    async def start(u, c):
        await u.message.reply_text(f"Halo, saya {cfg['nama']} (Lantai {cfg['lantai']}, {cfg['divisi']}).\n{cfg['peran']}.\n\n"
                                   "/status  /id  /budget\n/caveman on|off\n/council <pertanyaan>\n/skills  /skill <nama> <teks>")

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
        if a in ("on", "off"):
            SET["caveman"] = a == "on"
            await asyncio.to_thread(set_save)
        await u.message.reply_text(f"Mode caveman: {'ON' if SET['caveman'] else 'OFF'} (berlaku untuk semua bot)")

    async def hapus(u, c):
        if not (ALLOWED and _ok(u.effective_user)) or not (c.args and c.args[0].isdigit()):
            return
        n = await asyncio.to_thread(kas_del, int(c.args[0]))
        await u.message.reply_text("Dihapus." if n else "Nomor tidak ditemukan.")

    async def council(u, c):
        if not _ok(u.effective_user):
            return
        q = " ".join(c.args or []).strip()
        if not q:
            await u.message.reply_text("Pakai: /council <pertanyaan>\nContoh: /council pakai baterai LiPo atau 18650 untuk kapal monitoring?")
            return
        await c.bot.send_chat_action(chat_id=u.message.chat_id, action="typing")
        try:
            h = await council_run(q[:2000])
            await _send(u.message, fmt_result(h))
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
        s = SKILLS[c.args[0].lower()]
        teks = " ".join(c.args[1:]).strip() or s["deskripsi"]
        await c.bot.send_chat_action(chat_id=u.message.chat_id, action="typing")
        r = await run_task(s["bot"], teks, s["nama"], "skill")
        await _send(u.message, f"🧰 {s['nama']} oleh {AGENTS[s['bot']]['nama']}\n\n{r['hasil']}")

    async def chat(u, c):
        m = u.message
        if not m or not m.text or not _addressed(u, c):
            return
        if not _ok(u.effective_user):
            await m.reply_text(f"Akses ditolak. ID kamu: {u.effective_user.id}")
            return
        if key == "ledger" and (m.chat.type != "private" or not ALLOWED):
            await m.reply_text("Ledger hanya di chat pribadi dan butuh ALLOWED_USER_IDS terisi.")
            return
        text = m.text.replace(f"@{c.bot.username}", "").strip() if c.bot.username else m.text
        if not text:
            return
        try:
            await c.bot.send_chat_action(chat_id=m.chat_id, action="typing")
            if key == "apex":
                ans = fmt_result(await smart_run(text))
            elif key == "ledger":
                ans = await ledger_msg(text)
            else:
                sk = skill_match(text)
                if sk and sk["bot"] == key:
                    ans = await ask(key, text, skill=sk["nama"])
                else:
                    h = HIST.setdefault((key, m.chat_id), [])
                    ans = await ask(key, text, history=h)
                    h += [types.Content(role="user", parts=[types.Part(text=text)]), types.Content(role="model", parts=[types.Part(text=ans)])]
                    del h[:-6]
        except Exception as e:
            log.exception("[%s] gagal", cfg["nama"])
            ans = str(e) if isinstance(e, RuntimeError) else "⚠️ Ada gangguan saat menghubungi Gemini. Coba lagi sebentar."
        await _send(m, ans)

    async def photo(u, c):
        m = u.message
        if key != "ledger" or m.chat.type != "private" or not ALLOWED or not _ok(u.effective_user):
            return
        try:
            f = await m.photo[-1].get_file()
            ans = await ledger_msg(m.caption or "", bytes(await f.download_as_bytearray()))
        except Exception:
            log.exception("foto ledger gagal")
            ans = "⚠️ Gagal membaca foto."
        await _send(m, ans)

    return {"start": start, "id": myid, "status": status, "tokens": status, "budget": budget, "caveman": caveman,
            "hapus": hapus, "council": council, "dewan": council, "skills": skills, "skill": skill}, chat, photo


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
                tg.add_handler(CommandHandler(n, f))
            tg.add_handler(MessageHandler(filters.PHOTO, photo))
            tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
            tg.add_error_handler(on_error)
            await tg.initialize()
            await tg.start()
            await tg.updater.start_polling(drop_pending_updates=True)
            on += 1
            log.info("[%s] online di Telegram (Lantai %s)", cfg["nama"], cfg["lantai"])
        except Exception:
            log.exception("[%s] gagal start", cfg["nama"])
    log.info("%d bot Telegram online dari %d agen. Model utama %s, model lite %s, RPM %d", on, len(AGENTS), MODEL, MODEL_LITE, RPM)
    await asyncio.Event().wait()


if __name__ == "__main__":
    if not (ADMIN_TOKEN and VIEW_TOKEN):
        log.warning("ADMIN_TOKEN / VIEW_TOKEN belum diisi: web tidak bisa dibuka.")
    try:
        db_init()
        usage_load()
    except Exception:
        log.exception("Database gagal diinisialisasi (cek DATABASE_URL)")
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_bots())
