"""Teks resmi Undang-Undang MCI dan Peraturan Kas MCI (versi bahasa sederhana-formal, 9 Nov 2026).

main.py memakai teks ini untuk mengisi tabel `uu` bila masih kosong, dan untuk memperbarui sekali (UU_VERSI)
baris yang belum pernah diubah lewat web. Baris yang sudah diedit Dev di web tidak ditimpa.
Format isi: satu baris = satu ayat. Awali "* " untuk ayat, "** " untuk rincian di bawah ayat.
"""

UU_VERSI = 6

UU_SEED = {
    "info": "UNDANG-UNDANG MAHARDIKA CAKRAWALA INDONESIA (UU MCI)\n"
            "Draf Final Putusan Dewan · 3 November 2026 · @A13\n"
            "Undang-Undang ini mengatur anggota MCI (manusia, bukan bot AI). Setiap pasal disertai penjelasan singkat \"Artinya\" agar mudah dipahami. "
            "Draf ini belum berlaku sampai disetujui anggota.",
    "mukadimah": "MCI (3 Serangkai Production) dibangun bersama untuk berjaya di tiga bidang, sesuai semboyan \"Trimatra Vijaya Mahe\". "
                 "Agar kebersamaan ini tertib, adil, dan aman, anggota MCI menetapkan Undang-Undang ini.",
    "pasal": [
        {"bab": "BAB I. KETENTUAN UMUM", "nomor": 1, "judul": "Kedaulatan Anggota",
         "isi": "* Kekuasaan tertinggi di MCI ada pada anggota.\n"
                "* Keputusan penting diambil bersama sesuai Undang-Undang ini.",
         "artinya": "keputusan penting MCI diambil bersama oleh anggota."},
        {"bab": "BAB I. KETENTUAN UMUM", "nomor": 2, "judul": "Kepatuhan pada Aturan",
         "isi": "* Setiap anggota wajib mematuhi Undang-Undang ini dan peraturan turunannya, seperti Peraturan Kas.\n"
                "* Peraturan turunan tidak boleh bertentangan dengan Undang-Undang ini.",
         "artinya": "semua anggota wajib taat aturan, dan aturan yang lebih kecil tidak boleh melawan Undang-Undang."},
        {"bab": "BAB II. KEANGGOTAAN DAN HAK SUARA", "nomor": 3, "judul": "Hak Suara yang Setara",
         "isi": "* Setiap anggota memiliki satu suara dengan nilai yang sama.\n"
                "* Keputusan diambil melalui musyawarah. Jika tidak tercapai kesepakatan, keputusan diambil berdasarkan suara terbanyak.",
         "artinya": "semua anggota setara. Pengurus maupun anggota baru sama-sama memiliki satu suara."},
        {"bab": "BAB II. KEANGGOTAAN DAN HAK SUARA", "nomor": 4, "judul": "Kerahasiaan Data",
         "isi": "* Setiap anggota wajib menjaga data MCI, yaitu akun, kata sandi, data keuangan, dan informasi internal.\n"
                "* Anggota dilarang memberikan data tersebut kepada pihak luar tanpa izin Dewan Pengawas.",
         "artinya": "apa yang ada di dalam MCI tetap di dalam MCI."},
        {"bab": "BAB III. STRUKTUR DAN PENGAWASAN", "nomor": 5, "judul": "Jabatan dan Batas Wewenang",
         "isi": "* Struktur MCI bertingkat, dan setiap jabatan memiliki batas wewenang yang tertulis.\n"
                "* Perintah yang sesuai wewenang wajib dijalankan. Perintah di luar wewenang boleh dipertanyakan.",
         "artinya": "ada atasan dan bawahan, tetapi setiap jabatan punya batas kekuasaan yang jelas."},
        {"bab": "BAB III. STRUKTUR DAN PENGAWASAN", "nomor": 6, "judul": "Keamanan Digital",
         "isi": "* Dewan Pengawas menunjuk satu anggota sebagai penanggung jawab keamanan digital, yaitu akun, kata sandi, dan data MCI.\n"
                "* Semua anggota wajib mengikuti arahan keamanan dari penanggung jawab tersebut.",
         "artinya": "ada satu orang yang menjaga keamanan digital MCI, dan semua anggota mengikuti arahannya."},
        {"bab": "BAB III. STRUKTUR DAN PENGAWASAN", "nomor": 7, "judul": "Dewan Pengawas dan Penyelesaian Perselisihan",
         "isi": "* Dewan Pengawas adalah anggota yang dipilih oleh anggota untuk mengawasi pelaksanaan Undang-Undang ini.\n"
                "* Perselisihan antaranggota diselesaikan secara musyawarah. Jika gagal, Dewan Pengawas memutuskan secara tertulis beserta alasannya.",
         "artinya": "jika ada perselisihan, bicarakan dulu baik-baik. Jika tetap buntu, Dewan Pengawas yang memutuskan."},
        {"bab": "BAB IV. KEUANGAN", "nomor": 8, "judul": "Iuran Kas",
         "isi": "* Iuran kas biasa sebesar Rp5.000 per hari kerja, atau Rp25.000 per minggu (Senin–Jumat).\n"
                "* Anggota yang kurang mampu dapat mengajukan keringanan iuran sebesar Rp10.000 per minggu dengan persetujuan Bendahara atau Dewan Pengawas.\n"
                "* Kas dibayar setiap hari Jumat untuk minggu tersebut.\n"
                "* Kas hanya digunakan untuk operasional, perlengkapan, dan proyek MCI.",
         "artinya": "iuran biasa Rp25.000 per minggu. Anggota yang kurang mampu dapat membayar Rp10.000 per minggu setelah disetujui. Pembayaran dilakukan setiap Jumat."},
        {"bab": "BAB IV. KEUANGAN", "nomor": 9, "judul": "Pencatatan dan Laporan Kas",
         "isi": "* Semua uang masuk dan keluar dicatat dalam buku kas dalam Rupiah.\n"
                "* Bendahara wajib mengumumkan laporan pembayaran dan saldo kas paling lambat hari Minggu pukul 23.59 WIB setelah penagihan hari Jumat.",
         "artinya": "uang kas dicatat rapi, dan Bendahara wajib melapor kepada semua anggota setiap Minggu malam."},
        {"bab": "BAB IV. KEUANGAN", "nomor": 10, "judul": "Pemeriksaan Kas",
         "isi": "* Kas diperiksa secara berkala untuk menemukan penyimpangan sejak dini.\n"
                "* Hasil pemeriksaan disampaikan kepada semua anggota.",
         "artinya": "kas diperiksa rutin agar masalah ketahuan sebelum membesar."},
        {"bab": "BAB IV. KEUANGAN", "nomor": 11, "judul": "Dana Cadangan",
         "isi": "* Sebagian kas disisihkan sebagai dana cadangan untuk keadaan darurat. Besarnya ditetapkan oleh Dewan Pengawas.\n"
                "* Setiap pemakaian dana cadangan wajib dicatat dan dilaporkan.",
         "artinya": "ada tabungan untuk keadaan mendesak, dan pemakaiannya harus terbuka."},
        {"bab": "BAB V. KEAMANAN DAN KEADAAN DARURAT", "nomor": 12, "judul": "Prosedur Darurat",
         "isi": "* MCI memiliki prosedur darurat untuk keamanan sistem.\n"
                "* Prosedur tersebut diperiksa menggunakan daftar periksa (checklist) di setiap bidang kerja.",
         "artinya": "langkah-langkah darurat sudah disiapkan sebelum masalah terjadi."},
        {"bab": "BAB V. KEAMANAN DAN KEADAAN DARURAT", "nomor": 13, "judul": "Tindakan Cepat saat Darurat",
         "isi": "* Saat terjadi gangguan keamanan digital, penanggung jawab keamanan dan Dewan Pengawas boleh mengambil tindakan cepat di luar prosedur biasa.\n"
                "* Tindakan tersebut wajib dilaporkan dalam 24 jam dan ditinjau kembali oleh Dewan Pengawas.",
         "artinya": "saat akun diretas, boleh langsung bertindak tanpa rapat panjang, tetapi wajib melapor dalam satu hari."},
        {"bab": "BAB VI. TATA TERTIB DAN SANKSI", "nomor": 14, "judul": "Komunikasi",
         "isi": "* Komunikasi anggota di grup WhatsApp MCI wajib singkat, jelas, dan sopan.\n"
                "* Pengumuman resmi (kas, jadwal, keputusan) menggunakan format yang ditetapkan Dewan Pengawas.",
         "artinya": "bicara singkat dan sopan, dan pengumuman penting ditulis dengan format yang sama."},
        {"bab": "BAB VI. TATA TERTIB DAN SANKSI", "nomor": 15, "judul": "Izin dan Denda Kas",
         "isi": "* Anggota yang berhalangan membayar, atau yang mengajukan keringanan Rp10.000 per minggu, wajib meminta izin atau persetujuan kepada Bendahara paling lambat pada hari Jumat penagihan. Izin keterlambatan paling banyak 2 kali per anggota.\n"
                "* Anggota dengan iuran biasa yang tidak membayar pada hari Jumat tanpa izin didenda Rp25.000.\n"
                "* Jika tunggakan berlanjut sampai Jumat berikutnya, tagihannya menjadi Rp75.000 (kas minggu 1 Rp25.000 + kas minggu 2 Rp25.000 + denda Rp25.000). Untuk anggota dengan keringanan, besar tunggakan dan denda disesuaikan oleh Bendahara secara seimbang.\n"
                "* Rincian lebih lanjut diatur dalam Peraturan Kas.",
         "artinya": "jika berhalangan atau butuh keringanan, minta izin ke Bendahara. Tanpa izin tetap didenda."},
        {"bab": "BAB VI. TATA TERTIB DAN SANKSI", "nomor": 16, "judul": "Pencabutan Hak Proyek karena Tunggakan Kas",
         "isi": "* Anggota yang terus-menerus tidak membayar kas (melewati batas toleransi atau tidak beritikad baik) otomatis kehilangan seluruh haknya dalam proyek MCI.\n"
                "* Hak yang hilang meliputi:\n"
                "** hak suara dan keikutsertaan dalam keputusan proyek;\n"
                "** akses dan pemakaian perlengkapan, dokumen, serta fasilitas proyek;\n"
                "** hak atas bagian hasil, pengakuan, dan kepemilikan atas hasil proyek.\n"
                "* Hak proyek kembali penuh setelah seluruh tunggakan kas dan dendanya lunas.",
         "artinya": "menunggak kas tanpa tanggung jawab membuat anggota kehilangan hak atas proyek dan bagi hasil sampai semua tunggakan lunas."},
        {"bab": "BAB VI. TATA TERTIB DAN SANKSI", "nomor": 17, "judul": "Sanksi untuk Bendahara",
         "isi": "* Jika Bendahara tidak memberikan laporan kas sampai hari Minggu pukul 23.59 WIB, Bendahara didenda 2 kali iuran pribadinya dalam seminggu, atau menerima penalti lain yang disepakati tim.",
         "artinya": "Bendahara juga didenda jika terlambat melaporkan kas."},
        {"bab": "BAB VI. TATA TERTIB DAN SANKSI", "nomor": 18, "judul": "Sanksi Umum dan Perubahan Undang-Undang",
         "isi": "* Pelanggaran yang tidak diatur secara khusus dikenai sanksi bertahap: teguran, peringatan tertulis, sampai dikeluarkan dari keanggotaan.\n"
                "* Anggota berhak membela diri sebelum sanksi ditetapkan.\n"
                "* Perubahan Undang-Undang ini sah jika disetujui lebih dari separuh anggota.\n"
                "* Undang-Undang ini berlaku sejak disahkan oleh anggota.",
         "artinya": "pelanggaran umum ditangani bertahap dan anggota boleh membela diri. Undang-Undang dapat diubah jika lebih dari separuh anggota setuju."},
        {"bab": "BAB VI. TATA TERTIB DAN SANKSI", "nomor": 19, "judul": "Absen Harian",
         "isi": "* Setiap anggota wajib mengisi absen harian di web MCI setiap hari Senin sampai Sabtu, paling lambat pukul 23.59 WIB. Hari Minggu libur.\n"
                "* Sebelum absen, anggota wajib menjawab satu soal kuis dengan benar. Jika jawabannya salah, anggota mendapat soal lain sampai benar.\n"
                "* Anggota yang berhalangan wajib mengajukan izin melalui web. Izin disetujui atau ditolak oleh pengurus yang berwenang. Hari yang diizinkan tidak dihitung bolos.\n"
                "* Setiap anggota paling banyak 3 kali izin dalam satu bulan. Izin tambahan hanya dapat diberikan sebagai izin khusus oleh Manager atau Dev, dan izin khusus tidak dihitung dalam batas 3 kali tersebut.\n"
                "* Hari tanpa absen dan tanpa izin yang disetujui dihitung sebagai bolos. Jumlah bolos dihitung keseluruhan sejak absen harian berlaku.\n"
                "* Anggota diberi peringatan saat bolos mencapai 4 kali dan 5 kali.\n"
                "* Anggota yang bolos lebih dari 5 kali otomatis dikeluarkan dari MCI. Ketentuan ini adalah pengecualian dari sanksi bertahap pada Pasal 18.\n"
                "* Anggota yang dikeluarkan karena absen dapat mengajukan keberatan kepada Pemilik MCI. Jika Pemilik MCI memulihkannya, hitungan bolos dimulai lagi dari nol.",
         "artinya": "isi absen setiap hari kecuali Minggu dan jawab kuisnya. Kalau berhalangan, minta izin (maksimal 3 kali sebulan, lebih dari itu hanya lewat izin khusus Manager atau Dev). Bolos lebih dari 5 kali berarti otomatis keluar."},
    ],
}

# Peraturan Pengelolaan dan Pembayaran Kas MCI (aturan turunan UU MCI untuk urusan kas)
KAS_SEED = {
    "info": "PERATURAN KAS MCI\n"
            "Peraturan Pengelolaan dan Pembayaran Kas Mahardika Cakrawala Indonesia\n"
            "Berlaku sejak ditetapkan, demi kedisiplinan, keadilan, dan keberlanjutan proyek MCI.",
    "pasal": [
        {"nomor": 1, "judul": "Ketentuan Umum dan Besaran Kas", "isi": "\n".join([
            "* Iuran kas biasa sebesar Rp5.000 per hari kerja, atau Rp25.000 per minggu (Senin–Jumat).",
            "* Anggota yang kurang mampu dapat membayar keringanan Rp10.000 per minggu dengan persetujuan Bendahara atau Dewan Pengawas.",
            "* Kas dibayar setiap hari Jumat untuk minggu tersebut.",
            "* Kas hanya dipakai untuk operasional, perlengkapan, dan proyek MCI.",
            "* Kewajiban kas dimulai sejak minggu anggota resmi bergabung.",
            "* Anggota boleh membayar kas beberapa minggu sekaligus di muka.",
            "* Arti istilah dalam peraturan ini:",
            "** Tunggakan: minggu yang kasnya belum dibayar setelah hari Jumat minggu itu lewat.",
            "** Masa toleransi: tunggakan kurang dari 2 minggu tanpa izin.",
            "** Pemilik proyek: anggota yang mengusulkan dan bertanggung jawab atas sebuah proyek.",
            "** Pemilik MCI: pendiri sekaligus pemilik MCI.",
            "* Peraturan ini adalah aturan turunan UU MCI yang mengatur urusan kas secara lebih rinci. Hal yang tidak diatur di sini mengikuti UU MCI."])},
        {"nomor": 2, "judul": "Laporan dan Sanksi Bendahara", "isi": "\n".join([
            "* Bendahara wajib mengumumkan laporan kas, yaitu siapa yang sudah membayar dan sisa saldo, paling lambat hari Minggu pukul 23.59 WIB setelah penagihan hari Jumat.",
            "* Jika terlambat, Bendahara didenda 2 kali iuran pribadinya dalam seminggu, atau menerima penalti lain yang disepakati tim."])},
        {"nomor": 3, "judul": "Izin dan Denda Anggota", "isi": "\n".join([
            "* Anggota yang belum bisa membayar tepat waktu karena urusan penting wajib meminta izin kepada Bendahara paling lambat pada hari Jumat penagihan.",
            "* Setiap anggota hanya boleh memakai izin paling banyak 2 kali.",
            "* Anggota yang tidak membayar pada hari Jumat tanpa izin didenda Rp25.000.",
            "* Denda dikenakan satu kali untuk satu rangkaian tunggakan, tidak bertambah setiap minggu.",
            "* Contoh: menunggak 2 minggu tanpa izin, tagihannya Rp75.000 (kas minggu 1 Rp25.000 + kas minggu 2 Rp25.000 + denda Rp25.000).",
            "* Untuk anggota dengan keringanan, besar denda disesuaikan oleh Bendahara.",
            "* Tunggakan boleh dicicil sesuai jadwal yang disepakati dengan Bendahara. Selama cicilan lancar, tidak ada denda tambahan."])},
        {"nomor": 4, "judul": "Pencabutan dan Pemulihan Hak Proyek", "isi": "\n".join([
            "* Anggota yang menunggak melewati masa toleransi, atau tidak beritikad baik untuk membayar, otomatis kehilangan haknya dalam proyek MCI.",
            "* Hak yang hilang meliputi:",
            "** ikut memberi suara dan mengambil keputusan proyek;",
            "** memakai perlengkapan, dokumen, dan fasilitas proyek;",
            "** menerima bagian hasil, pengakuan, atau kepemilikan atas hasil proyek.",
            "* Hak kembali penuh setelah semua tunggakan dan denda lunas.",
            "* Anggota yang baru mulai membayar di tengah jalan tetap wajib melunasi semua tunggakan sejak kewajibannya dimulai. Membayar minggu berjalan saja tidak mengembalikan hak.",
            "* Bagian hasil yang dibagikan selama hak dicabut tidak diberikan kemudian, meskipun anggota sudah melunasi tunggakannya."])},
        {"nomor": 5, "judul": "Keterbukaan Laporan", "isi": "\n".join([
            "* Laporan kas wajib diumumkan Bendahara setiap minggu agar dapat dilihat semua anggota."])},
        {"nomor": 6, "judul": "Mengeluarkan Anggota dari Proyek", "isi": "\n".join([
            "* Pemilik proyek boleh langsung mengeluarkan anggota tim yang haknya dicabut menurut Pasal 4, tanpa persetujuan orang lain.",
            "* Tindakan ini adalah urusan administrasi, bukan hukuman. Pemilik proyek wajib mencatatnya di halaman Proyek dan memberi tahu anggota tersebut.",
            "* Anggota yang menolak dikeluarkan, misalnya tetap memakai perlengkapan atau mengaku memiliki hasil proyek, dapat diberi Surat Peringatan melalui proses sidang. Ia tetap berhak membela diri.",
            "* Setelah haknya pulih, anggota boleh bergabung kembali jika pemilik proyek menyetujui."])},
        {"nomor": 7, "judul": "Membuat Proyek dan Dana Proyek", "isi": "\n".join([
            "* Setiap anggota boleh mengusulkan proyek baru.",
            "* Proyek sah jika disetujui paling sedikit 3 anggota aktif yang haknya tidak sedang dicabut, termasuk pengusul.",
            "* Proyek wajib dicatat di halaman Proyek, lengkap dengan pemilik proyek dan nama anggota yang menyetujui.",
            "* Setiap proyek boleh memakai dana kas paling banyak Rp75.000 secara keseluruhan.",
            "* Jika membutuhkan tambahan dana, pemilik proyek mengajukannya kepada Bendahara atau Pemilik MCI. Dana tambahan baru boleh dipakai setelah disetujui.",
            "* Dana kas untuk proyek dipindahkan ke kas proyek setelah Bendahara mengonfirmasi pencairan. Kas proyek dicatat terpisah dari kas MCI.",
            "* Subsidi, hibah, atau pinjaman dari mitra dicatat sebagai kas proyek dan tidak dihitung dalam batas Rp75.000. Pinjaman mitra wajib dicatat bersama tanggal jatuh temponya.",
            "* Proyek lama atau proyek arsip boleh dicatat pengurus tanpa persetujuan 3 anggota, dengan syarat tidak meminta dana kas MCI. Proyek yang dibekukan (Freeze) wajib ditulis alasannya.",
            "* Pembelian memakai kas proyek diajukan lewat Pengajuan dana (barang, harga, alasan). Yang disetujui Bendahara atau Pemilik MCI langsung dicatat keluar dari kas proyek.",
            "* Catatan dan jurnal proyek menyimpan nama penulis dan tanggalnya. Jejak itu hanya boleh dihapus Pemilik MCI."])},
        {"nomor": 8, "judul": "Penyalahgunaan Dana Kas", "isi": "\n".join([
            "* Yang termasuk penyalahgunaan dana kas: memakai kas untuk kepentingan pribadi, mengambil kas tanpa izin, menahan uang setoran, atau memalsukan bukti pengeluaran.",
            "* Dugaan penyalahgunaan diperiksa oleh Dewan Pengawas atau Pemilik MCI. Terlapor diberi waktu membela diri paling lama 7 hari.",
            "* Jika terbukti, pelaku wajib mengembalikan seluruh dana yang disalahgunakan. Setelah dana dikembalikan, ia dikeluarkan dari MCI dan dimasukkan ke dalam Blacklist.",
            "* Selama dana belum dikembalikan, semua hak pelaku di MCI dibekukan.",
            "* Kesalahan administrasi yang tidak disengaja, seperti salah catat atau nota hilang, bukan penyalahgunaan jika diperbaiki dalam 7 hari."])},
        {"nomor": 9, "judul": "Bukti Pengeluaran", "isi": "\n".join([
            "* Setiap pengeluaran kas dicatat beserta buktinya, berupa nota atau foto.",
            "* Pengeluaran di bawah Rp10.000 boleh tanpa nota, cukup dengan keterangan yang jelas.",
            "* Jika bukti belum ada, pengguna dana wajib melengkapinya paling lama 7 hari setelah diminta Bendahara."])},
        {"nomor": 10, "judul": "Anggota yang Keluar", "isi": "\n".join([
            "* Iuran yang sudah dibayar menjadi milik kas MCI dan tidak dikembalikan.",
            "* Anggota yang ingin keluar wajib melunasi tunggakan kasnya terlebih dahulu.",
            "* Anggota yang keluar tanpa melunasi tunggakan dimasukkan ke dalam Blacklist."])},
        {"nomor": 11, "judul": "Tutup Buku dan Pembagian Hasil Proyek", "isi": "\n".join([
            "* Setelah proyek selesai, Bendahara atau Pemilik MCI menutup buku kas proyek.",
            "* Saat tutup buku, uang kas proyek dipakai dengan urutan berikut:",
            "** 1. Melunasi pinjaman mitra. Jika uangnya kurang, kekurangannya ditalangi kas MCI.",
            "** 2. Mengembalikan modal dari kas MCI.",
            "** 3. Sisanya adalah keuntungan proyek.",
            "* Keuntungan proyek dibagi sebagai berikut: 20% masuk kas MCI sebagai uang perputaran, dan 80% dibagi rata kepada anggota tim proyek yang bekerja.",
            "* Anggota tim yang tidak bekerja, sesuai catatan pemilik proyek, tidak mendapat bagian.",
            "* Anggota yang hak proyeknya sedang dicabut tidak mendapat bagian, sesuai Pasal 4.",
            "* Sisa pembulatan, atau bagian yang tidak punya penerima, masuk kas MCI.",
            "* Proyek yang merugi tidak dibagi. Modal yang tidak kembali dicatat sebagai pengeluaran kas MCI."])},
    ],
}
