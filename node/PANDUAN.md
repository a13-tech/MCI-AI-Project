# Panduan Pasang MCI Node (MacBook + HDD + Ollama)

MCI Node menjadikan MacBook di markas sebagai **gudang file berat** untuk web MCI HQ:

- **Video dokumentasi ukuran asli** disimpan di HDD, lalu diputar langsung dari HDD.
- **Backup database dikirim tiap malam** (setelah pukul 02.00 WIB) ke HDD atau SSD. Yang disimpan 30 backup terakhir.
- **Ollama di MacBook jadi cadangan AI terakhir** kalau semua AI lain penuh.

Neon tetap jadi database utama. Kalau laptop mati atau tidur, web tetap jalan seperti biasa. Bedanya cuma dua: video yang ada di HDD tampil "sedang offline", dan AI langsung lompat ke cadangan lain.

Jalur datanya: browser anggota ke `node.mci-ai.my.id`, lalu lewat Cloudflare Tunnel (terowongan terenkripsi) ke MacBook, lalu MCI Node (port 8787), lalu HDD atau Ollama. Router rumah tidak perlu dibuka.

---

## Tahap 1: Pindahkan DNS mci-ai.my.id ke Cloudflare (sekali saja)

Domain tetap milikmu di Hostinger. Yang dipindah hanya "buku alamat"-nya (nameserver).

1. Daftar atau masuk ke **dash.cloudflare.com**, klik **Add a domain**, ketik `mci-ai.my.id`, lalu pilih paket **Free**.
2. Cloudflare akan memindai catatan DNS yang sudah ada. **Cocokkan dengan daftar di bawah**, dan tambahkan sendiri yang belum muncul:

   | Tipe | Nama | Isi | Proxy |
   |---|---|---|---|
   | A | `@` | `216.24.57.1` | **DNS only (awan abu-abu)** |
   | CNAME | `www` | `mci-ai-hq.onrender.com` | **DNS only (awan abu-abu)** |
   | MX | `@` | `mx.zoho.com` prioritas 10 | (otomatis DNS only) |
   | MX | `@` | `mx2.zoho.com` prioritas 20 | |
   | MX | `@` | `mx3.zoho.com` prioritas 50 | |
   | TXT | `@` | `v=spf1 include:zohomail.com ~all` | |
   | TXT | `@` | `zoho-verification=zb83002275.zmverify.zoho.com` | |
   | TXT | `zmail._domainkey` | (kunci DKIM panjang dari Zoho, diawali `v=DKIM1; k=rsa; p=`) | |

   Dua catatan web Render **harus DNS only (abu-abu)**, supaya sertifikat HTTPS dari Render tetap jalan.
   Kunci DKIM yang panjang bisa disalin dari Zoho Mail Admin, bagian Domains, mci-ai.my.id, Email Configuration, DKIM.
3. Cloudflare akan memberi **2 nameserver**, misalnya `xxx.ns.cloudflare.com`.
4. Masuk ke **Hostinger**, buka Domain, lalu `mci-ai.my.id`, lalu **DNS / Nameservers**, pilih **Change nameservers**, dan isi dengan 2 nameserver dari Cloudflare. Simpan.
5. Tunggu sampai Cloudflare menandai domain **Active**. Biasanya beberapa menit, paling lama 24 jam.
6. Setelah aktif, cek tiga hal:
   - https://mci-ai.my.id masih terbuka;
   - kirim email ke alvaro@mci-ai.my.id dari email lain, lalu cek masuk;
   - balas email itu, lalu cek keluar.

## Tahap 2: Siapkan MacBook supaya tidak tidur

1. Buka **System Settings**, lalu **Battery**, lalu **Options**, dan nyalakan **Prevent automatic sleeping on power adapter when the display is off**.
2. Di menu yang sama, matikan **Put hard disks to sleep when possible**. Kalau pilihannya tidak ada, buka Terminal dan ketik `sudo pmset -a disksleep 0`.
3. Biarkan MacBook dicolok charger. Program Node juga menahan Mac agar tidak tertidur selama dicolok.

## Tahap 3: Pasang MCI Node

1. Unduh folder `node/` dari repo GitHub, atau ambil dari file yang dikirim di chat, lalu taruh misalnya di `~/Downloads/node`.
2. Colok HDD. Cek namanya di Finder, misalnya `/Volumes/HDD`.
3. Buka **Terminal**, lalu jalankan (ganti nama HDD sesuai milikmu):

   ```bash
   cd ~/Downloads/node
   bash pasang_node.sh "/Volumes/HDD/MCI-Node" "/Volumes/MCI/Database Server MCI"
   ```

   Argumen kedua adalah folder backup database. Boleh dihapus kalau backup cukup disimpan di HDD.
4. Di akhir, skrip akan menampilkan **TOKEN RAHASIA**. Salin token itu. Jangan dikirim ke chat atau grup.

Kalau ingin melihat log Node: `tail -f ~/MCI-Node/node.log`.

## Tahap 4: Pasang Cloudflare Tunnel di MacBook

1. Pasang cloudflared. Kalau Homebrew sudah ada, jalankan `brew install cloudflared`. Kalau belum, unduh `.pkg` untuk macOS dari halaman "Downloads" di dokumentasi Cloudflare Tunnel.
2. Jalankan perintah berikut satu per satu:

   ```bash
   cloudflared tunnel login                     # browser terbuka, pilih domain mci-ai.my.id
   cloudflared tunnel create mci-node           # membuat terowongan
   cloudflared tunnel route dns mci-node node.mci-ai.my.id
   ```

3. Buat file `~/.cloudflared/config.yml` (ganti `NAMA_PENGGUNA` dan `ID-TUNNEL` sesuai yang muncul saat `tunnel create`):

   ```yaml
   tunnel: mci-node
   credentials-file: /Users/NAMA_PENGGUNA/.cloudflared/ID-TUNNEL.json
   ingress:
     - hostname: node.mci-ai.my.id
       service: http://localhost:8787
     - service: http_status:404
   ```

4. Jadikan cloudflared jalan otomatis saat Mac menyala:

   ```bash
   sudo cloudflared --config ~/.cloudflared/config.yml service install
   ```

5. Tes dari HP (pakai data seluler, bukan wifi rumah): buka https://node.mci-ai.my.id/sehat. Kalau berhasil, akan muncul `{"ok": true, "nama": "MCI Node"}`.

## Tahap 5: Ollama di HDD (cadangan AI terakhir)

1. Tutup aplikasi Ollama dulu (ikon llama di menu bar, lalu Quit).
2. Pindahkan folder model ke HDD dan buat jalan pintasnya:

   ```bash
   mkdir -p "/Volumes/HDD/ollama-models"
   mv ~/.ollama/models/* "/Volumes/HDD/ollama-models/" 2>/dev/null || true
   rm -rf ~/.ollama/models
   ln -s "/Volumes/HDD/ollama-models" ~/.ollama/models
   ```

3. Buka lagi aplikasi Ollama, lalu cek `ollama list`. `gemma3:4b` harus masih ada.
4. Model yang cocok untuk MacBook M1 RAM 8 GB: `gemma3:4b`, `qwen3:4b`, `llama3.2:3b`. Jangan memakai model di atas sekitar 8B, karena laptop akan sangat lambat.
5. Saat pertama dipanggil, model dimuat dari HDD dan bisa makan waktu puluhan detik. Setelah itu modelnya tinggal di RAM dan jauh lebih cepat.

## Tahap 6: Sambungkan ke web

1. Buka web MCI HQ, lalu **Panel CEO**, lalu **Kunci AI**, grup **MCI Node (HDD + Ollama markas)**.
2. Isi:
   - `NODE_URL` = `https://node.mci-ai.my.id`
   - `NODE_TOKEN` = token dari Tahap 3, lalu klik **Uji**. Seharusnya muncul "Node 1.0 online, HDD kosong … GB, Ollama: gemma3:4b".
   - `NODE_MODEL` (opsional) = nama model Ollama yang dipakai. Bawaannya `gemma3:4b`.
3. Kartu **MCI Node** di Panel CEO akan menampilkan status online, ruang HDD, model Ollama, dan backup terakhir.
4. Di **Antrean cadangan**, sekarang muncul **MCI Node · Ollama**, otomatis paling belakang.
5. Di halaman **Dokumentasi**, tab **Video** akan menulis "Disimpan ukuran asli di HDD markas MCI".

---

## Kalau ada masalah

| Gejala | Yang dicek |
|---|---|
| Panel: "Node tidak menjawab atau token salah" | Laptop menyala? Jalankan `curl http://127.0.0.1:8787/sehat` di Mac. Cek juga https://node.mci-ai.my.id/sehat dari HP. Token di Panel sama dengan `~/MCI-Node/.token`? |
| Node tidak jalan setelah restart Mac | `launchctl load ~/Library/LaunchAgents/id.mci.node.plist`, lalu cek `~/MCI-Node/node.log` |
| HDD tidak terbaca setelah restart | Colok ulang HDD; Node otomatis mencoba lagi tiap 15 detik |
| Log berisi "Operation not permitted" | macOS butuh izin akses HDD. Pilih **Allow** kalau muncul pertanyaan "python3 would like to access files on a removable volume". Kalau tidak muncul: System Settings, Privacy & Security, Full Disk Access, tambahkan `python3`, lalu jalankan lagi `pasang_node.sh` |
| Video tidak bisa diputar | Laptop tidur atau mati, atau HDD dicabut. Web menampilkan "HDD markas sedang offline" |
| Email Zoho macet setelah pindah DNS | Cek lagi catatan MX, SPF, DKIM di Cloudflare (Tahap 1, langkah 2) |
| Ganti token | Hapus `~/MCI-Node/.token`, jalankan lagi `pasang_node.sh`, lalu tempel token baru di Panel |

**Catatan Cloudflare:** aturan paket gratis Cloudflare tidak mengizinkan jaringannya dipakai untuk streaming video dalam jumlah besar. Untuk album dokumentasi internal MCI yang ditonton belasan anggota, risikonya kecil. Kalau suatu saat Cloudflare memberi peringatan atau memblokir, pindahkan penyimpanan video ke Cloudflare R2 (kodenya sudah ada di web; isi kunci R2 di Panel CEO).
