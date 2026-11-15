"""Ключи VAPID для Web Push. Запуск внутри контейнера app: пишет VAPID_PUBLIC_KEY и VAPID_PRIVATE_KEY в .env,
если их там ещё нет. Значения не печатает: приватный ключ это секрет.

docker compose -f docker-compose.server.yml -p jobsuccess exec -T app python /app/scripts/gen_vapid.py /app/.env
"""

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

env = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
text = env.read_text(encoding="utf-8") if env.exists() else ""
if "VAPID_PRIVATE_KEY=" in text:
    print("VAPID уже есть, ничего не меняю")
    sys.exit(0)
key = ec.generate_private_key(ec.SECP256R1())
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
private = b64(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
public = b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
with env.open("a", encoding="utf-8") as f:
    f.write(("" if text.endswith("\n") or not text else "\n") + f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}\n")
print("VAPID записан в", env)
