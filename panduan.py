"""Buku Panduan Rakit MCI: peta pin board, pemeriksa sambungan, dan penggambar diagram breadboard (SVG).

AI hanya menulis "resep" panduan dalam bentuk data (komponen, pin, sambungan per langkah).
Modul ini yang memeriksa resep itu terhadap peta pin board yang sebenarnya, lalu menggambar
diagramnya dengan kode. Jadi gambar selalu sama persis dengan daftar sambungan, dan kesalahan
umum (pin tidak ada, 5V masuk ke pin ESP32, lupa GND, satu pin dipakai dua sinyal) ketahuan
sebelum panduan sampai ke anggota.

Format ujung sambungan: "PAPAN:<pin>" (contoh "PAPAN:GPIO5", "PAPAN:D9", "PAPAN:5V")
atau "<id komponen>:<nama pin>" (contoh "hcsr04:TRIG").
"""
import re
from html import escape as _esc

# ---------- PETA PIN BOARD ----------
# Setiap pin: tipe (gpio / daya / gnd / lain), tegangan logika, dan kemampuan.
_ESP32_GPIO = [0, 1, 2, 3, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 39]
_ESP32_ADC1 = {32, 33, 34, 35, 36, 39}
_ESP32_ADC2 = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27}


def _papan_esp32():
    pin = {}
    for g in _ESP32_GPIO:
        pin[f"GPIO{g}"] = {"tipe": "gpio", "no": g, "masuk_saja": g in (34, 35, 36, 39), "analog": g in _ESP32_ADC1 | _ESP32_ADC2,
                           "adc2": g in _ESP32_ADC2, "pwm": g not in (34, 35, 36, 39)}
    for g in range(6, 12):
        pin[f"GPIO{g}"] = {"tipe": "terlarang", "no": g}
    pin.update({"3V3": {"tipe": "daya", "volt": 3.3}, "5V": {"tipe": "daya", "volt": 5.0}, "GND": {"tipe": "gnd"}, "EN": {"tipe": "lain"}})
    return {"id": "esp32", "nama": "ESP32 DevKit V4", "logika": 3.3, "pin": pin, "sda": "GPIO21", "scl": "GPIO22",
            "serial": {"GPIO1", "GPIO3"}, "strapping": {"GPIO0": "menentukan mode boot", "GPIO2": "harus LOW saat boot", "GPIO12": "kalau HIGH saat boot, ESP32 gagal menyala"}}


def _papan_avr(nama, analog_n, idp):
    pin = {}
    pwm = {3, 5, 6, 9, 10, 11}
    for d in range(14):
        pin[f"D{d}"] = {"tipe": "gpio", "no": d, "masuk_saja": False, "analog": False, "pwm": d in pwm}
    for a in range(analog_n):
        hanya = a >= 6   # A6/A7 di Nano: analog saja, tidak bisa digital
        pin[f"A{a}"] = {"tipe": "gpio", "no": 14 + a, "masuk_saja": hanya, "analog": True, "pwm": False, "analog_saja": hanya}
    pin.update({"5V": {"tipe": "daya", "volt": 5.0}, "3V3": {"tipe": "daya", "volt": 3.3}, "VIN": {"tipe": "daya", "volt": 7.0, "vin": True},
                "GND": {"tipe": "gnd"}, "RST": {"tipe": "lain"}, "AREF": {"tipe": "lain"}})
    return {"id": idp, "nama": nama, "logika": 5.0, "pin": pin, "sda": "A4", "scl": "A5", "serial": {"D0", "D1"}, "strapping": {}}


PAPAN = {"esp32": _papan_esp32(), "nano": _papan_avr("Arduino Nano", 8, "nano"), "uno": _papan_avr("Arduino Uno", 6, "uno")}


def normal_pin(papan, nama):
    """Samakan penulisan pin: 'D5', 'IO5', '5', 'G5' -> GPIO5 (ESP32); 'D05', 'PIN 9' -> D9 (AVR); '3.3V' -> 3V3, dst."""
    t = str(nama or "").strip().upper().replace(" ", "").replace("-", "").replace("_", "")
    t = {"3.3V": "3V3", "3V": "3V3", "3.3": "3V3", "VCC": "5V", "+5V": "5V", "5.0V": "5V", "VUSB": "5V", "GROUND": "GND", "G": "GND"}.get(t, t)
    if papan == "esp32":
        t = {"VP": "GPIO36", "VN": "GPIO39", "SVP": "GPIO36", "SVN": "GPIO39", "VIN": "5V", "TX": "GPIO1", "RX": "GPIO3", "TX0": "GPIO1", "RX0": "GPIO3"}.get(t, t)
        m = re.fullmatch(r"(?:GPIO|IO|D|G|P|PIN)?0*(\d{1,2})", t)
        if m:
            return f"GPIO{int(m.group(1))}"
        return t
    m = re.fullmatch(r"(?:D|PIN|P)?0*(\d{1,2})", t)
    if m and int(m.group(1)) <= 13:
        return f"D{int(m.group(1))}"
    m = re.fullmatch(r"A0*(\d)", t)
    if m:
        return f"A{int(m.group(1))}"
    return {"SDA": "A4", "SCL": "A5", "TX": "D1", "RX": "D0"}.get(t, t)


def _ujung(s):
    s = str(s or "").strip()
    if ":" not in s:
        return None, None
    a, b = s.split(":", 1)
    return a.strip(), b.strip()


def _float(v, bawaan=0.0):
    try:
        return float(str(v).replace(",", ".").replace("V", "").replace("v", ""))
    except (TypeError, ValueError):
        return bawaan


def _volt_rentang(t):
    """'5' -> (5,5); '3.3-5' -> (3.3,5); kosong -> None."""
    t = str(t or "").replace(",", ".").replace("V", "").replace("v", "").strip()
    if not t:
        return None
    angka = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if not angka:
        return None
    return (min(angka), max(angka))


BERBAHAYA = re.compile(r"\b(220\s*v|230\s*v|110\s*v|listrik\s*(pln|rumah)|tegangan\s*tinggi|stop\s*kontak|colokan\s*listrik|mains|ac\s*220)\b", re.I)


def semua_komponen(data):
    out = {}
    for k in data.get("kantong") or []:
        for x in k.get("komponen") or []:
            if x.get("id"):
                out[str(x["id"]).strip()] = x
    return out


def periksa(data):
    """Periksa resep panduan. Balik (galat, peringatan): galat wajib diperbaiki AI, peringatan ditampilkan ke anggota."""
    galat, waspada = [], []
    pid = str(data.get("papan") or "").lower()
    if pid not in PAPAN:
        return [f"Papan '{data.get('papan')}' tidak dikenal. Pakai salah satu: esp32, nano, uno."], []
    P = PAPAN[pid]
    komp = semua_komponen(data)
    if not komp:
        galat.append("Daftar komponen (kantong) kosong.")
    teks_semua = " ".join([str(data.get("judul", "")), str(data.get("ringkasan", ""))] + [str(x.get("nama", "")) for x in komp.values()])
    if BERBAHAYA.search(teks_semua):
        galat.append("Panduan menyentuh listrik tegangan tinggi (PLN/220V). Ini dilarang demi keselamatan; ganti dengan rangkaian tegangan rendah.")
    pinkomp = {}
    for kid, x in komp.items():
        pinkomp[kid] = {}
        for p in x.get("pin") or []:
            nm = str(p.get("nama", "")).strip().upper()
            if nm:
                pinkomp[kid][nm] = p
    pakai_gpio = {}       # pin board -> [(komponen, pin, bus)]
    terhubung = set()     # (komponen, PIN) yang punya sambungan
    gnd_papan = set()     # komponen yang GND-nya nyambung ke GND board (langsung/lewat komponen lain dicek sederhana)
    sambung_komp = {}     # komponen -> set komponen tetangga
    led_langsung = set()
    nomor = 0
    for li, lg in enumerate(data.get("langkah") or [], 1):
        for s in lg.get("sambungan") or []:
            nomor += 1
            a, b = _ujung(s.get("dari")), _ujung(s.get("ke"))
            label = f"Langkah {li}, sambungan {s.get('dari')} ke {s.get('ke')}"
            if not a[0] or not b[0]:
                galat.append(f"{label}: format ujung harus 'PAPAN:pin' atau 'idkomponen:pin'.")
                continue
            ujung = []
            for sisi in (a, b):
                if sisi[0].upper() == "PAPAN":
                    nm = normal_pin(pid, sisi[1])
                    info = P["pin"].get(nm)
                    if not info:
                        galat.append(f"{label}: pin {sisi[1]} tidak ada di {P['nama']}.")
                        info = None
                    elif info["tipe"] == "terlarang":
                        galat.append(f"{label}: {nm} dipakai flash internal ESP32 dan tidak boleh disambung.")
                        info = None
                    ujung.append(("PAPAN", nm, info))
                else:
                    kid = sisi[0].strip()
                    if kid not in komp:
                        galat.append(f"{label}: komponen '{kid}' tidak ada di daftar kantong.")
                        ujung.append(None)
                        continue
                    pn = sisi[1].strip().upper()
                    pk = pinkomp[kid].get(pn)
                    if not pk:
                        galat.append(f"{label}: pin '{sisi[1]}' tidak ada di komponen {komp[kid].get('nama', kid)}.")
                        ujung.append(None)
                        continue
                    terhubung.add((kid, pn))
                    ujung.append((kid, pn, pk))
            if None in ujung or len(ujung) != 2:
                continue
            u1, u2 = ujung
            if u1[0] == "PAPAN" and u2[0] == "PAPAN":
                galat.append(f"{label}: dua pin board disambung langsung (bisa korsleting).")
                continue
            if u1[0] != "PAPAN" and u2[0] != "PAPAN":
                sambung_komp.setdefault(u1[0], set()).add(u2[0])
                sambung_komp.setdefault(u2[0], set()).add(u1[0])
                j1, j2 = str(u1[2].get("jenis", "")).lower(), str(u2[2].get("jenis", "")).lower()
                if {j1, j2} == {"daya", "gnd"}:
                    galat.append(f"{label}: pin daya disambung ke GND (korsleting).")
                continue
            papan_u, komp_u = (u1, u2) if u1[0] == "PAPAN" else (u2, u1)
            _, nm, info = papan_u
            kid, pn, pk = komp_u
            if info is None:
                continue
            jenis = str(pk.get("jenis", "")).lower()
            nkomp = komp[kid].get("nama", kid)
            if info["tipe"] == "gnd":
                if jenis == "daya":
                    galat.append(f"{label}: pin daya {nkomp} ({pn}) disambung ke GND (korsleting).")
                gnd_papan.add(kid)
                continue
            if info["tipe"] == "daya":
                if jenis == "gnd":
                    galat.append(f"{label}: GND {nkomp} disambung ke pin daya {nm} (korsleting).")
                    continue
                if jenis in ("sinyal",):
                    galat.append(f"{label}: pin sinyal {nkomp} ({pn}) disambung langsung ke {nm}. Sinyal harus ke pin GPIO.")
                    continue
                if jenis == "daya":
                    r = _volt_rentang(pk.get("tegangan"))
                    v = info.get("volt", 0)
                    if info.get("vin"):
                        waspada.append(f"{nkomp} diberi daya dari VIN. VIN hanya berisi tegangan kalau board diberi catu eksternal 7-12V.")
                    elif r and v > r[1] + 0.25:
                        galat.append(f"{label}: {nkomp} butuh {pk.get('tegangan')}V tetapi disambung ke {nm} ({v}V). Komponen bisa rusak.")
                    elif r and v < r[0] - 0.25:
                        galat.append(f"{label}: {nkomp} butuh {pk.get('tegangan')}V tetapi {nm} hanya {v}V. Komponen tidak akan bekerja.")
                continue
            if info["tipe"] != "gpio":
                galat.append(f"{label}: pin {nm} bukan pin GPIO.")
                continue
            if jenis in ("daya", "gnd"):
                galat.append(f"{label}: pin {jenis.upper()} {nkomp} ({pn}) disambung ke GPIO {nm}. Daya harus ke 5V/3V3, GND ke GND.")
                continue
            bus = str(pk.get("bus", "") or "").lower()
            pakai_gpio.setdefault(nm, []).append((kid, pn, bus))
            arah = str(pk.get("arah", "")).lower()
            if arah == "masuk" and info.get("masuk_saja"):
                galat.append(f"{label}: {nm} hanya bisa membaca (input), tidak bisa mengirim sinyal ke {nkomp} ({pn}).")
            if pk.get("analog") and not info.get("analog"):
                galat.append(f"{label}: {nkomp} ({pn}) butuh pin analog, tetapi {nm} bukan pin analog.")
            if pk.get("analog") and info.get("adc2"):
                waspada.append(f"{nm} adalah ADC2: pembacaan analognya tidak bisa dipakai saat WiFi ESP32 menyala. Pakai GPIO32-39 kalau proyek memakai WiFi.")
            if info.get("analog_saja") and not pk.get("analog"):
                galat.append(f"{label}: {nm} di {P['nama']} hanya bisa membaca analog, tidak bisa dipakai sebagai pin digital.")
            if pk.get("pwm") and not info.get("pwm"):
                galat.append(f"{label}: {nkomp} ({pn}) butuh PWM, tetapi {nm} bukan pin PWM. Di {P['nama']} pin PWM: "
                             + ("semua GPIO kecuali 34-39." if pid == "esp32" else "D3, D5, D6, D9, D10, D11."))
            lvl = _float(pk.get("level"), 0)
            if s.get("pembagi_tegangan") and not any(re.search(r"resist|hambatan|ohm|Ω|level\s*shift|logic\s*level", str(x.get("nama", "")), re.I) for x in komp.values()):
                galat.append(f"{label}: ditandai lewat pembagi tegangan, tetapi resistor atau modul level shifter belum ada di daftar komponen.")
            if arah == "keluar" and P["logika"] < 4 and lvl >= 4.5 and not s.get("pembagi_tegangan"):
                galat.append(f"{label}: {nkomp} ({pn}) mengeluarkan sinyal 5V ke {nm}, padahal pin ESP32 hanya tahan 3,3V. "
                             "Tambahkan pembagi tegangan (mis. resistor 1kΩ + 2kΩ) dan tandai pembagi_tegangan=true.")
            if arah == "masuk" and P["logika"] < 4 and lvl >= 4.5:
                waspada.append(f"{nkomp} ({pn}) dirancang untuk sinyal 5V; dari ESP32 sinyalnya 3,3V. Biasanya tetap jalan, tetapi uji dulu.")
            if bus == "i2c_sda" and nm != P["sda"]:
                (waspada if pid == "esp32" else galat).append(f"{nkomp} SDA disambung ke {nm}; pin SDA bawaan {P['nama']} adalah {P['sda']}."
                                                                + (" Pastikan kode memanggil Wire.begin(SDA, SCL)." if pid == "esp32" else ""))
            if bus == "i2c_scl" and nm != P["scl"]:
                (waspada if pid == "esp32" else galat).append(f"{nkomp} SCL disambung ke {nm}; pin SCL bawaan {P['nama']} adalah {P['scl']}."
                                                                + (" Pastikan kode memanggil Wire.begin(SDA, SCL)." if pid == "esp32" else ""))
            if nm in P["serial"]:
                waspada.append(f"{nm} juga dipakai Serial (USB). Saat unggah kode atau membuka Serial Monitor, {nkomp} bisa terganggu.")
            if nm in P["strapping"]:
                waspada.append(f"{nm} adalah pin strapping ESP32 ({P['strapping'][nm]}). Kalau ESP32 susah menyala atau diunggah, pindahkan ke pin lain.")
            if "led" in nkomp.lower() and "rgb" not in nkomp.lower() and "strip" not in nkomp.lower() and "ws28" not in nkomp.lower():
                led_langsung.add(kid)
    for nm, isi in pakai_gpio.items():
        pemakai = {(k, p) for k, p, _ in isi}
        bus = {b for _, _, b in isi if b}
        if len(pemakai) > 1 and not (len(bus) == 1 and next(iter(bus)).startswith("i2c")):
            galat.append(f"Pin {nm} dipakai lebih dari satu sinyal: " + ", ".join(f"{komp[k].get('nama', k)} ({p})" for k, p in sorted(pemakai)) + ".")
    for kid, pins in pinkomp.items():
        nkomp = komp[kid].get("nama", kid)
        for pn, pk in pins.items():
            if str(pk.get("jenis", "")).lower() in ("daya", "gnd") and (kid, pn) not in terhubung:
                galat.append(f"Pin {pn} pada {nkomp} belum disambung. Setiap pin daya dan GND wajib tersambung.")
    for kid in led_langsung:
        tetangga = sambung_komp.get(kid, set())
        if not any(re.search(r"resist|hambatan|ohm|Ω", str(komp[t].get("nama", "")), re.I) for t in tetangga):
            waspada.append(f"{komp[kid].get('nama', kid)} tersambung langsung ke pin board. Pastikan ada resistor 220Ω seri supaya LED dan pin tidak rusak.")
    sumber = [kid for kid, pins in pinkomp.items() if any(str(p.get("jenis", "")).lower() == "sumber_gnd" for p in pins.values())]
    for kid in sumber:
        if not any(str(pinkomp[kid][pn].get("jenis", "")).lower() == "sumber_gnd" and (kid, pn) in terhubung for pn in pinkomp[kid]):
            galat.append(f"GND {komp[kid].get('nama', kid)} (catu eksternal) harus disambung ke GND board (ground bersama).")
    if not data.get("langkah"):
        galat.append("Panduan belum punya langkah.")
    if len(str(data.get("kode") or "").strip()) < 40:
        galat.append("Kode Arduino belum ditulis.")
    return list(dict.fromkeys(galat))[:25], list(dict.fromkeys(waspada))[:20]


# ---------- DIAGRAM BREADBOARD (SVG) ----------
WARNA = {"merah": "#D92D20", "hitam": "#1D2939", "kuning": "#E7B008", "hijau": "#12A150", "biru": "#1570EF", "oranye": "#F97316", "jingga": "#F97316",
         "ungu": "#7A5AF8", "putih": "#98A2B3", "coklat": "#8B5E34", "cokelat": "#8B5E34", "abu": "#667085", "abu-abu": "#667085", "pink": "#EE46BC", "cyan": "#06AED4"}


def _warna(w, ujung_papan=""):
    t = str(w or "").strip().lower()
    if t in WARNA:
        return WARNA[t]
    if ujung_papan == "GND":
        return WARNA["hitam"]
    if ujung_papan in ("5V", "3V3"):
        return WARNA["merah"]
    return WARNA["biru"]


def _urut_pin(p):
    pr = {"5V": 0, "VIN": 1, "3V3": 2, "GND": 3}
    if p in pr:
        return (0, pr[p], "")
    m = re.search(r"(\d+)", p)
    return (1 if p.startswith(("D", "GPIO")) else 2, int(m.group(1)) if m else 99, p)


def diagram(data, sampai=None, sorot=None):
    """Gambar rangkaian. sampai = indeks langkah terakhir yang ikut digambar (None = semua).
    sorot = indeks langkah yang kabelnya ditebalkan; kabel langkah sebelumnya dibuat pudar (seperti manual Lego)."""
    pid = str(data.get("papan") or "").lower()
    if pid not in PAPAN:
        return ""
    P = PAPAN[pid]
    komp = semua_komponen(data)
    langkah = data.get("langkah") or []
    batas = len(langkah) - 1 if sampai is None else sampai
    kabel = []
    for li, lg in enumerate(langkah):
        if li > batas:
            break
        for s in lg.get("sambungan") or []:
            a, b = _ujung(s.get("dari")), _ujung(s.get("ke"))
            if not a[0] or not b[0]:
                continue
            kabel.append((li, a, b, s.get("warna")))
    pin_papan, komp_dipakai, komp_baru = set(), [], set()
    pin_komp = {}
    for li, a, b, _ in kabel:
        for sisi in (a, b):
            if sisi[0].upper() == "PAPAN":
                pin_papan.add(normal_pin(pid, sisi[1]))
            elif sisi[0] in komp:
                if sisi[0] not in komp_dipakai:
                    komp_dipakai.append(sisi[0])
                    if sorot is not None and li == sorot:
                        komp_baru.add(sisi[0])
                pin_komp.setdefault(sisi[0], [])
                if sisi[1].upper() not in pin_komp[sisi[0]]:
                    pin_komp[sisi[0]].append(sisi[1].upper())
    for kid in komp_dipakai:   # urutan pin mengikuti definisi komponen supaya rapi
        urut = [str(p.get("nama", "")).upper() for p in komp[kid].get("pin") or []]
        pin_komp[kid].sort(key=lambda p: urut.index(p) if p in urut else 99)
    pins = sorted(pin_papan, key=_urut_pin)
    JARAK, ATAS = 26, 70
    tinggi_papan = max(160, 60 + len(pins) * JARAK)
    y_papan = {p: ATAS + 40 + i * JARAK for i, p in enumerate(pins)}
    y_komp, kotak, y = {}, {}, ATAS
    for kid in komp_dipakai:
        n = max(1, len(pin_komp[kid]))
        h = 44 + n * 24
        kotak[kid] = (y, h)
        for i, p in enumerate(pin_komp[kid]):
            y_komp[(kid, p)] = y + 40 + i * 24
        y += h + 22
    H = max(ATAS + tinggi_papan, y) + 30
    W, XP, XK = 780, 40, 520
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="Diagram rangkaian {_esc(P["nama"])}" '
         f'style="width:100%;height:auto;font-family:system-ui,sans-serif">',
         f'<rect x="0" y="0" width="{W}" height="{H}" rx="14" fill="#FBFAF6"/>',
         '<defs><pattern id="pdTitik" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="20" cy="20" r="1.6" fill="#E9E4D8"/></pattern></defs>',
         f'<rect x="0" y="0" width="{W}" height="{H}" rx="14" fill="url(#pdTitik)"/>',
         f'<rect x="{XP}" y="{ATAS}" width="190" height="{tinggi_papan}" rx="10" fill="{"#1B4F8A" if pid == "esp32" else "#0E7C86"}"/>',
         f'<text x="{XP + 16}" y="{ATAS + 26}" fill="#fff" font-size="15" font-weight="700">{_esc(P["nama"])}</text>']
    for p in pins:
        yy = y_papan[p]
        o.append(f'<text x="{XP + 172}" y="{yy + 4}" fill="#F2EBDD" font-size="12" text-anchor="end" font-weight="600">{_esc(p)}</text>'
                 f'<circle cx="{XP + 190}" cy="{yy}" r="5" fill="#D4AF5A" stroke="#fff" stroke-width="1.5"/>')
    for kid in komp_dipakai:
        y0, h = kotak[kid]
        baru = kid in komp_baru
        nama = str(komp[kid].get("nama", kid))
        nama = nama if len(nama) <= 27 else nama[:26].rstrip() + "…"
        o.append(f'<rect x="{XK}" y="{y0}" width="220" height="{h}" rx="10" fill="#fff" stroke="{"#D4AF5A" if baru else "#D0CBBE"}" stroke-width="{3 if baru else 1.5}"/>'
                 f'<text x="{XK + 14}" y="{y0 + 24}" fill="#1D2939" font-size="13.5" font-weight="700">{_esc(nama)}</text>')
        for p in pin_komp[kid]:
            yy = y_komp[(kid, p)]
            o.append(f'<circle cx="{XK}" cy="{yy}" r="5" fill="#667085" stroke="#fff" stroke-width="1.5"/>'
                     f'<text x="{XK + 14}" y="{yy + 4}" fill="#344054" font-size="12">{_esc(p)}</text>')
    jalur = 0
    for li, a, b, w in kabel:
        titik = []
        warna_ujung = ""
        for sisi in (a, b):
            if sisi[0].upper() == "PAPAN":
                nm = normal_pin(pid, sisi[1])
                warna_ujung = nm
                titik.append(("P", XP + 190, y_papan.get(nm, ATAS)))
            elif (sisi[0], sisi[1].upper()) in y_komp:
                titik.append(("K", XK, y_komp[(sisi[0], sisi[1].upper())]))
        if len(titik) != 2:
            continue
        pudar = sorot is not None and li < sorot
        tebal = sorot is not None and li == sorot
        warna = _warna(w, warna_ujung)
        op = "0.22" if pudar else "1"
        sw = 4.2 if tebal else 3
        (t1, x1, y1), (t2, x2, y2) = titik
        if t1 == "K" and t2 == "K":   # antarkomponen: lengkung di sisi kiri kotak komponen
            xc = XK - 24 - (jalur % 6) * 8
            d = f"M{x1},{y1} H{xc} V{y2} H{x2}"
        else:
            if t1 == "K":
                (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
            xc = 270 + (jalur % 18) * 12
            d = f"M{x1},{y1} H{xc} V{y2} H{x2}"
        jalur += 1
        o.append(f'<path d="{d}" fill="none" stroke="{warna}" stroke-width="{sw}" stroke-linejoin="round" stroke-linecap="round" opacity="{op}"/>')
    if sorot is not None:
        o.append(f'<text x="{W - 20}" y="{H - 12}" fill="#98A2B3" font-size="11.5" text-anchor="end">pudar = langkah sebelumnya · tebal = langkah ini</text>')
    o.append("</svg>")
    return "".join(o)


def lengkapi(data):
    """Tambahkan diagram per langkah dan diagram lengkap ke data panduan (dipanggil saat panduan ditampilkan)."""
    out = dict(data)
    out["langkah"] = [dict(lg, diagram=diagram(data, sampai=i, sorot=i)) for i, lg in enumerate(data.get("langkah") or [])]
    out["diagram_lengkap"] = diagram(data)
    out["papan_nama"] = PAPAN.get(str(data.get("papan") or "").lower(), {}).get("nama", data.get("papan"))
    return out
