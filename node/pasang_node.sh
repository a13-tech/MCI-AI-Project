#!/bin/bash
# Pasang MCI Node di MacBook supaya jalan otomatis saat login dan tidak tertidur selama dicolok listrik.
# Pakai:  bash pasang_node.sh "/Volumes/NAMA_HDD/MCI-Node" "/Volumes/MCI/Database Server MCI"
#   argumen 1 = folder gudang di HDD (wajib)
#   argumen 2 = folder backup database (opsional, bawaan: <gudang>/backup)
# Token rahasia dibuat otomatis kalau belum ada, lalu ditampilkan SEKALI untuk ditempel di Panel CEO.
set -euo pipefail

GUDANG="${1:-}"
BACKUP="${2:-}"
if [ -z "$GUDANG" ]; then
  echo "Pakai: bash pasang_node.sh \"/Volumes/NAMA_HDD/MCI-Node\" [\"/Volumes/MCI/Database Server MCI\"]"
  exit 1
fi
if [ ! -d "$(dirname "$GUDANG")" ]; then
  echo "Folder $(dirname "$GUDANG") tidak ada. HDD sudah dicolok?"
  exit 1
fi

DIR_INI="$(cd "$(dirname "$0")" && pwd)"
RUMAH="$HOME/MCI-Node"
mkdir -p "$RUMAH" "$GUDANG"
cp "$DIR_INI/mci_node.py" "$RUMAH/mci_node.py"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 belum ada. Jalankan: xcode-select --install  lalu ulangi."
  exit 1
fi

TOKEN_FILE="$RUMAH/.token"
BARU=0
if [ ! -s "$TOKEN_FILE" ]; then
  "$PY" -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  BARU=1
fi
chmod 600 "$TOKEN_FILE"
TOKEN="$(cat "$TOKEN_FILE")"

PLIST="$HOME/Library/LaunchAgents/id.mci.node.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>id.mci.node</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-s</string>
    <string>$PY</string><string>$RUMAH/mci_node.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>NODE_TOKEN</key><string>$TOKEN</string>
    <key>NODE_DIR</key><string>$GUDANG</string>
    <key>BACKUP_DIR</key><string>$BACKUP</string>
    <key>NODE_ASAL</key><string>https://mci-ai.my.id,https://www.mci-ai.my.id</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$RUMAH/node.log</string>
  <key>StandardErrorPath</key><string>$RUMAH/node.log</string>
</dict></plist>
PL
chmod 600 "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 2

if curl -s http://127.0.0.1:8787/sehat | grep -q '"ok"'; then
  echo "MCI Node jalan di http://127.0.0.1:8787 (log: $RUMAH/node.log)"
else
  echo "MCI Node belum menjawab. Cek log: tail -n 30 $RUMAH/node.log"
fi

echo
if [ "$BARU" = "1" ]; then
  echo "TOKEN RAHASIA (tempel ke Panel CEO > Kunci AI > NODE_TOKEN, jangan dikirim ke chat/grup):"
else
  echo "Token lama dipakai lagi (tersimpan di $TOKEN_FILE). Kalau perlu ditempel ulang ke Panel CEO:"
fi
echo "$TOKEN"
