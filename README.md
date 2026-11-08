# 멘토링 업무 관리 앱

멘토 1인이 여러 멘토링 의뢰를 관리하는 웹앱입니다. 의뢰 등록, 회차별 기록, AI 정리, 체크리스트·미결 사항 관리, 공식 보고서 초안 작성을 한곳에서 합니다.

저장소에는 **코드만** 있습니다. 멘티 기록·업로드 파일·비밀번호·키는 앱 폴더 밖 `../data/`와 `.env`에 있으며 올리지 않습니다.

## 시작

```bash
git clone https://github.com/mw44lee/Mentor.git ~/Mentor/app
cd ~/Mentor/app
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python manage.py set-password
./run.sh
```

브라우저에서 http://127.0.0.1:8770 을 엽니다. Windows는 `run.bat`을 씁니다.

AI 기능은 그 PC에 설치·로그인된 Claude Code(`claude -p`)를 씁니다. API 키는 선택 사항입니다.

## 기능

- **현황:** 진행 중·완료 프로젝트, 회차 진행, 체크리스트 진행률, 다음 단계
- **새 프로젝트:** 직접 입력, 또는 의뢰 자료(파일·텍스트)로 AI 초안 작성 후 확인
- **회차·기록:** 회차별로 메모·사진·문서·녹음 변환본을 올리고, 회차 단위로 AI 정리·재정리
- **검토 후 반영:** AI 제안은 체크한 것만 체크리스트·미결·일정에 반영
- **보고서:** 양식3·양식4 초안 생성, 피드백 재생성, 직접 수정(버전 보존), docx 다운로드
- **업무 프로세스:** 표준 절차 페이지, md·docx 다운로드

개발 정보는 [docs/HANDOFF.md](docs/HANDOFF.md)에 있습니다.
