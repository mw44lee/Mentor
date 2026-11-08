"""데이터 폴더를 zip 하나로 묶는다 (PC 이전·백업용). 앱이 켜져 있어도 DB는 일관된 상태로 복사된다.

사용: python scripts/backup_data.py [저장할 zip 경로]
받는 쪽: python scripts/backup_data.py --restore 받은.zip

zip에는 멘티 기록, 비밀번호 해시, 세션 비밀키가 들어 있다. GitHub 등 공개된 곳에 올리지 말 것.
"""
import sqlite3, sys, tempfile, zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DATA_DIR, DB_PATH  # noqa: E402

SKIP = {"app.log", "mentor.db", "mentor.db-wal", "mentor.db-shm"}

def backup(dest: Path):
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "mentor.db"
        src = sqlite3.connect(DB_PATH); dst = sqlite3.connect(snap)
        src.backup(dst); dst.close(); src.close()
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "mentor.db")
            for f in DATA_DIR.rglob("*"):
                if f.is_file() and f.name not in SKIP:
                    z.write(f, f.relative_to(DATA_DIR).as_posix())
    print(f"저장: {dest} ({dest.stat().st_size / 1048576:.1f}MB)")

def restore(src: Path):
    if DB_PATH.exists():
        sys.exit(f"이미 DB가 있습니다: {DB_PATH} — 덮어쓰지 않습니다. 옮겨 둔 뒤 다시 실행하세요.")
    with zipfile.ZipFile(src) as z:
        z.extractall(DATA_DIR)
    print(f"복원: {DATA_DIR}")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--restore"] and len(args) == 2:
        restore(Path(args[1]))
    else:
        backup(Path(args[0]) if args else DATA_DIR.parent / f"mentor-data-{datetime.now():%Y%m%d}.zip")
