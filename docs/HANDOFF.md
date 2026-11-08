# 개발 인수인계

Windows PC에서 만든 앱을 Ubuntu의 Claude Code에서 이어서 개발하기 위한 문서입니다. 공개 저장소이므로 실제 운영 정보는 여기에 적지 않고 `data/HANDOFF_PRIVATE.md`에 있습니다.

## 폴더 구성

```
~/Mentor/
├── app/    이 저장소 (git clone)
└── data/   mentor.db, uploads/, secret.key, app.log, HANDOFF_PRIVATE.md  (git 제외, 따로 옮김)
```

## Ubuntu 준비

1. 소스 받기: README의 '시작' 순서대로 `~/Mentor/app`에 clone
2. Claude Code 설치·로그인 (`claude auth login`). 앱의 AI 기능이 이 로그인을 씁니다.
3. 데이터 옮기기: Windows에서 만든 `mentor-data-날짜.zip`을 복사한 뒤
   ```bash
   .venv/bin/python scripts/backup_data.py --restore mentor-data-날짜.zip
   ```
   비밀번호 해시도 함께 옮겨지므로 같은 비밀번호로 로그인합니다.
4. `.env` 작성: `MENTOR_NAME`은 필수에 가깝고(보고서 멘토명), 메일 알림을 쓰면 `GMAIL_ADDRESS`·`GMAIL_APP_PASSWORD`
5. 소스 동기화: 작업 시작 때 `git pull`, 끝나면 commit·push

### 운영도 Ubuntu로 옮길 때 (아직 결정되지 않음)
Windows의 작업 스케줄러 자동 실행과 `tailscale serve`에 해당하는 설정이 필요합니다. 두 PC에서 동시에 운영하면 데이터가 갈라지므로 DB는 한 곳에서만 씁니다.

`~/.config/systemd/user/mentor-app.service` 예시:
```ini
[Unit]
Description=Mentor app
[Service]
WorkingDirectory=%h/Mentor/app
ExecStart=%h/Mentor/app/.venv/bin/python serve.py
Restart=on-failure
[Install]
WantedBy=default.target
```
```bash
systemctl --user enable --now mentor-app
sudo loginctl enable-linger "$USER"
sudo tailscale serve --bg 8770
```

## 구조

FastAPI + Jinja2 서버 렌더링 + SQLite. 빌드 단계 없음.

| 파일 | 역할 |
| --- | --- |
| `main.py` | 라우트 전체, 로그인 미들웨어 |
| `db.py` | 스키마(`SCHEMA`)와 열 추가(`MIGRATIONS`). 앱 시작 때 자동 적용 |
| `config.py` | 경로, 의뢰 경로(`CHANNELS`, `CHANNEL_FORMS`), `upload_path()` |
| `ai.py` | Claude 호출, 회차 정리(`digest_session`), 의뢰 자료 초안(`intake_draft`) |
| `reports.py` | 양식3·4 프롬프트, 머리 항목, 점검, docx |
| `extract.py` | 업로드 분류, Word·Excel·PDF 글자 추출, Whisper(선택) |
| `process.py`, `docs/process.md` | 신규 의뢰 체크리스트, 업무 프로세스 문서·다운로드 |
| `notify.py`, `serve.py` | 창 없는 실행, PC 켜짐 감지 후 접속 링크 메일 |
| `auth.py`, `manage.py` | scrypt 비밀번호, 로그인 시도 제한, 비밀번호 설정 명령 |
| `scripts/backup_data.py` | 데이터 zip 백업·복원 |

### AI 호출
`ai.call_json()`이 `.env`에 `ANTHROPIC_API_KEY`가 있으면 API, 없으면 `claude -p --json-schema`를 씁니다(구독 계정 사용). 첨부 사진·PDF는 `Read` 도구로 읽게 하고 업로드 폴더 안으로 제한합니다.

### 데이터 흐름
1. 회차별로 기록 올리기 (`inputs`, `input_files`)
2. 회차 단위 AI 정리·재정리 (`session_digests`) — 그 회차의 기록 전부를 읽음. `session_id`가 NULL이면 회차 밖 기록
3. 저장되는 것: PLAB 경로면 양식3 초안 새 버전(`reports`), 승인 대기 제안, 관찰 사실(양식4 생성에 사용)
4. 검토 화면에서 체크한 제안만 체크리스트·미결·일정에 반영
5. 기록 상태: `대기`(정리 전) → `정리됨` → `반영됨`. 기록 삭제 시 첨부는 `uploads/_deleted`로 이동

### 의뢰 경로
- PLAB: 모두의 창업 프로젝트 양식3·4 사용
- 혁신 소상공인 AI 활용지원 사업: 업무 프로세스 7~9장. 보고 양식 미확인이라 '보고서용 정리'만 저장

## 진행 상태

완료: 로그인, 현황, 새 프로젝트(직접·자료 기반), 회차·기록 통합 탭, 회차별 AI 정리·재정리, 검토 반영, 기록 삭제, 양식3·4 생성·피드백·버전·docx, 업무 프로세스 페이지·다운로드, 접속 링크 메일.

## 검증되지 않은 것
- **Ubuntu에서 실행해 본 적 없음.** 경로와 실행 파일 탐색은 Linux를 고려해 고쳤지만, Windows에서만 테스트했습니다. 첫 작업으로 실행 확인이 필요합니다.
- `requirements.txt`는 Python 3.14에서 고정한 버전입니다. Ubuntu 기본 Python에서 설치되는지 확인 필요.
- 작업 스케줄러로 켜진 앱 안에서 AI 버튼을 누르는 경로, 실제 PC 재부팅·절전 해제 시 링크 메일, 자료 기반 새 프로젝트 화면의 브라우저 확인
- OpenAI Whisper 실제 호출 (키 없음, 기본 흐름은 변환본 업로드)

## 남은 일
- 혁신 소상공인 AI 사업의 보고 양식을 받으면 `CHANNEL_FORMS`와 생성 기능 추가
- 데이터 정기 백업 위치 결정
- 운영을 어느 PC에서 할지 결정
- 알려진 제약: 로그인 시도 제한은 IP 기준인데 Tailscale serve 뒤에서는 모든 접속이 127.0.0.1로 보여 전체가 함께 잠김
