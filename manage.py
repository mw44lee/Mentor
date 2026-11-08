"""관리 명령. 사용법: .venv\Scripts\python manage.py set-password"""
import sys, getpass
from db import init_db
from auth import set_password

def main():
    init_db()
    if len(sys.argv) < 2 or sys.argv[1] != "set-password":
        print(__doc__); return
    pw = getpass.getpass("새 비밀번호 (8자 이상): ")
    if len(pw) < 8:
        print("8자 이상이어야 합니다."); return
    if pw != getpass.getpass("한 번 더 입력: "):
        print("두 입력이 다릅니다."); return
    set_password(pw)
    print("비밀번호를 저장했습니다.")

if __name__ == "__main__":
    main()
