"""Studio 3D & gambar-dari-kode MCI.

AI TIDAK menjalankan kode di server. AI hanya menulis "resep" (daftar bentuk + ukuran dalam milimeter),
lalu modul ini yang menghitung bentuknya secara pasti (CSG: gabung / potong), mengukur hasilnya,
dan menulis file STL untuk cetak 3D. Untuk gambar, AI menulis SVG lalu disaring di sini.

Murni Python, tanpa pustaka luar.
"""
import math
import re
import struct
import xml.etree.ElementTree as ET

EPS = 1e-5
MAKS_BAGIAN = 40
MAKS_UKURAN = 500.0     # mm
BENTUK = ("kotak", "silinder", "kerucut", "bola", "prisma")
OPERASI = ("tambah", "potong")


# ---------- vektor (tuple) ----------
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _bidang(vs):
    """Bidang (normal, w) dari poligon; pakai metode Newell supaya tahan titik segaris."""
    nx = ny = nz = 0.0
    for i, a in enumerate(vs):
        b = vs[(i + 1) % len(vs)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    n = _unit((nx, ny, nz))
    return (n, _dot(n, vs[0]))


def _poli(vs):
    return (vs, _bidang(vs))


def _balik(p):
    (n, w) = p[1]
    return (p[0][::-1], ((-n[0], -n[1], -n[2]), -w))


def _belah(bidang, p, cf, cb, depan, belakang):
    n, w = bidang
    vs = p[0]
    tipe, semua = [], 0
    for v in vs:
        t = _dot(n, v) - w
        k = 2 if t < -EPS else (1 if t > EPS else 0)
        semua |= k
        tipe.append(k)
    if semua == 0:
        (cf if _dot(n, p[1][0]) > 0 else cb).append(p)
    elif semua == 1:
        depan.append(p)
    elif semua == 2:
        belakang.append(p)
    else:
        f, b = [], []
        m = len(vs)
        for i in range(m):
            j = (i + 1) % m
            ti, tj, vi, vj = tipe[i], tipe[j], vs[i], vs[j]
            if ti != 2:
                f.append(vi)
            if ti != 1:
                b.append(vi)
            if (ti | tj) == 3:
                d = _sub(vj, vi)
                t = (w - _dot(n, vi)) / _dot(n, d)
                v = (vi[0] + d[0] * t, vi[1] + d[1] * t, vi[2] + d[2] * t)
                f.append(v)
                b.append(v)
        if len(f) >= 3:
            depan.append((f, p[1]))
        if len(b) >= 3:
            belakang.append((b, p[1]))


class _Node:
    __slots__ = ("bidang", "depan", "belakang", "polis")

    def __init__(self, polis=None):
        self.bidang = None
        self.depan = None
        self.belakang = None
        self.polis = []
        if polis:
            self.bangun(polis)

    def _semua_node(self):
        st, out = [self], []
        while st:
            n = st.pop()
            out.append(n)
            if n.depan:
                st.append(n.depan)
            if n.belakang:
                st.append(n.belakang)
        return out

    def bangun(self, polis):
        st = [(self, polis)]
        while st:
            node, ps = st.pop()
            if not ps:
                continue
            if node.bidang is None:
                node.bidang = ps[0][1]
            f, b = [], []
            for p in ps:
                _belah(node.bidang, p, node.polis, node.polis, f, b)
            if f:
                if node.depan is None:
                    node.depan = _Node()
                st.append((node.depan, f))
            if b:
                if node.belakang is None:
                    node.belakang = _Node()
                st.append((node.belakang, b))

    def balik(self):
        for n in self._semua_node():
            n.polis = [_balik(p) for p in n.polis]
            if n.bidang is not None:
                (nn, w) = n.bidang
                n.bidang = ((-nn[0], -nn[1], -nn[2]), -w)
            n.depan, n.belakang = n.belakang, n.depan

    def potong_polis(self, polis):
        hasil, st = [], [(self, polis)]
        while st:
            node, ps = st.pop()
            if node.bidang is None:
                hasil.extend(ps)
                continue
            f, b = [], []
            for p in ps:
                _belah(node.bidang, p, f, b, f, b)
            if node.depan is not None:
                st.append((node.depan, f))
            else:
                hasil.extend(f)
            if node.belakang is not None:
                st.append((node.belakang, b))
        return hasil

    def potong_ke(self, bsp):
        for n in self._semua_node():
            n.polis = bsp.potong_polis(n.polis)

    def semua(self):
        out = []
        for n in self._semua_node():
            out.extend(n.polis)
        return out


def gabung(a, b):
    if not a:
        return list(b)
    if not b:
        return list(a)
    if not _kotak_tumpang(kotak_batas(a), kotak_batas(b)):
        return list(a) + list(b)
    A, B = _Node(list(a)), _Node(list(b))
    A.potong_ke(B)
    B.potong_ke(A)
    B.balik()
    B.potong_ke(A)
    B.balik()
    A.bangun(B.semua())
    return A.semua()


def kurang(a, b):
    if not a or not b or not _kotak_tumpang(kotak_batas(a), kotak_batas(b)):
        return list(a)
    A, B = _Node(list(a)), _Node(list(b))
    A.balik()
    A.potong_ke(B)
    B.potong_ke(A)
    B.balik()
    B.potong_ke(A)
    B.balik()
    A.bangun(B.semua())
    A.balik()
    return A.semua()


# ---------- ukuran ----------
def kotak_batas(polis):
    xs = [v for p in polis for v in p[0]]
    if not xs:
        return ((0, 0, 0), (0, 0, 0))
    return (tuple(min(v[i] for v in xs) for i in range(3)), tuple(max(v[i] for v in xs) for i in range(3)))


def _kotak_tumpang(a, b, celah=EPS):
    return all(a[0][i] < b[1][i] - celah and b[0][i] < a[1][i] - celah for i in range(3))


def segitiga(polis):
    for vs, (n, _w) in polis:
        for i in range(1, len(vs) - 1):
            yield n, vs[0], vs[i], vs[i + 1]


def volume(polis):
    return sum(_dot(a, _cross(b, c)) for _n, a, b, c in segitiga(polis)) / 6.0


# ---------- bentuk dasar (semua berpusat di titik asal) ----------
def _cembung(pts):
    s = 0
    m = len(pts)
    for i in range(m):
        a, b, c = pts[i], pts[(i + 1) % m], pts[(i + 2) % m]
        z = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(z) > 1e-9:
            if s and (z > 0) != (s > 0):
                return False
            s = z
    return True


def _luas2(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))


def _telinga(pts):
    """Pecah poligon sederhana (CCW) menjadi segitiga (ear clipping)."""
    idx, out, jaga = list(range(len(pts))), [], 0
    def dalam(p, a, b, c):
        d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
        d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
        d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
    while len(idx) > 3 and jaga < 10000:
        jaga += 1
        ketemu = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-9:
                continue
            if any(dalam(pts[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            out.append([a, b, c])
            idx.pop(k)
            ketemu = True
            break
        if not ketemu:
            break
    if len(idx) == 3:
        out.append([pts[i] for i in idx])
    return out


def prisma(pts, h):
    """Ekstrusi poligon 2D (daftar [x, y]) setinggi h, berpusat di z = 0."""
    pts = [(float(x), float(y)) for x, y in pts]
    if _luas2(pts) < 0:
        pts = pts[::-1]
    z0, z1 = -h / 2.0, h / 2.0
    polis = []
    tutup = [pts] if _cembung(pts) else _telinga(pts)
    for t in tutup:
        polis.append(_poli([(x, y, z1) for x, y in t]))
        polis.append(_poli([(x, y, z0) for x, y in t[::-1]]))
    m = len(pts)
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % m]
        polis.append(_poli([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)]))
    return polis


def kotak(sx, sy, sz, radius=0.0, sisi=8):
    x, y = sx / 2.0, sy / 2.0
    r = max(0.0, min(float(radius or 0), x - 1e-3, y - 1e-3))
    if r <= 0:
        return prisma([(-x, -y), (x, -y), (x, y), (-x, y)], sz)
    pts = []
    for cx, cy, a0 in ((x - r, y - r, 0), (-x + r, y - r, 90), (-x + r, -y + r, 180), (x - r, -y + r, 270)):
        for k in range(sisi + 1):
            a = math.radians(a0 + 90.0 * k / sisi)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return prisma(pts, sz)


def kerucut(r1, r2, h, sisi=48):
    """Silinder (r1 == r2) atau kerucut terpancung; sumbu Z, berpusat di z = 0."""
    z0, z1 = -h / 2.0, h / 2.0
    ling = [(math.cos(2 * math.pi * k / sisi), math.sin(2 * math.pi * k / sisi)) for k in range(sisi)]
    polis = []
    if r1 > 0:
        polis.append(_poli([(c * r1, s * r1, z0) for c, s in ling[::-1]]))
    if r2 > 0:
        polis.append(_poli([(c * r2, s * r2, z1) for c, s in ling]))
    for k in range(sisi):
        a, b = ling[k], ling[(k + 1) % sisi]
        if r1 > 0 and r2 > 0:
            polis.append(_poli([(a[0] * r1, a[1] * r1, z0), (b[0] * r1, b[1] * r1, z0), (b[0] * r2, b[1] * r2, z1), (a[0] * r2, a[1] * r2, z1)]))
        elif r1 > 0:
            polis.append(_poli([(a[0] * r1, a[1] * r1, z0), (b[0] * r1, b[1] * r1, z0), (0.0, 0.0, z1)]))
        else:
            polis.append(_poli([(0.0, 0.0, z0), (b[0] * r2, b[1] * r2, z1), (a[0] * r2, a[1] * r2, z1)]))
    return polis


def bola(r, sisi=32):
    lapis = max(4, sisi // 2)
    polis = []
    def titik(i, j):
        t, f = math.pi * j / lapis, 2 * math.pi * i / sisi
        return (r * math.sin(t) * math.cos(f), r * math.sin(t) * math.sin(f), r * math.cos(t))
    for i in range(sisi):
        for j in range(lapis):
            vs = [titik(i, j)] if j == 0 else [titik(i + 1, j), titik(i, j)]
            if j < lapis - 1:
                vs += [titik(i, j + 1), titik(i + 1, j + 1)]
            else:
                vs += [titik(i, j + 1)]
            polis.append(_poli(vs))
    return polis


def _ubah(polis, rot, pos):
    rx, ry, rz = (math.radians(float(a or 0)) for a in rot)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    def f(v):
        x, y, z = v
        y, z = y * cx - z * sx, y * sx + z * cx
        x, z = x * cy + z * sy, -x * sy + z * cy
        x, y = x * cz - y * sz, x * sz + y * cz
        return (x + pos[0], y + pos[1], z + pos[2])
    return [_poli([f(v) for v in p[0]]) for p in polis]


# ---------- resep ----------
def _angka(d, k, bawaan=0.0, lo=None, hi=None):
    try:
        v = float(d.get(k, bawaan) if d.get(k) is not None else bawaan)
    except (TypeError, ValueError):
        raise ValueError(f"'{k}' harus angka")
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"'{k}' tidak sah")
    if lo is not None and v < lo:
        raise ValueError(f"'{k}' minimal {lo:g}")
    if hi is not None and v > hi:
        raise ValueError(f"'{k}' maksimal {hi:g}")
    return v


def rapikan(resep):
    """Validasi resep dari AI. Kembalikan daftar bagian yang bersih; ValueError kalau salah."""
    bagian = resep.get("bagian") if isinstance(resep, dict) else None
    if not isinstance(bagian, list) or not bagian:
        raise ValueError("Resep tidak punya 'bagian'")
    if len(bagian) > MAKS_BAGIAN:
        raise ValueError(f"Terlalu banyak bagian (maksimal {MAKS_BAGIAN})")
    out = []
    for n, b in enumerate(bagian, 1):
        if not isinstance(b, dict):
            raise ValueError(f"Bagian {n} bukan objek")
        try:
            bt, op = str(b.get("bentuk", "")).lower(), str(b.get("operasi", "tambah")).lower()
            if bt not in BENTUK:
                raise ValueError(f"bentuk '{bt}' tidak dikenal (pilih: {', '.join(BENTUK)})")
            if op not in OPERASI:
                raise ValueError("operasi harus 'tambah' atau 'potong'")
            x = {"nama": str(b.get("nama") or f"bagian {n}")[:60], "bentuk": bt, "operasi": op,
                 "pos": [_angka(b, k, 0, -MAKS_UKURAN, MAKS_UKURAN) for k in ("x", "y", "z")],
                 "rot": [_angka(b, k, 0, -360, 360) for k in ("rx", "ry", "rz")],
                 "sisi": int(_angka(b, "sisi", 48, 3, 96))}
            if bt == "kotak":
                x.update(sx=_angka(b, "sx", 0, 0.1, MAKS_UKURAN), sy=_angka(b, "sy", 0, 0.1, MAKS_UKURAN), sz=_angka(b, "sz", 0, 0.1, MAKS_UKURAN), radius=_angka(b, "radius", 0, 0, MAKS_UKURAN))
            elif bt == "silinder":
                x.update(r=_angka(b, "r", 0, 0.05, MAKS_UKURAN), h=_angka(b, "h", 0, 0.1, MAKS_UKURAN))
            elif bt == "kerucut":
                x.update(r=_angka(b, "r", 0, 0, MAKS_UKURAN), r2=_angka(b, "r2", 0, 0, MAKS_UKURAN), h=_angka(b, "h", 0, 0.1, MAKS_UKURAN))
                if x["r"] <= 0 and x["r2"] <= 0:
                    raise ValueError("kerucut perlu r atau r2 lebih dari 0")
            elif bt == "bola":
                x.update(r=_angka(b, "r", 0, 0.05, MAKS_UKURAN))
            else:
                t = b.get("titik")
                if not isinstance(t, list) or not 3 <= len(t) <= 200:
                    raise ValueError("prisma perlu 'titik' berisi 3-200 pasangan [x, y]")
                pts = []
                for p in t:
                    if not isinstance(p, (list, tuple)) or len(p) != 2:
                        raise ValueError("tiap titik prisma harus [x, y]")
                    pts.append((_angka({"v": p[0]}, "v", 0, -MAKS_UKURAN, MAKS_UKURAN), _angka({"v": p[1]}, "v", 0, -MAKS_UKURAN, MAKS_UKURAN)))
                if abs(_luas2(pts)) < 1e-6:
                    raise ValueError("titik prisma tidak membentuk bidang")
                x.update(titik=pts, h=_angka(b, "h", 0, 0.1, MAKS_UKURAN))
        except ValueError as e:
            raise ValueError(f"Bagian {n} ({b.get('nama') or b.get('bentuk') or '?'}): {e}")
        out.append(x)
    if out[0]["operasi"] != "tambah":
        raise ValueError("Bagian pertama harus 'tambah' (belum ada benda untuk dipotong)")
    return out


def _bentuk(x):
    s = x["sisi"]
    if x["bentuk"] == "kotak":
        p = kotak(x["sx"], x["sy"], x["sz"], x["radius"], max(4, s // 6))
    elif x["bentuk"] == "silinder":
        p = kerucut(x["r"], x["r"], x["h"], max(12, s))
    elif x["bentuk"] == "kerucut":
        p = kerucut(x["r"], x["r2"], x["h"], max(12, s))
    elif x["bentuk"] == "bola":
        p = bola(x["r"], max(12, min(s, 48)))
    else:
        p = prisma(x["titik"], x["h"])
    return _ubah(p, x["rot"], x["pos"])


def bangun(resep):
    """Resep -> (poligon, laporan). Laporan berisi ukuran nyata + peringatan untuk diperiksa AI dan manusia."""
    bagian = rapikan(resep)
    hasil, catatan, peringatan = [], [], []
    for n, x in enumerate(bagian, 1):
        p = _bentuk(x)
        v_awal, kb = volume(hasil), kotak_batas(hasil)
        if x["operasi"] == "tambah":
            hasil = gabung(hasil, p)
        else:
            hasil = kurang(hasil, p)
        dv = volume(hasil) - v_awal
        catatan.append({"no": n, "nama": x["nama"], "bentuk": x["bentuk"], "operasi": x["operasi"], "ubah_volume_mm3": round(dv, 1)})
        if x["operasi"] == "potong" and dv > -0.5:
            peringatan.append(f"Bagian {n} ({x['nama']}) tidak memotong apa pun: posisinya di luar benda.")
        if x["operasi"] == "tambah" and n > 1 and dv < 0.5:
            peringatan.append(f"Bagian {n} ({x['nama']}) tidak menambah apa pun: seluruhnya sudah di dalam benda.")
        if x["operasi"] == "tambah" and n > 1 and not _kotak_tumpang(kotak_batas(p), kb, -0.01):
            peringatan.append(f"Bagian {n} ({x['nama']}) melayang, tidak menempel ke benda lain.")
        if x["operasi"] == "potong" and x["bentuk"] == "kotak" and not any(x["rot"]):
            # rongga kotak di dalam kotak: ukur tebal alas dan dinding yang tersisa
            ca = [x["pos"][i] - (x["sx"], x["sy"], x["sz"])[i] / 2 for i in range(3)]
            cb = [x["pos"][i] + (x["sx"], x["sy"], x["sz"])[i] / 2 for i in range(3)]
            for y in bagian[:n - 1]:
                if y["operasi"] != "tambah" or y["bentuk"] != "kotak" or any(y["rot"]):
                    continue
                ba = [y["pos"][i] - (y["sx"], y["sy"], y["sz"])[i] / 2 for i in range(3)]
                bb = [y["pos"][i] + (y["sx"], y["sy"], y["sz"])[i] / 2 for i in range(3)]
                if not all(ba[i] < x["pos"][i] < bb[i] for i in range(3)):
                    continue
                for i, nm in enumerate(("dinding kiri/kanan (X)", "dinding depan/belakang (Y)", "alas/tutup (Z)")):
                    for tebal in (ca[i] - ba[i], bb[i] - cb[i]):
                        if 0.01 < tebal < 1.6:
                            peringatan.append(f"Bagian {n} ({x['nama']}) menyisakan {nm} setebal {tebal:.2g} mm di {y['nama']}; minimal 1,6 mm supaya kuat dicetak.")
    if not hasil:
        raise ValueError("Model kosong: semua bagian terpotong habis")
    lo, hi = kotak_batas(hasil)
    hasil = [(([(v[0], v[1], v[2] - lo[2]) for v in vs]), (nw[0], nw[1] - nw[0][2] * lo[2])) for vs, nw in hasil]   # duduk di alas cetak (z = 0)
    hasil = rapat(hasil)
    vol = volume(hasil)
    if vol <= 1:
        raise ValueError("Model kosong: volumenya nol")
    uk = [round(hi[i] - lo[i], 2) for i in range(3)]
    if min(uk) < 0.8:
        peringatan.append(f"Ada sisi setipis {min(uk):g} mm; printer FDM biasanya butuh minimal 0,8 mm.")
    if max(uk) > 220:
        peringatan.append(f"Sisi terpanjang {max(uk):g} mm melebihi alas cetak umum (220 mm).")
    lap = {"ukuran_mm": {"panjang_x": uk[0], "lebar_y": uk[1], "tinggi_z": uk[2]}, "volume_cm3": round(vol / 1000.0, 2),
           "segitiga": sum(len(p[0]) - 2 for p in hasil), "bagian": catatan, "peringatan": peringatan[:12]}
    return hasil, lap


def _kipas(g, nw, titik):
    """Pecah satu poligon cembung (indeks) jadi segitiga; kalau ada titik segaris, kipas dari titik tengah."""
    n = nw[0]
    if len(g) == 3:
        return [(g[0], g[1], g[2], nw)]
    P = [titik[i] for i in g]
    segaris = any(abs(_dot(n, _cross(_sub(P[k], P[k - 1]), _sub(P[(k + 1) % len(P)], P[k])))) < 1e-9 for k in range(len(P)))
    if not segaris:
        return [(g[0], g[k], g[k + 1], nw) for k in range(1, len(g) - 1)]
    titik.append((sum(v[0] for v in P) / len(P), sum(v[1] for v in P) / len(P), sum(v[2] for v in P) / len(P)))
    c = len(titik) - 1
    return [(c, g[k], g[(k + 1) % len(g)], nw) for k in range(len(g))]


def _susun_bidang(grup, titik):
    """Gabungkan semua pecahan di satu bidang, ambil garis tepinya (luar + lubang), lalu ear clipping.
    Kembalikan None kalau tepinya tidak sederhana; pemanggil memakai pecahan asli."""
    if len(grup) == 1 and len(grup[0][0]) == 3:
        g, nw = grup[0]
        return [(g[0], g[1], g[2], nw)]
    nw = grup[0][1]
    n = nw[0]
    hit = {}
    for g, _nw in grup:
        for k in range(len(g)):
            e = (g[k], g[(k + 1) % len(g)])
            if e[0] == e[1]:
                continue
            if (e[1], e[0]) in hit:
                hit[(e[1], e[0])] -= 1
                if not hit[(e[1], e[0])]:
                    del hit[(e[1], e[0])]
            else:
                hit[e] = hit.get(e, 0) + 1
    if any(v != 1 for v in hit.values()):
        return None
    nxt = {}
    for a, b in hit:
        if a in nxt:
            return None
        nxt[a] = b
    # proyeksi ke 2D dengan arah yang menjaga putaran (berlawanan jarum jam = searah normal)
    ax = max(range(3), key=lambda i: abs(n[i]))
    u, v = [(1, 2), (2, 0), (0, 1)][ax]
    if n[ax] < 0:
        u, v = v, u
    xy = lambda i: (titik[i][u], titik[i][v])
    loops, sisa = [], set(nxt)
    while sisa:
        st = sisa.pop()
        L, c = [st], nxt[st]
        while c != st:
            if c not in sisa:
                return None
            sisa.discard(c)
            L.append(c)
            c = nxt.get(c)
            if c is None:
                return None
        if len(L) >= 3:
            loops.append(L)
    luas = lambda L: sum(xy(L[k])[0] * xy(L[(k + 1) % len(L)])[1] - xy(L[(k + 1) % len(L)])[0] * xy(L[k])[1] for k in range(len(L))) / 2
    luar = [L for L in loops if luas(L) > 1e-9]
    lubang = [L for L in loops if luas(L) < -1e-9]
    if not luar:
        return None
    def dalam(pt, L):
        x, y, ins = pt[0], pt[1], False
        for k in range(len(L)):
            a, b = xy(L[k]), xy(L[k - 1])
            if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                ins = not ins
        return ins
    milik = {id(L): [] for L in luar}
    for H in lubang:
        p0 = xy(H[0])
        cal = [L for L in luar if dalam(p0, L)]
        if not cal:
            return None
        milik[id(min(cal, key=luas))].append(H)
    luas_asli = 0.0
    for g, _nw in grup:
        P = [titik[i] for i in g]
        luas_asli += abs(_dot(n, _cross(_sub(P[1], P[0]), _sub(P[2], P[0])))) / 2 if len(P) == 3 else abs(sum(_dot(n, _cross(_sub(P[k], P[0]), _sub(P[k + 1], P[0]))) for k in range(1, len(P) - 1))) / 2
    out = []
    for L in luar:
        poly = _jembatan(list(L), milik[id(L)], xy)
        if poly is None:
            return None
        t = _telinga_idx(poly, xy)
        if t is None:
            return None
        out.extend((a, b, c, nw) for a, b, c in t)
    luas_baru = sum(abs(_dot(n, _cross(_sub(titik[b], titik[a]), _sub(titik[c], titik[a])))) / 2 for a, b, c, _ in out)
    if abs(luas_baru - luas_asli) > 1e-6 + 1e-6 * luas_asli:
        return None
    return out


def _potong2(p1, p2, q1, q2):
    """Apakah ruas p1-p2 dan q1-q2 berpotongan sejati (bukan hanya bersentuhan di ujung)?"""
    d = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = d(q1, q2, p1), d(q1, q2, p2), d(p1, p2, q1), d(p1, p2, q2)
    return ((d1 > 1e-12 and d2 < -1e-12) or (d1 < -1e-12 and d2 > 1e-12)) and ((d3 > 1e-12 and d4 < -1e-12) or (d3 < -1e-12 and d4 > 1e-12))


def _jembatan(outer, holes, xy):
    """Sambungkan tiap lubang ke tepi luar dengan sepasang sisi kembar sehingga jadi satu poligon."""
    for H in sorted(holes, key=lambda H: -max(xy(i)[0] for i in H)):
        m = max(range(len(H)), key=lambda k: (xy(H[k])[0], xy(H[k])[1]))
        M = xy(H[m])
        semua = [(outer[k], outer[(k + 1) % len(outer)]) for k in range(len(outer))]
        for H2 in holes:
            semua += [(H2[k], H2[(k + 1) % len(H2)]) for k in range(len(H2))]
        calon = sorted(range(len(outer)), key=lambda k: (xy(outer[k])[0] - M[0]) ** 2 + (xy(outer[k])[1] - M[1]) ** 2)
        pilih = None
        for k in calon:
            P = xy(outer[k])
            if P == M:
                continue
            if any(_potong2(M, P, xy(a), xy(b)) for a, b in semua):
                continue
            # jembatan juga tidak boleh melewati titik lain (titik segaris di tengah jembatan = T-junction baru)
            dx, dy, L2 = P[0] - M[0], P[1] - M[1], (P[0] - M[0]) ** 2 + (P[1] - M[1]) ** 2
            lewat = False
            for a, _b in semua:
                q = xy(a)
                if q == M or q == P:
                    continue
                t = ((q[0] - M[0]) * dx + (q[1] - M[1]) * dy) / L2
                if 0 < t < 1 and abs((q[0] - M[0]) * dy - (q[1] - M[1]) * dx) <= 1e-9 * (1 + L2):
                    lewat = True
                    break
            if lewat:
                continue
            # pastikan jembatan masuk ke dalam sudut poligon luar di titik k
            a, b = xy(outer[k - 1]), xy(outer[(k + 1) % len(outer)])
            cr = lambda o, p, q: (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
            if cr(a, P, b) >= 0:        # sudut cembung: M harus di antara kedua sisi
                if not (cr(P, b, M) > 0 and cr(a, P, M) > 0):
                    continue
            else:                       # sudut cekung: M asal tidak di luar keduanya
                if cr(P, b, M) <= 0 and cr(a, P, M) <= 0:
                    continue
            pilih = k
            break
        if pilih is None:
            return None
        outer = outer[:pilih + 1] + H[m:] + H[:m + 1] + outer[pilih:]
    return outer


def _telinga_idx(poly, xy):
    """Ear clipping untuk poligon sederhana (boleh ada titik kembar dari jembatan dan titik segaris)."""
    idx = list(poly)
    out = []
    cr = lambda o, p, q: (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    jaga = 0
    while len(idx) > 3:
        jaga += 1
        if jaga > 20 * len(poly) + 100:
            return None
        m, ketemu = len(idx), False
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = xy(i0), xy(i1), xy(i2)
            if cr(a, b, c) <= 1e-12:
                continue
            bersih = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = xy(j)
                if p in (a, b, c):
                    continue
                if cr(a, b, p) >= -1e-12 and cr(b, c, p) >= -1e-12 and cr(c, a, p) >= -1e-12:
                    bersih = False
                    break
            if not bersih:
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            ketemu = True
            break
        if not ketemu:
            return None
    a, b, c = (xy(i) for i in idx)
    if cr(a, b, c) > 1e-12:
        out.append(tuple(idx))
    elif abs(cr(a, b, c)) > 1e-12:
        return None
    return out


# ---------- rapikan jaring: tanpa ini STL "bocor" di sambungan (T-junction) dan Tinkercad/slicer menampilkannya robek ----------
def rapat(polis, toleransi=1e-5):
    """Satukan titik kembar, sisipkan titik yang jatuh di tengah sisi poligon tetangga (T-junction),
    lalu pecah jadi segitiga. Hasil: tiap sisi segitiga punya tepat satu pasangan (watertight)."""
    # titik yang berjarak < toleransi (0,00001 mm: sisa galat hitung) disatukan, termasuk berantai (A~B~C jadi satu)
    kunci = lambda v: (int(math.floor(v[0] / toleransi)), int(math.floor(v[1] / toleransi)), int(math.floor(v[2] / toleransi)))
    t2 = toleransi * toleransi
    mentah, mid = [], {}
    for vs, _nw in polis:
        for v in vs:
            if v not in mid:
                mid[v] = len(mentah)
                mentah.append(v)
    induk = list(range(len(mentah)))
    def akar(i):
        while induk[i] != i:
            induk[i] = induk[induk[i]]
            i = induk[i]
        return i
    grid = {}
    for i, v in enumerate(mentah):
        kx, ky, kz = kunci(v)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((kx + dx, ky + dy, kz + dz), ()):
                        w = mentah[j]
                        if (w[0] - v[0]) ** 2 + (w[1] - v[1]) ** 2 + (w[2] - v[2]) ** 2 < t2:
                            ra, rb = akar(i), akar(j)
                            if ra != rb:
                                induk[max(ra, rb)] = min(ra, rb)
        grid.setdefault((kx, ky, kz), []).append(i)
    titik, idx = [], {}
    def id_(v):
        r = akar(mid[v])
        if r not in idx:
            idx[r] = len(titik)
            titik.append(mentah[r])
        return idx[r]
    muka = []
    for vs, nw in polis:
        f = []
        for v in vs:
            i = id_(v)
            if not f or f[-1] != i:
                f.append(i)
        while len(f) > 1 and f[0] == f[-1]:
            f.pop()
        if len(f) >= 3:
            muka.append((f, nw))
    # sel ruang 4 mm untuk mencari titik di dekat sebuah sisi dengan cepat
    SEL = 4.0
    sel = {}
    for i, v in enumerate(titik):
        sel.setdefault((int(v[0] // SEL), int(v[1] // SEL), int(v[2] // SEL)), []).append(i)
    tol2 = (toleransi * 2) ** 2   # titik T-junction asli dari BSP tepat di sisi (galat ~1e-12)
    def di_sisi(a, b):
        A, B = titik[a], titik[b]
        d = _sub(B, A)
        L2 = _dot(d, d)
        if L2 < 1e-12:
            return []
        lo = [int(min(A[k], B[k]) // SEL) - 1 for k in range(3)]
        hi = [int(max(A[k], B[k]) // SEL) + 1 for k in range(3)]
        out = []
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    for i in sel.get((x, y, z), ()):
                        if i == a or i == b:
                            continue
                        P = titik[i]
                        w = _sub(P, A)
                        t = _dot(w, d) / L2
                        if t <= 1e-7 or t >= 1 - 1e-7:
                            continue
                        q = (w[0] - d[0] * t, w[1] - d[1] * t, w[2] - d[2] * t)
                        if _dot(q, q) < tol2:
                            out.append((t, i))
        out.sort()
        return [i for _t, i in out]
    cache = {}
    sisi = []         # poligon (indeks) yang sudah disisipi titik T-junction
    for f, nw in muka:
        g = []
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            if (a, b) not in cache:
                cache[(a, b)] = cache[(b, a)][::-1] if (b, a) in cache else di_sisi(a, b)
            g.append(a)
            g.extend(cache[(a, b)])
        sisi.append((g, nw))
    # tiap bidang datar disusun ulang: pecahan-pecahan BSP digabung, lalu dipecah ulang jadi segitiga yang rapi (tanpa serpihan tipis)
    kel = {}
    for g, nw in sisi:
        n, w = nw
        kel.setdefault((round(n[0], 4), round(n[1], 4), round(n[2], 4), round(w, 3)), []).append((g, nw))
    tri = []
    for grup in kel.values():
        hasil_g = _susun_bidang(grup, titik)
        if hasil_g is None:      # bentuk bidang rumit (mis. sudut bertemu di satu titik): pakai pecahan asli
            hasil_g = []
            for g, nw in grup:
                hasil_g.extend(_kipas(g, nw, titik))
        tri.extend(hasil_g)
    # buang segitiga gepeng dan muka ganda: dua muka sama arah = sisa potongan, dua muka berlawanan = dinding dalam yang saling meniadakan
    hit, simpan = {}, []
    for a, b, c, nw in tri:
        if len({a, b, c}) < 3:
            continue
        x = _cross(_sub(titik[b], titik[a]), _sub(titik[c], titik[a]))
        if _dot(x, x) < 1e-14:
            continue
        m = min(a, b, c)
        urut = (a, b, c) if m == a else (b, c, a) if m == b else (c, a, b)
        balik = (urut[0], urut[2], urut[1])
        if urut in hit:
            continue
        if balik in hit:
            simpan[hit.pop(balik)] = None
            continue
        hit[urut] = len(simpan)
        simpan.append((a, b, c, nw))
    simpan = [t for t in simpan if t is not None]
    # buang serpihan melayang: segitiga yang tak satu pun sisinya bersambung dengan segitiga lain
    while True:
        sisi_ = {}
        for a, b, c, _nw in simpan:
            for e in ((a, b), (b, c), (c, a)):
                sisi_[e] = sisi_.get(e, 0) + 1
        baru = [t for t in simpan if any((y, x) in sisi_ for x, y in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])))]
        if len(baru) == len(simpan):
            break
        simpan = baru
    # tambal lubang kecil: sisi tanpa pasangan yang membentuk lingkaran tertutup pendek diisi segitiga (arah dibalik)
    lepas = {}
    for e in sisi_:
        if (e[1], e[0]) not in sisi_:
            lepas.setdefault(e[0], []).append(e[1])
    if lepas and all(len(v) == 1 for v in lepas.values()):
        sisa_ = set(lepas)
        while sisa_:
            st = sisa_.pop()
            L, c = [st], lepas[st][0]
            while c != st and c in sisa_ and len(L) <= 24:
                sisa_.discard(c)
                L.append(c)
                c = lepas[c][0]
            if c != st or len(L) < 3 or len(L) > 24:
                continue
            P = [titik[i] for i in L[::-1]]
            nw = _bidang(P)
            for k in range(1, len(L) - 1):
                simpan.append((L[0], L[-k], L[-k - 1], nw))
    return [([titik[a], titik[b], titik[c]], nw) for a, b, c, nw in simpan]


def stl(polis, nama="MCI"):
    tri = list(segitiga(polis))
    out = [(f"MCI Studio 3D - {nama}"[:79]).encode("ascii", "replace").ljust(80, b" "), struct.pack("<I", len(tri))]
    for n, a, b, c in tri:
        out.append(struct.pack("<12fH", n[0], n[1], n[2], a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2], 0))
    return b"".join(out)


def pratinjau(polis, lebar=520, tinggi=400):
    """Gambar isometrik sederhana (SVG) supaya bentuknya bisa dicek sebelum mencetak."""
    az, el = math.radians(35), math.radians(28)
    ca, sa, ce, se = math.cos(az), math.sin(az), math.cos(el), math.sin(el)
    def pr(v):
        x, y, z = v
        x, y = x * ca + y * sa, -x * sa + y * ca
        return (x, y * se + z * ce, y * ce - z * se)       # (layar x, layar y ke atas, kedalaman)
    cahaya = _unit((0.35, -0.5, 0.8))
    muka = []
    for vs, (n, _w) in polis:
        ps = [pr(v) for v in vs]
        nd = pr(n)[2]
        if nd > 0.02:      # menghadap menjauhi kamera
            continue
        muka.append((sum(p[2] for p in ps) / len(ps), ps, max(0.0, _dot(n, cahaya))))
    if not muka:
        return ""
    xs = [p[0] for _d, ps, _s in muka for p in ps]
    ys = [p[1] for _d, ps, _s in muka for p in ps]
    sk = min((lebar - 40) / max(1e-6, max(xs) - min(xs)), (tinggi - 40) / max(1e-6, max(ys) - min(ys)))
    ox, oy = (lebar - (max(xs) + min(xs)) * sk) / 2, (tinggi + (max(ys) + min(ys)) * sk) / 2
    muka.sort(key=lambda m: -m[0])
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lebar} {tinggi}" role="img" aria-label="Pratinjau model 3D"><rect width="{lebar}" height="{tinggi}" fill="#F4F1EA"/>']
    for _d, ps, s in muka[:9000]:
        g = 0.38 + 0.62 * s
        warna = f"#{int(46 + 150 * g):02x}{int(74 + 140 * g):02x}{int(120 + 120 * g):02x}"
        pts = " ".join(f"{ox + p[0] * sk:.1f},{oy - p[1] * sk:.1f}" for p in ps)
        out.append(f'<polygon points="{pts}" fill="{warna}" stroke="{warna}" stroke-width="0.6" stroke-linejoin="round"/>')
    out.append("</svg>")
    return "".join(out)


# ---------- SVG dari AI: saring dulu ----------
SVG_TAG = {"svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "textPath",
           "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "symbol", "use", "marker", "filter", "feGaussianBlur", "feOffset",
           "feMerge", "feMergeNode", "feColorMatrix", "feBlend", "feFlood", "feComposite", "feDropShadow", "feTurbulence", "feDisplacementMap"}
SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"


def svg_bersih(teks, maks=300_000):
    """Kembalikan (svg aman, error). Membuang skrip, event, tautan luar, dan tag yang tidak dikenal."""
    t = str(teks or "").strip()
    m = re.search(r"<svg\b[\s\S]*</svg>", t, re.I)
    if not m:
        return None, "AI tidak menghasilkan SVG"
    t = m.group(0)
    if len(t) > maks:
        return None, "SVG terlalu besar"
    if re.search(r"<!(DOCTYPE|ENTITY)", t, re.I):
        return None, "SVG memuat DOCTYPE/ENTITY"
    if "xmlns=" not in t[:400]:
        t = t.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1)
    if "xlink:" in t and "xmlns:xlink" not in t[:600]:
        t = t.replace("<svg", f'<svg xmlns:xlink="{XLINK}"', 1)
    try:
        root = ET.fromstring(t)
    except ET.ParseError as e:
        return None, f"SVG rusak: {e}"
    lokal = lambda s: s.split("}", 1)[-1]
    if lokal(root.tag) != "svg":
        return None, "Akar dokumen bukan <svg>"
    jahat = re.compile(r"javascript:|data:|expression\(|@import|url\(\s*['\"]?\s*(?!#)", re.I)
    n_el = 0
    def bersih(el):
        nonlocal n_el
        for ch in list(el):
            if not isinstance(ch.tag, str) or lokal(ch.tag) not in SVG_TAG or (ch.tag.startswith("{") and not ch.tag.startswith("{" + SVG_NS + "}")):
                el.remove(ch)
                continue
            bersih(ch)
        n_el += 1
        for k in list(el.attrib):
            nm, v = lokal(k).lower(), el.attrib[k]
            if nm.startswith("on") or (nm == "href" and not v.strip().startswith("#")) or jahat.search(v) and nm != "d":
                del el.attrib[k]
        if lokal(el.tag) in ("text", "tspan", "title", "desc", "textPath"):
            return
        el.text = None if lokal(el.tag) != "svg" else el.text
    bersih(root)
    if n_el < 2:
        return None, "SVG kosong"
    if not root.get("viewBox"):
        w, h = re.sub(r"[^\d.]", "", root.get("width", "")), re.sub(r"[^\d.]", "", root.get("height", ""))
        root.set("viewBox", f"0 0 {w or 800} {h or 600}")
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK)
    return ET.tostring(root, encoding="unicode"), None
