"""PC가 켜지거나 절전에서 깨어나 앱에 접속할 수 있게 되면, 접속 링크를 멘토 본인 Gmail로 보낸다 (받는 사람은 나 자신뿐).

- 메일 계정: app/.env의 GMAIL_ADDRESS·GMAIL_APP_PASSWORD, 없으면 MAIL_ENV_FALLBACK이 가리키는 다른 .env 파일.
  비밀번호는 메일을 보낼 때만 읽고 화면·로그에 남기지 않는다.
- '켜짐' 판단: 앱이 1분마다 남기는 신호(heartbeat)가 5분 넘게 끊겼다가 다시 이어지면 = PC가 꺼져 있었거나 잠들어 있었음.
  앱만 잠깐 다시 시작한 경우(신호 공백이 짧음)에는 보내지 않는다.
- 보내기 전에 로컬 앱과 Tailscale 주소가 실제로 열리는지 확인한다 (최대 10분 기다림).
"""
import json, os, shutil, smtplib, subprocess, sys, threading, time, urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from dotenv import dotenv_values
from config import APP_DIR, PORT
from db import get_setting, set_setting

HEARTBEAT_SEC = 60
GAP_SEC = 5 * 60
READY_WAIT_SEC = 10 * 60
FALLBACK_ENV = Path(os.environ["MAIL_ENV_FALLBACK"]) if os.getenv("MAIL_ENV_FALLBACK") else None
TAILSCALE = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"

def _log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 링크 메일: {msg}", flush=True)

def _creds():
    for path in (APP_DIR / ".env", FALLBACK_ENV):
        if path and path.exists():
            env = dotenv_values(path)
            if env.get("GMAIL_ADDRESS") and env.get("GMAIL_APP_PASSWORD"):
                return env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"], path
    return None, None, None

def tailnet_url() -> str | None:
    try:
        r = subprocess.run([TAILSCALE, "status", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
        dns = (json.loads(r.stdout).get("Self") or {}).get("DNSName", "").rstrip(".")
        return f"https://{dns}" if dns else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None

def _opens(url) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/login", timeout=15) as r:
            return r.status == 200
    except Exception:
        return False

def send_links(reason: str) -> str:
    addr, pw, src = _creds()
    if not addr:
        return "메일 계정이 설정되지 않아 보내지 않음 (GMAIL_ADDRESS·GMAIL_APP_PASSWORD)"
    local = f"http://127.0.0.1:{PORT}"
    deadline = time.time() + READY_WAIT_SEC
    url = None
    while time.time() < deadline:
        url = url or tailnet_url()
        if _opens(local) and url and _opens(url):
            break
        time.sleep(15)
    else:
        note = "외부 주소가 열리지 않음" if url else "Tailscale 주소를 찾지 못함"
        if not _opens(local):
            return f"앱이 열리지 않아 보내지 않음 ({note})"
        reason += f" — 주의: {note}"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = EmailMessage()
    msg["Subject"] = f"[멘토링 업무 관리] 접속 링크 ({stamp})"
    msg["From"] = msg["To"] = addr
    msg.set_content(f"멘토링 업무 관리 앱에 접속할 수 있습니다 — {stamp} ({reason})\n\n"
                    f"- 외부(Tailscale 기기): {url or '확인 필요'}\n- 이 PC: {local}\n\n"
                    "휴대폰은 Tailscale 앱 연결을 켠 뒤 링크를 열고 비밀번호로 로그인하세요. PC가 꺼지면 접속되지 않습니다.")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(addr, pw)
            s.send_message(msg, from_addr=addr, to_addrs=[addr])   # 나에게만
    except (smtplib.SMTPException, OSError) as e:
        return "보내지 못함: " + str(e).replace(pw, "***")[:200]
    return f"{addr}로 보냄 ({reason}, 계정 설정: {src})"

def _notify(reason):
    note = send_links(reason)
    set_setting("last_link_mail", f"{datetime.now():%Y-%m-%d %H:%M} {note}")
    _log(note)

def _loop():
    last = get_setting("heartbeat")
    gap = time.time() - float(last) if last else None
    if gap is None or gap > GAP_SEC:
        _notify("PC 켜짐" if gap is None or gap > GAP_SEC else "앱 시작")
    else:
        _log(f"앱만 다시 시작됨(신호 공백 {gap:.0f}초) — 보내지 않음")
    prev = time.time()
    while True:
        set_setting("heartbeat", str(time.time()))
        time.sleep(HEARTBEAT_SEC)
        now = time.time()
        if now - prev > GAP_SEC + HEARTBEAT_SEC:   # 절전·최대 절전에서 깨어남
            _notify("절전에서 깨어남")
        prev = now

def start():
    threading.Thread(target=_loop, daemon=True, name="link-mail").start()
