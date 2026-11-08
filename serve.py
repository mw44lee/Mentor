"""창 없이(pythonw) 실행할 때 쓰는 시작 파일. 로그는 data/app.log에 남긴다.
작업 스케줄러 '멘토링 업무 관리 앱'이 Windows 로그인 때 이 파일을 실행한다."""
import sys
from config import DATA_DIR, HOST, PORT

log = open(DATA_DIR / "app.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

import uvicorn  # noqa: E402

if __name__ == "__main__":
    from db import init_db
    import notify
    init_db()
    notify.start()   # PC가 켜지거나 깨어나면 접속 링크를 내 메일로
    uvicorn.run("main:app", host=HOST, port=PORT, log_config=None)
