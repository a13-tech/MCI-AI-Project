/*
  MCI HQ · Lab IoT — kirim data sensor ESP32 ke web MCI HQ
  -------------------------------------------------------------
  Data muncul di: halaman Lab IoT (admin) + dinding layar LT 2 di kantor 3D.
  Aegis kirim alarm Telegram kalau nilai melewati aturan.

  Rangkaian contoh:
    HC-SR04  VCC -> 5V (VIN), GND -> GND
             TRIG -> GPIO 5
             ECHO -> pembagi tegangan (1k ke GPIO 18, 2k ke GND)  << WAJIB: ECHO keluar 5V, ESP32 cuma tahan 3.3V
    Baterai  + -> resistor 100k -> GPIO 34 -> resistor 100k -> GND  (pembagi 1/2, maks 6.6V)
             (lewati bagian baterai kalau tidak dipakai; nilainya jadi ~0)

  Board: "ESP32 Dev Module". Tidak perlu library tambahan (WiFi, HTTPClient sudah bawaan).
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// ====== GANTI BAGIAN INI ======
const char* WIFI_SSID = "NAMA_WIFI";
const char* WIFI_PASS = "PASSWORD_WIFI";
const char* ENDPOINT  = "https://URL-RENDER-KAMU/api/iot";   // lihat halaman Lab IoT
const char* IOT_KEY   = "ISI_KUNCI_IOT";                     // lihat halaman Lab IoT
const char* PERANGKAT = "esp32-lab";                         // huruf kecil, angka, minus
const uint32_t JEDA_MS = 5000;                               // kirim tiap 5 detik (min 1 detik)
// ==============================

const int PIN_TRIG = 5, PIN_ECHO = 18, PIN_BAT = 34, PIN_LED = 2;
uint32_t terakhir = 0, gagal = 0;

float bacaJarakCm() {
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long us = pulseIn(PIN_ECHO, HIGH, 30000);   // batas 30 ms (~5 m)
  if (us == 0) return -1;                              // tidak ada pantulan
  return us * 0.0343f / 2.0f;
}

float bacaBateraiV() {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(PIN_BAT);   // rata-rata 16 sampel
  return (mv / 16.0f) * 2.0f / 1000.0f;                               // x2 karena pembagi 100k/100k
}

void sambungWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Menyambung ke %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); }
  Serial.println(WiFi.status() == WL_CONNECTED ? " OK" : " GAGAL");
}

bool kirim(float jarak, float bat) {
  WiFiClientSecure tls;
  tls.setInsecure();               // sederhana: tanpa cek sertifikat (cukup untuk data sensor)
  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(tls, ENDPOINT)) return false;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-MCI", "1");
  http.addHeader("X-IOT-KEY", IOT_KEY);

  char body[256];
  snprintf(body, sizeof(body),
    "{\"perangkat\":\"%s\",\"data\":{\"jarak_cm\":%.1f,\"baterai_v\":%.2f,\"rssi\":%d,\"uptime_s\":%lu}}",
    PERANGKAT, jarak, bat, WiFi.RSSI(), millis() / 1000UL);

  int kode = http.POST((uint8_t*)body, strlen(body));
  String balas = http.getString();
  http.end();
  Serial.printf("POST %d  %s  -> %s\n", kode, body, balas.c_str());
  return kode == 200;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_LED, OUTPUT);
  analogSetPinAttenuation(PIN_BAT, ADC_11db);
  sambungWiFi();
}

void loop() {
  if (millis() - terakhir < JEDA_MS) { delay(20); return; }
  terakhir = millis();
  sambungWiFi();
  if (WiFi.status() != WL_CONNECTED) return;

  float jarak = bacaJarakCm(), bat = bacaBateraiV();
  digitalWrite(PIN_LED, HIGH);
  bool ok = kirim(jarak, bat);
  digitalWrite(PIN_LED, LOW);

  gagal = ok ? 0 : gagal + 1;
  if (gagal >= 10) { Serial.println("10x gagal, restart..."); delay(1000); ESP.restart(); }
}
