"""Web Push (notifikasi HP/laptop) tanpa pustaka tambahan selain `cryptography`.

Standar yang dipakai:
- RFC 8291: enkripsi isi pesan (aes128gcm) memakai kunci langganan browser (p256dh + auth).
- RFC 8292: VAPID, tanda tangan server (JWT ES256) supaya layanan push (Google/Apple/Mozilla) tahu pesan dari MCI.

Analogi: browser memberi kita "gembok" (p256dh) dan "kode rahasia" (auth). Kita mengunci surat dengan gembok itu,
menempel cap resmi MCI (VAPID), lalu menitipkannya ke kurir push. Kurir tidak bisa membaca isinya.
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(kunci_publik):
    return kunci_publik.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_buat():
    """Pasangan kunci VAPID baru -> (private PEM teks, public base64url untuk browser)."""
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii")
    return pem, b64u(_pub_bytes(k.public_key()))


def _hkdf(salt, ikm, info, n):
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def enkripsi(isi, p256dh, auth, rs=4096):
    """RFC 8291 aes128gcm. isi = bytes. Balik badan pesan siap kirim."""
    ua_pub = unb64u(p256dh)
    auth_secret = unb64u(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub = _pub_bytes(as_priv.public_key())
    bersama = as_priv.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, bersama, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    sandi = AESGCM(cek).encrypt(nonce, isi + b"\x02", None)   # 0x02 = rekaman terakhir
    return salt + struct.pack("!IB", rs, len(as_pub)) + as_pub + sandi


def _jwt_vapid(endpoint, priv_pem, kontak):
    u = urllib.parse.urlparse(endpoint)
    kepala = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    klaim = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": kontak}, separators=(",", ":")).encode())
    k = serialization.load_pem_private_key(priv_pem.encode("ascii"), password=None)
    der = k.sign(f"{kepala}.{klaim}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{kepala}.{klaim}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def kirim(langganan, data, priv_pem, pub_b64, kontak="mailto:admin@mci.local", ttl=86400, batas_detik=10):
    """Kirim satu notifikasi. langganan = {"endpoint", "keys": {"p256dh", "auth"}}; data = dict (jadi JSON).
    Balik kode HTTP (201/200 = terkirim, 404/410 = langganan sudah mati -> hapus)."""
    badan = enkripsi(json.dumps(data, ensure_ascii=False).encode("utf-8")[:3800], langganan["keys"]["p256dh"], langganan["keys"]["auth"])
    req = urllib.request.Request(langganan["endpoint"], data=badan, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "normal",
        "Authorization": f"vapid t={_jwt_vapid(langganan['endpoint'], priv_pem, kontak)}, k={pub_b64}"})
    try:
        with urllib.request.urlopen(req, timeout=batas_detik) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
