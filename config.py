import os
from pathlib import Path
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

DATA_DIR = Path(os.getenv("MENTOR_DATA_DIR") or APP_DIR.parent / "data")
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "mentor.db"
for d in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8770"))
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
ANTHROPIC_KEY_SET = bool(os.getenv("ANTHROPIC_API_KEY"))
OPENAI_KEY_SET = bool(os.getenv("OPENAI_API_KEY"))
MAX_UPLOAD_MB = 200
MENTOR_NAME = os.getenv("MENTOR_NAME") or "멘토"   # 보고서 양식의 멘토명

def upload_path(stored) -> Path:
    """DB에 적힌 파일 경로를 이 PC의 실제 경로로 바꾼다. 다른 OS에서 만든 절대 경로(D:\\...\\data\\uploads\\...)도 uploads 아래 위치로 찾는다."""
    parts = str(stored).replace("\\", "/").split("/")
    if "uploads" in parts:
        return UPLOAD_DIR.joinpath(*parts[len(parts) - 1 - parts[::-1].index("uploads") + 1:])
    return Path(stored)

PHASES = {1: "가설 분해 & 진단", 2: "현장 발견 & 검증", 3: "파일럿 실험 & 측정", 4: "사업 모델 안착"}
# 의뢰 경로. 경로마다 보고 양식이 다를 수 있음: 확인된 양식만 FORMS에 둔다.
CHANNELS = ["PLAB (프랩)", "혁신 소상공인 AI 활용 지원 사업", "기타"]
CHANNEL_FORMS = {"PLAB (프랩)": ["form3", "form4"]}  # 모두의 창업 프로젝트 양식3·4 (admin/ 원본)
PROJECT_STATUSES =["진행 중", "보류", "완료"]
INPUT_KINDS = ["미팅", "자료조사", "멘티 제출물", "기타"]
PRIORITIES = ["긴급", "높음", "보통"]
