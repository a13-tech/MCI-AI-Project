/**
 * MCI HQ · Pengirim email notifikasi !izin
 * Render gratis memblokir port SMTP, jadi server MCI mengirim lewat Google Apps Script (HTTP).
 *
 * Cara pasang (pakai akun mci.corp.indonesia@gmail.com):
 * 1. Buka https://script.google.com → New project → hapus isi, tempel file ini.
 * 2. Ganti ISI_MAIL_SECRET di bawah dengan nilai MAIL_SECRET dari Render → Environment.
 * 3. Klik Run pada fungsi tes() sekali untuk memberi izin Gmail (cek email tes masuk).
 * 4. Deploy → New deployment → Select type: Web app
 *    Execute as: Me · Who has access: Anyone → Deploy.
 * 5. Salin "Web app URL" (https://script.google.com/macros/s/.../exec)
 *    lalu isi di Render → Environment → MAIL_WEBHOOK.
 */
const SECRET = "ISI_MAIL_SECRET";

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.secret !== SECRET) return ContentService.createTextOutput("forbidden");
    MailApp.sendEmail({to: d.to || "mci.corp.indonesia@gmail.com", subject: String(d.subject).slice(0, 200), body: String(d.body).slice(0, 5000), name: "MCI HQ"});
    return ContentService.createTextOutput("ok");
  } catch (err) {
    return ContentService.createTextOutput("error: " + err);
  }
}

/** Jalankan sekali dari editor (tombol Run) untuk tes + memberi izin Gmail. */
function tes() {
  MailApp.sendEmail("mci.corp.indonesia@gmail.com", "[MCI HQ] Tes email izin", "Kalau email ini masuk, pengirim notifikasi !izin sudah siap.");
}
