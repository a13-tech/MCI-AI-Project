#!/usr/bin/env python3
"""MCI Node: jembatan antara web MCI HQ (Render) dan MacBook + HDD di markas.

Fungsi:
  1. Gudang file berat di HDD (video dokumentasi ukuran asli). Unggahan datang langsung dari browser anggota
     dalam potongan (maks 64 MB per potong, karena Cloudflare gratis membatasi 100 MB per kiriman), lalu
     disambung di HDD. Pemutaran mendukung lompat-lompat (HTTP Range).
  2. Menerima backup database dari web tiap malam dan menyimpan 30 backup terakhir.
  3. Meneruskan permintaan AI ke Ollama lokal (format kompatibel OpenAI, /v1/...) dengan token,
     supaya web bisa memakai Ollama sebagai cadangan terakhir.
  4. Membuat salinan ringan 720p tiap video (butuh ffmpeg: brew install ffmpeg). Pemutaran di web memakai
     salinan ini supaya hemat kuota; file asli tetap utuh untuk diunduh atau diputar "kualitas asli".

Tanpa pustaka tambahan: cukup Python 3.9+ bawaan macOS.
Keamanan: semua akses butuh token rahasia (NODE_TOKEN) yang sama dengan di Panel CEO web, atau tautan bertanda
tangan sementara yang dibuat web dari token itu. Ollama tidak pernah dibuka langsung ke internet.

Pengaturan lewat variabel lingkungan (lihat node/PANDUAN.md):
  NODE_TOKEN    wajib. Token rahasia panjang, sama persis dengan NODE_TOKEN di Panel CEO.
  NODE_DIR      folder gudang di HDD, mis. /Volumes/HDD/MCI-Node
  BACKUP_DIR    folder backup (bawaan: NODE_DIR/backup), mis. /Volumes/MCI/Database Server MCI
  OLLAMA_URL    bawaan http://127.0.0.1:11434
  NODE_ASAL     asal web yang boleh mengunggah dari browser, bawaan https://mci-ai.my.id
  NODE_PORT     bawaan 8787
  NODE_SISA_GB  ruang HDD minimal yang dijaga tetap kosong, bawaan 10
"""
import hashlib
import hmac
import json
import os
import re
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

VERSI = "1.1"
TOKEN = os.getenv("NODE_TOKEN", "").strip()
DIR = os.path.abspath(os.path.expanduser(os.getenv("NODE_DIR", "~/MCI-Node")))
BERKAS = os.path.join(DIR, "berkas")
BACKUP = os.path.abspath(os.path.expanduser(os.getenv("BACKUP_DIR", "") or os.path.join(DIR, "backup")))
OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
ASAL = [a.strip().rstrip("/") for a in os.getenv("NODE_ASAL", "https://mci-ai.my.id").split(",") if a.strip()]
PORT = int(os.getenv("NODE_PORT", "8787"))
SISA = int(float(os.getenv("NODE_SISA_GB", "10")) * 1024 ** 3)
POTONG_MAKS = 64 * 1024 * 1024 + 1024          # satu kiriman unggahan dari browser
BACKUP_MAKS = 95 * 1024 * 1024                  # satu file backup
BACKUP_SIMPAN = 30
KEY_OK = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_./-]{0,199}")
_KUNCI_TULIS = threading.Lock()
_SALAH = []                                     # waktu percobaan token salah (rem kasar terhadap tebak-tebakan)
VIDEO_EXT = ("mp4", "m4v", "mov", "webm", "3gp")
RINGAN = ".ringan.mp4"                          # salinan 720p di sebelah file asli: <key>.ringan.mp4
_ANTRE = queue.Queue()
_DIANTRE = set()


LOG_MAKS = 2 * 1024 * 1024   # log disimpan di HDD (bukan storage internal Mac), dibatasi 2 MB lalu diputar
_KUNCI_LOG = threading.Lock()


def log(*a):
    baris = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(str(x) for x in a)
    try:
        p = os.path.join(DIR, "node.log")
        with _KUNCI_LOG:
            if os.path.exists(p) and os.path.getsize(p) > LOG_MAKS:
                os.replace(p, p + ".1")   # simpan satu log lama saja
            with open(p, "a", encoding="utf-8") as f:
                f.write(baris + "\n")
    except OSError:
        print(baris, flush=True)   # HDD belum siap: tampil di log sistem saja


def tanda(method, key, exp, total=0):
    """Tanda tangan tautan sementara. Harus sama persis dengan node_tanda() di main.py web."""
    pesan = f"{method}\n{key}\n{int(exp)}\n{int(total)}".encode()
    return hmac.new(TOKEN.encode(), pesan, hashlib.sha256).hexdigest()


def path_key(key):
    """Key berkas -> path aman di dalam folder BERKAS (tidak bisa keluar folder)."""
    if not KEY_OK.fullmatch(key or "") or ".." in key.split("/"):
        return None
    p = os.path.abspath(os.path.join(BERKAS, key))
    return p if p.startswith(BERKAS + os.sep) else None


def ruang_kosong():
    os.makedirs(DIR, exist_ok=True)
    return shutil.disk_usage(DIR).free


# ---------- salinan ringan 720p (hemat kuota saat diputar) ----------
def cari_ffmpeg():
    """launchd tidak membawa PATH Homebrew, jadi cek lokasi umumnya juga."""
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


def video_asli(p):
    nama = os.path.basename(p)
    return nama.rsplit(".", 1)[-1].lower() in VIDEO_EXT and not nama.endswith(RINGAN) and ".ringan." not in nama


def antre_ringan(p):
    if video_asli(p) and p not in _DIANTRE and not os.path.exists(p + RINGAN) and not os.path.exists(p + ".ringan.lewati"):
        _DIANTRE.add(p)
        _ANTRE.put(p)


def buat_ringan(p, ff):
    """Satu video -> <p>.ringan.mp4 (720p, H.264). Coba encoder hardware Mac dulu (ringan untuk CPU), lalu software."""
    tmp = p + ".ringan.tmp.mp4"
    umum = ["-vf", "scale=-2:'min(720,ih)'", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"]
    for enc in (["-c:v", "h264_videotoolbox", "-b:v", "2200k"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]):
        try:
            os.path.exists(tmp) and os.remove(tmp)
            r = subprocess.run([ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", p, *enc, *umum, tmp],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=4 * 3600,
                               preexec_fn=lambda: os.nice(10))   # prioritas rendah: Mac tetap enak dipakai
        except Exception as e:
            log("salinan ringan gagal:", os.path.basename(p), e)
            continue
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            if not os.path.exists(p):   # file asli dihapus saat sedang dikonversi
                os.remove(tmp)
                return
            if os.path.getsize(tmp) >= os.path.getsize(p) * 0.8:   # tidak jauh lebih kecil: tidak ada gunanya
                os.remove(tmp)
                open(p + ".ringan.lewati", "w").close()
                log("salinan ringan dilewati (tidak lebih kecil):", os.path.basename(p))
                return
            os.replace(tmp, p + RINGAN)
            log("salinan ringan siap:", os.path.basename(p), os.path.getsize(p) // 1048576, "MB ->", os.path.getsize(p + RINGAN) // 1048576, "MB")
            return
        log("encoder", enc[1], "gagal untuk", os.path.basename(p), (r.stderr or b"")[-200:].decode(errors="replace"))
    os.path.exists(tmp) and os.remove(tmp)


def pekerja_ringan():
    """Satu video dikerjakan bergantian (bukan bersamaan) supaya Mac tidak berat."""
    while True:
        p = _ANTRE.get()
        try:
            ff = cari_ffmpeg()
            if ff and os.path.isfile(p):
                buat_ringan(p, ff)
        except Exception as e:
            log("pekerja salinan ringan error:", e)
        finally:
            _DIANTRE.discard(p)


def pindai_ringan():
    """Saat Node menyala: video lama yang belum punya salinan ringan dimasukkan antrean."""
    if not cari_ffmpeg():
        log("ffmpeg belum terpasang: salinan ringan video tidak dibuat (brew install ffmpeg)")
        return
    for akar, _d, files in os.walk(BERKAS):
        for f in sorted(files):
            if f.endswith(".ringan.tmp.mp4"):
                os.remove(os.path.join(akar, f))   # sisa konversi yang terputus
            else:
                antre_ringan(os.path.join(akar, f))


class Node(BaseHTTPRequestHandler):
    server_version = "MCI-Node/" + VERSI
    protocol_version = "HTTP/1.1"

    # ---------- bantuan ----------
    def log_message(self, fmt, *args):   # log ringkas, tanpa query string (berisi tanda tangan)
        log(self.command, urlsplit(self.path).path, "->", args[1] if len(args) > 1 else "")

    def cors(self):
        asal = (self.headers.get("Origin") or "").rstrip("/")
        if asal and asal in ASAL:
            self.send_header("Access-Control-Allow-Origin", asal)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
            self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges, X-Varian")
            self.send_header("Access-Control-Max-Age", "600")

    def balas(self, kode, data=None, tipe="application/json"):
        body = b"" if data is None else (data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode())
        self.send_response(kode)
        self.cors()
        self.send_header("Content-Type", tipe)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def salah(self, kode=401, pesan="Token salah"):
        _SALAH.append(time.time())
        return self.balas(kode, {"error": pesan})

    def bertoken(self):
        kini = time.time()
        _SALAH[:] = [t for t in _SALAH if kini - t < 600]
        if len(_SALAH) > 50:
            return False   # terlalu banyak salah dalam 10 menit: tolak sementara
        h = self.headers.get("Authorization", "")
        return bool(TOKEN) and h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip(), TOKEN)

    def bertanda(self, method, key, q):
        try:
            exp, total, sig = int(q.get("exp", ["0"])[0]), int(q.get("n", ["0"])[0]), q.get("sig", [""])[0]
        except ValueError:
            return None
        if exp < time.time() or not hmac.compare_digest(sig, tanda(method, key, exp, total)):
            return None
        return total

    def baca_body(self, maks):
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > maks:
            return None
        return self.rfile.read(n) if n else b""

    # ---------- rute ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/sehat":
            return self.sehat()
        if u.path.startswith("/berkas/"):
            return self.ambil(unquote(u.path[8:]), q)
        if u.path.startswith("/v1/"):
            return self.ollama()
        return self.balas(404, {"error": "tidak ada"})

    do_HEAD = do_GET

    def do_PUT(self):
        u = urlsplit(self.path)
        if u.path.startswith("/berkas/"):
            return self.unggah(unquote(u.path[8:]), parse_qs(u.query))
        return self.balas(404, {"error": "tidak ada"})

    def do_POST(self):
        u = urlsplit(self.path)
        if u.path == "/backup":
            return self.backup(parse_qs(u.query))
        if u.path.startswith("/v1/"):
            return self.ollama()
        return self.balas(404, {"error": "tidak ada"})

    def do_DELETE(self):
        u = urlsplit(self.path)
        if not u.path.startswith("/berkas/"):
            return self.balas(404, {"error": "tidak ada"})
        if not self.bertoken():
            return self.salah()
        p = path_key(unquote(u.path[8:]))
        if not p:
            return self.balas(400, {"error": "nama berkas tidak sah"})
        for x in (p, p + ".part", p + RINGAN, p + ".ringan.lewati", p + ".ringan.tmp.mp4"):
            if os.path.exists(x):
                os.remove(x)
        return self.balas(200, {"ok": True})

    # ---------- isi ----------
    def sehat(self):
        if not self.bertoken():   # tanpa token: cukup tahu Node hidup
            return self.balas(200, {"ok": True, "nama": "MCI Node"})
        models = []
        try:
            with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=4) as f:
                models = [m.get("name") for m in json.loads(f.read()).get("models", [])]
            ollama = True
        except Exception:
            ollama = False
        os.makedirs(BERKAS, exist_ok=True)
        os.makedirs(BACKUP, exist_ok=True)
        cad = sorted(x for x in os.listdir(BACKUP) if x.startswith("MCI-backup-"))
        return self.balas(200, {"ok": True, "nama": "MCI Node", "versi": VERSI, "kosong": ruang_kosong(), "sisa_jaga": SISA,
                                "ollama": ollama, "model": models, "backup_terakhir": cad[-1] if cad else "", "backup_jumlah": len(cad),
                                "ffmpeg": bool(cari_ffmpeg()), "antrean": len(_DIANTRE)})

    def ambil(self, key, q):
        p = path_key(key)
        if not p:
            return self.balas(400, {"error": "nama berkas tidak sah"})
        if not (self.bertoken() or self.bertanda("GET", key, q) is not None):
            return self.salah(403, "Tautan kedaluwarsa atau salah")
        if not os.path.isfile(p):
            return self.balas(404, {"error": "berkas tidak ada"})
        varian = "asli"
        if q.get("v", [""])[0] == "ringan" and not q.get("unduh") and os.path.isfile(p + RINGAN):
            p, key, varian = p + RINGAN, key + RINGAN, "ringan"   # salinan 720p (lebih hemat kuota); kalau belum ada: file asli
        uk = os.path.getsize(p)
        mulai, akhir, kode = 0, uk - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "").strip())
        if m and uk:
            if m.group(1):
                mulai = int(m.group(1))
                akhir = min(int(m.group(2)), uk - 1) if m.group(2) else uk - 1
            elif m.group(2):
                mulai = max(0, uk - int(m.group(2)))
            if mulai > akhir or mulai >= uk:
                self.send_response(416)
                self.cors()
                self.send_header("Content-Range", f"bytes */{uk}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            kode = 206
        ext = key.rsplit(".", 1)[-1].lower()
        tipe = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "3gp": "video/3gpp",
                "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "stl": "model/stl", "pdf": "application/pdf"}.get(ext, "application/octet-stream")
        self.send_response(kode)
        self.cors()
        self.send_header("Content-Type", tipe)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(akhir - mulai + 1))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Varian", varian)
        if q.get("unduh"):
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(p)}"')
        if kode == 206:
            self.send_header("Content-Range", f"bytes {mulai}-{akhir}/{uk}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(mulai)
            sisa = akhir - mulai + 1
            try:
                while sisa > 0:
                    b = f.read(min(1024 * 1024, sisa))
                    if not b:
                        break
                    self.wfile.write(b)
                    sisa -= len(b)
            except (BrokenPipeError, ConnectionResetError):
                pass   # penonton menutup / melompat; wajar

    def unggah(self, key, q):
        """PUT /berkas/<key>?exp&n&sig&off=... : satu potongan dari browser. n = ukuran total (ditandatangani web)."""
        p = path_key(key)
        if not p:
            return self.balas(400, {"error": "nama berkas tidak sah"})
        total = self.bertanda("PUT", key, q)
        if total is None or total <= 0:
            return self.salah(403, "Izin unggah kedaluwarsa atau salah. Ulangi dari web.")
        try:
            off = int(q.get("off", ["0"])[0])
        except ValueError:
            return self.balas(400, {"error": "off tidak sah"})
        data = self.baca_body(POTONG_MAKS)
        if data is None:
            return self.balas(413, {"error": "potongan terlalu besar (maks 64 MB)"})
        if off < 0 or off + len(data) > total:
            return self.balas(400, {"error": "potongan melewati ukuran total"})
        if os.path.exists(p):
            return self.balas(409, {"error": "berkas sudah ada", "terima": os.path.getsize(p)})
        with _KUNCI_TULIS:
            if off == 0 and ruang_kosong() - total < SISA:
                return self.balas(507, {"error": "HDD hampir penuh"})
            os.makedirs(os.path.dirname(p), exist_ok=True)
            part = p + ".part"
            if off == 0 and os.path.exists(part):
                os.remove(part)
            if off > 0 and (not os.path.exists(part) or os.path.getsize(part) != off):
                return self.balas(409, {"error": "urutan potongan tidak sesuai", "terima": os.path.getsize(part) if os.path.exists(part) else 0})
            with open(part, "ab") as f:
                f.write(data)
            ada = os.path.getsize(part)
            if ada == total:
                os.replace(part, p)
                log("berkas lengkap:", key, total, "byte")
                antre_ringan(p)
        return self.balas(200, {"ok": True, "terima": ada, "selesai": ada == total})

    def backup(self, q):
        if not self.bertoken():
            return self.salah()
        data = self.baca_body(BACKUP_MAKS)
        if not data:
            return self.balas(400, {"error": "backup kosong atau terlalu besar"})
        os.makedirs(BACKUP, exist_ok=True)
        nama = f"MCI-backup-{datetime.now():%Y-%m-%d_%H%M}.zip"
        with open(os.path.join(BACKUP, nama + ".part"), "wb") as f:
            f.write(data)
        os.replace(os.path.join(BACKUP, nama + ".part"), os.path.join(BACKUP, nama))
        cad = sorted(x for x in os.listdir(BACKUP) if x.startswith("MCI-backup-") and x.endswith(".zip"))
        for lama in cad[:-BACKUP_SIMPAN]:
            os.remove(os.path.join(BACKUP, lama))
        log("backup disimpan:", nama, len(data), "byte")
        return self.balas(200, {"ok": True, "nama": nama, "jumlah": min(len(cad), BACKUP_SIMPAN)})

    def ollama(self):
        """Teruskan /v1/... ke Ollama lokal (kompatibel OpenAI). Jawaban streaming diteruskan apa adanya."""
        if not self.bertoken():
            return self.salah()
        body = self.baca_body(8 * 1024 * 1024) if self.command == "POST" else None
        if body is None and self.command == "POST":
            return self.balas(413, {"error": "permintaan terlalu besar"})
        req = urllib.request.Request(OLLAMA + urlsplit(self.path).path, data=body, method=self.command,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
        try:
            with urllib.request.urlopen(req, timeout=600) as f:
                self.send_response(f.status)
                self.cors()
                self.send_header("Content-Type", f.headers.get("Content-Type", "application/json"))
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while True:
                    b = f.read1(65536) if hasattr(f, "read1") else f.read(65536)
                    if not b:
                        break
                    self.wfile.write(f"{len(b):X}\r\n".encode() + b + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
        except urllib.error.HTTPError as e:
            return self.balas(e.code, e.read() or b"{}")
        except Exception as e:
            return self.balas(502, {"error": f"Ollama tidak menjawab: {e}"})


def main():
    if len(TOKEN) < 24:
        sys.exit("NODE_TOKEN belum diisi atau terlalu pendek (minimal 24 karakter). Lihat node/PANDUAN.md")
    os.makedirs(BERKAS, exist_ok=True)
    os.makedirs(BACKUP, exist_ok=True)
    log(f"MCI Node {VERSI} jalan di http://127.0.0.1:{PORT}  gudang={DIR}  backup={BACKUP}  ollama={OLLAMA}  ffmpeg={cari_ffmpeg() or 'belum ada'}")
    threading.Thread(target=pekerja_ringan, daemon=True).start()
    threading.Thread(target=pindai_ringan, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Node)   # hanya bisa dihubungi dari Mac ini; internet lewat Cloudflare Tunnel
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("berhenti")


if __name__ == "__main__":
    main()
