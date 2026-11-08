import hashlib, hmac, os, secrets, time
from config import DATA_DIR
from db import get_setting, set_setting

def secret_key() -> str:
    p = DATA_DIR / "secret.key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip()

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()

def verify_password(pw: str) -> bool:
    stored = get_setting("password_hash")
    if not stored:
        return False
    salt_hex, h_hex = stored.split("$")
    h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(h.hex(), h_hex)

def password_is_set() -> bool:
    return bool(get_setting("password_hash"))

def set_password(pw: str):
    set_setting("password_hash", hash_password(pw))

# 로그인 실패 제한: IP별 10분 동안 5회
_fails: dict[str, list[float]] = {}
def too_many_attempts(ip: str) -> bool:
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 600]
    return len(_fails[ip]) >= 5

def record_fail(ip: str):
    _fails.setdefault(ip, []).append(time.time())
