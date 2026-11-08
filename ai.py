"""Claude 호출: 입력 정리(제안 생성)와 보고서 초안. 결과는 제안일 뿐이며, 멘토가 검토 화면에서 승인한 것만 반영된다.

기본: 이 PC의 Claude Code(`claude -p`)를 불러 멘토의 구독 계정 사용량으로 처리 (API 키 불필요).
선택: app\\.env에 ANTHROPIC_API_KEY를 넣으면 API로 처리.
"""
import base64, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from config import CLAUDE_MODEL, upload_path
from db import get_db

CLI_MODEL = os.getenv("CLAUDE_CLI_MODEL") or None   # 비우면 Claude Code 계정 기본 모델
CLI_TIMEOUT_SEC = 900
LOGIN_HELP = "PC 터미널에서 `claude auth login`으로 다시 로그인하거나, `claude setup-token`으로 만든 장기 토큰을 app\\.env의 CLAUDE_CODE_OAUTH_TOKEN에 넣어 주세요."

class AIError(Exception):
    pass

def _find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    return str(fallback) if fallback.exists() else None

def backend() -> str | None:
    """'api' | 'cli' | None (쓸 수 있는 것 없음)"""
    if os.getenv("ANTHROPIC_API_KEY"):
        return "api"
    return "cli" if _find_claude() else None

def ai_ready() -> bool:
    return backend() is not None

def model_label() -> str:
    b = backend()
    if b == "api":
        return f"API · {CLAUDE_MODEL}"
    if b == "cli":
        return f"Claude Code · {CLI_MODEL or '계정 기본 모델'}"
    return "AI 연결 없음"

def _log_usage(purpose, model, inp, out, project_id):
    with get_db() as con:
        con.execute("INSERT INTO ai_usage(purpose,model,input_tokens,output_tokens,project_id) VALUES(?,?,?,?,?)",
                    (purpose, model, inp, out, project_id))

def call_json(system: str, content: list, schema: dict, purpose: str, project_id: int, max_tokens: int = 16000) -> dict:
    """구조화 출력(JSON 스키마)으로 Claude를 한 번 호출.
    content: [{"type":"text",...}, {"type":"image"|"document", "source":..., "_path": 파일 경로}, ...]"""
    b = backend()
    if b == "api":
        return _call_api(system, content, schema, purpose, project_id, max_tokens)
    if b == "cli":
        return _call_cli(system, content, schema, purpose, project_id)
    raise AIError("AI를 쓸 수 없습니다: 이 PC에서 Claude Code(claude)를 찾지 못했고 API 키도 없습니다.")

def _is_login_error(msg: str) -> bool:
    m = (msg or "").lower()
    return ("failed to authenticate" in m or "not logged in" in m or "please run /login" in m
            or ("oauth" in m and ("expired" in m or "could not be refreshed" in m)))

def _call_cli(system, content, schema, purpose, project_id) -> dict:
    """Claude Code를 `claude -p`로 부른다. 도구는 첨부 파일 읽기(Read)만, 첨부가 있는 폴더 안으로 제한."""
    exe = _find_claude()
    texts, files = [], []
    for blk in content:
        if blk.get("type") == "text":
            texts.append(blk["text"])
        elif blk.get("_path"):
            files.append(Path(blk["_path"]))
    prompt = "\n\n".join(texts)
    folders = {f.parent for f in files}
    if len(folders) > 1:   # 한 프로젝트의 업로드는 한 폴더에 있다
        raise AIError("첨부 파일이 여러 폴더에 있어 읽을 수 없습니다.")
    if files:
        prompt += "\n\n[첨부 파일 — Read 도구로 직접 열어 보고 내용에 포함하세요]\n" + "\n".join(f"- {f.name}" for f in files)
    fd, sp_path = tempfile.mkstemp(suffix=".txt", prefix="mentor_sys_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(system)
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
           "--system-prompt-file", sp_path, "--json-schema", json.dumps(schema, ensure_ascii=False)]
    if files:
        cwd = str(next(iter(folders)))
        cmd += ["--tools", "Read", "--allowedTools", "Read(./**)", "--max-turns", str(4 + 2 * len(files))]
    else:
        cwd = tempfile.gettempdir()
        cmd += ["--tools", ""]
    if CLI_MODEL:
        cmd += ["--model", CLI_MODEL]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=cwd, timeout=CLI_TIMEOUT_SEC, env=dict(os.environ),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        raise AIError(f"AI 응답 시간 초과({CLI_TIMEOUT_SEC // 60}분). 입력을 나눠서 다시 시도하세요.")
    except OSError as e:
        raise AIError(f"Claude Code 실행 실패: {e}")
    finally:
        try:
            os.remove(sp_path)
        except OSError:
            pass
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        detail = (r.stderr or r.stdout or "").strip()[-300:]
        if _is_login_error(detail):
            raise AIError(f"Claude 로그인이 풀렸습니다. {LOGIN_HELP}")
        raise AIError(f"AI 응답을 읽지 못했습니다 (종료 코드 {r.returncode}): {detail or '내용 없음'}")
    u = data.get("usage") or {}
    inp = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    _log_usage(purpose, "claude-code:" + ",".join((data.get("modelUsage") or {}).keys()), inp, int(u.get("output_tokens") or 0), project_id)
    if data.get("is_error") or r.returncode != 0:
        msg = str(data.get("result") or data.get("subtype") or r.stderr.strip()[:300])
        if _is_login_error(msg):
            raise AIError(f"Claude 로그인이 풀렸습니다. {LOGIN_HELP}")
        if "limit" in msg.lower():
            raise AIError(f"Claude 사용 한도에 걸렸습니다. 한도가 풀린 뒤 다시 시도하세요. ({msg[:150]})")
        raise AIError(f"AI 오류: {msg[:300]}")
    out = data.get("structured_output")
    if not isinstance(out, dict):
        try:
            out = json.loads(data.get("result") or "")
        except json.JSONDecodeError:
            raise AIError("AI 응답 형식이 맞지 않습니다. 다시 시도하세요.")
    return out

def _call_api(system, content, schema, purpose, project_id, max_tokens) -> dict:
    """Anthropic API (키가 있을 때만). 안전 분류로 거절되면 서버 측 대체 모델로 재시도."""
    import anthropic
    client = anthropic.Anthropic()
    content = [{k: v for k, v in blk.items() if k != "_path"} for blk in content]
    try:
        resp = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise AIError("Anthropic API 키가 올바르지 않습니다.")
    except anthropic.PermissionDeniedError:
        raise AIError("API 키에 권한이 없습니다 (결제·크레딧 확인).")
    except anthropic.RateLimitError:
        raise AIError("요청이 많아 잠시 제한되었습니다. 1분 뒤 다시 시도하세요.")
    except anthropic.BadRequestError as e:
        raise AIError(f"요청 오류: {e.message}")
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude 서버 오류 ({e.status_code}). 잠시 뒤 다시 시도하세요.")
    except anthropic.APIConnectionError:
        raise AIError("인터넷 연결을 확인하세요.")
    _log_usage(purpose, resp.model, getattr(resp.usage, "input_tokens", None), getattr(resp.usage, "output_tokens", None), project_id)
    if resp.stop_reason == "refusal":
        raise AIError("AI가 이 입력의 처리를 거절했습니다. 내용을 확인한 뒤 직접 정리해 주세요.")
    if resp.stop_reason == "max_tokens":
        raise AIError("입력이 너무 길어 정리가 중간에 끊겼습니다. 입력을 나눠서 올려 주세요.")
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise AIError("AI 응답을 읽지 못했습니다. 다시 시도하세요.")

# ---------------- 입력 정리 ----------------
S = {"type": "string"}
def _arr(props, req=None):
    return {"type": "array", "items": {"type": "object", "properties": props,
            "required": req or list(props), "additionalProperties": False}}

# ---------------- 의뢰 자료로 새 프로젝트 초안 ----------------
def intake_schema():
    from config import CHANNELS
    N = {"type": ["integer", "null"]}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "summary": S,
            "brand": S, "service": S, "mentee": S, "org": S,
            "channel": {"type": "string", "enum": CHANNELS + ["확인 필요"]},
            "goal": S, "item": S, "critical_question": S,
            "planned_sessions": N, "planned_hours": {"type": ["number", "null"]},
            "start_date": S, "end_date": S,
            "first_session": {"type": "object", "additionalProperties": False,
                              "properties": {k: S for k in ["date", "start_time", "end_time", "place", "method"]},
                              "required": ["date", "start_time", "end_time", "place", "method"]},
            "requests": _arr({"text": S, "source": S}),
            "milestones": _arr({"date": S, "label": S}),
            "questions": _arr({"text": S, "owner": S, "priority": {"type": "string", "enum": ["긴급", "높음", "보통"]}}),
            "notes": S,
        },
        "required": ["summary", "brand", "service", "mentee", "org", "channel", "goal", "item", "critical_question",
                     "planned_sessions", "planned_hours", "start_date", "end_date", "first_session", "requests",
                     "milestones", "questions", "notes"],
    }

INTAKE_SYSTEM = """당신은 창업 멘토의 업무 보조입니다. 새로 들어온 멘토링 의뢰 자료(신청서, 사업계획서, 안내 메일, 메모, 캡처 등)를 읽고
새 프로젝트 등록 양식의 초안을 채웁니다. 멘토가 검토하고 고친 뒤 저장합니다.

원칙:
- 자료에 실제로 적힌 사실만 씁니다. 추측해서 채우지 않습니다. 없으면 "" 또는 null로 둡니다.
- 채우지 못했거나 애매한 중요 항목(의뢰 경로, 회차 수·시간, 일정, 진출자 정보, 보고 양식 등)은 questions에 "무엇을 확인해야 하는지"로 넣습니다.
- channel: 자료에 사업명이 나오면 그에 맞는 경로. 'PLAB'·'모두의 창업' → "PLAB (프랩)", '혁신 소상공인 AI 활용지원' → 그 경로. 판단할 근거가 없으면 "확인 필요".
- brand: 멘티의 브랜드·상호 / service: 멘토링 대상 아이템·서비스 이름 / mentee: 멘티(대표자) 이름 / org: 운영기관
- goal: 멘티가 하고자 하는 일 1~2문장 / item: 핵심 아이템 설명 / critical_question: 이번 멘토링에서 답을 찾아야 할 근본 질문 1개 (자료로 도출할 수 있을 때만)
- requests: 멘티가 멘토에게 요청하거나 고민으로 적은 것들 (각각 한 줄, source에 어느 자료의 어디인지)
- milestones: 날짜가 있는 일정(마감·회차·발표 등). date는 YYYY-MM-DD, 연도가 없으면 자료의 맥락으로 판단하되 애매하면 questions로.
- first_session: 1회차 일시·장소가 자료에 있으면. 시각은 HH:MM, 방법은 '오프라인'/'온라인'.
- planned_sessions·planned_hours: 자료에 명시된 경우만 숫자, 아니면 null.
- notes: 멘토가 알아 두면 좋은 사실(수당·조건·특이사항 등)을 출처와 함께 짧게.
- summary: 자료 전체 요약 3~5문장. 한국어로 간결하게."""

def intake_draft(text: str, files: list[dict]) -> dict:
    """files: [{"path","filename","media","mime","extracted_text"}]"""
    content = [{"type": "text", "text": "[의뢰 자료]\n" + (f"[멘토 메모·붙여넣은 텍스트]\n{text}" if text else "(메모 없음)")}]
    body = []
    for f in files:
        path = upload_path(f["path"])
        if f["media"] == "image":
            content.append({"type": "image", "_path": str(path), "source": {"type": "base64", "media_type": f["mime"],
                            "data": base64.standard_b64encode(path.read_bytes()).decode()}})
            body.append(f"[첨부 사진: {f['filename']} — 파일명 {path.name}]")
        elif f["media"] == "pdf" and not f.get("extracted_text"):
            content.append({"type": "document", "_path": str(path), "source": {"type": "base64", "media_type": "application/pdf",
                            "data": base64.standard_b64encode(path.read_bytes()).decode()}})
            body.append(f"[첨부 PDF: {f['filename']} — 파일명 {path.name}]")
        elif f.get("extracted_text"):
            body.append(f"[첨부 문서: {f['filename']}]\n{f['extracted_text']}")
        else:
            body.append(f"[첨부: {f['filename']}] (내용을 읽을 수 없음)")
    if body:
        content.append({"type": "text", "text": "\n\n".join(body)})
    return call_json(INTAKE_SYSTEM, content, intake_schema(), "새 프로젝트 초안", None)

def _project_context(con, pid, session_id):
    p = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    reqs = con.execute("SELECT code, text FROM requests WHERE project_id=?", (pid,)).fetchall()
    ck = con.execute("""SELECT c.id, c.text, c.due, r.code rcode FROM checklist c LEFT JOIN requests r ON r.id=c.request_id
                        WHERE c.project_id=? AND c.done=0""", (pid,)).fetchall()
    qs = con.execute("SELECT code, text FROM questions WHERE project_id=? AND status='미결'", (pid,)).fetchall()
    ss = con.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY no", (pid,)).fetchall()
    lines = [f"[프로젝트] {p['code']} {p['brand'] or ''} {p['service'] or ''} / 멘티 {p['mentee'] or '-'}",
             f"하려는 일: {p['goal'] or '-'}", f"핵심 질문: {p['critical_question'] or '-'}", "", "[멘티 요청]"]
    lines += [f"{r['code']}: {r['text']}" for r in reqs] or ["(없음)"]
    lines += ["", "[미완료 체크리스트] (id: 내용)"]
    lines += [f"{c['id']}: {c['text']}" + (f" [{c['rcode']}]" if c["rcode"] else "") + (f" (마감 {c['due']})" if c["due"] else "") for c in ck] or ["(없음)"]
    lines += ["", "[미결 사항]"] + ([f"{q['code']}: {q['text']}" for q in qs] or ["(없음)"])
    lines += ["", "[회차]"]
    for s in ss:
        lines.append(f"{s['no']}회차: {s['status']} {s['date'] or '날짜 미정'} {s['start_time'] or ''}~{s['end_time'] or ''} {s['place'] or ''} 주제: {s['topic'] or '-'}")
    if session_id:
        s = con.execute("SELECT no FROM sessions WHERE id=?", (session_id,)).fetchone()
        if s:
            lines += ["", f"※ 멘토가 이 입력을 {s['no']}회차 기록으로 지정했습니다."]
    return "\n".join(lines)

def digest_schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "summary": S,
            "session_meta": _arr({"no": {"type": "integer"}, "date": S, "start_time": S, "end_time": S, "place": S, "method": S,
                                  "topic": S, "held": {"type": "boolean"}}),
            "checklist_done": _arr({"id": {"type": "integer"}, "evidence": S}),
            "checklist_new": _arr({"text": S, "request_code": S, "due": S, "reason": S}),
            "questions_new": _arr({"text": S, "owner": S, "due": S, "priority": {"type": "string", "enum": ["긴급", "높음", "보통"]}}),
            "questions_resolved": _arr({"code": S, "decision": S, "basis": S}),
            "requests_new": _arr({"text": S, "source": S}),
            "milestones_new": _arr({"date": S, "label": S}),
            "research_note": S,
            "uncertain": {"type": "array", "items": S},
            "report": {"type": "object", "additionalProperties": False,
                       "properties": {"topic": S, "content": S, "missing": {"type": "array", "items": S}},
                       "required": ["topic", "content", "missing"]},
            "observations": {"type": "object", "additionalProperties": False,
                             "properties": {"diligence": S, "growth": S, "excellence": S},
                             "required": ["diligence", "growth", "excellence"]},
        },
        "required": ["summary", "session_meta", "checklist_done", "checklist_new", "questions_new", "questions_resolved",
                     "requests_new", "milestones_new", "research_note", "uncertain", "report", "observations"],
    }

DIGEST_SYSTEM = """당신은 창업 멘토의 업무 보조입니다. 한 회차(또는 '회차 밖 기록' 묶음)에 올라온 기록 전부
(미팅 메모, 자료조사, 멘티 제출물, 사진, 녹음 전사본 등)를 함께 읽고 정리합니다.
정리 결과는 ① 체크리스트·미결 사항 제안(멘토가 승인한 것만 반영) ② 보고서 초안 ③ 관찰 사실로 쓰입니다.

원칙:
- 기록에 실제로 있는 사실만 씁니다. 추측하거나 그럴듯하게 채우지 않습니다. 해당 내용이 없으면 빈 문자열("")이나 빈 배열을 둡니다.
- 기록만으로 확정할 수 없는 일정·수치·결정은 제안하지 말고 'uncertain'에 "무엇을 확인해야 하는지"로 적습니다.
- 기록에 다른 회차 이야기가 섞여 있으면, 정리 대상 회차의 내용만 보고서에 쓰고 다른 회차 내용은 맥락으로만 짧게 언급합니다.
- checklist_done: 기록에 그 항목이 끝났다는 근거가 분명할 때만, 근거 문장과 함께. id는 주어진 목록의 id만.
- questions_resolved: 기록에 그 미결 사항의 답이 분명히 있을 때만. code는 주어진 목록의 code만.
- checklist_new: 기록에서 도출된 새 실행 항목(숙제·준비물 등). 관련 요청이 있으면 request_code(R1 등), 없으면 "". due는 날짜가 있을 때만 YYYY-MM-DD.
- session_meta: 기록에 회차 기본 정보(일자 YYYY-MM-DD, 시작·종료 HH:MM, 장소, 진행방법 '오프라인'/'온라인', 주제)가 명시돼 있을 때만,
  회차별로 하나씩. 모르는 칸은 "". held는 그 회차가 실제로 진행됐다는 근거가 있으면 true.
- research_note: 자료조사 기록이 있으면 핵심을 출처(링크·문서명)와 함께. 아니면 "".
- report: 보고서 초안. topic은 한 줄(40자 이내) 주제, content는 아래 [보고서 작성 규칙]을 따릅니다.
  길이를 맞추려고 반복하거나 일반론을 쓰지 않습니다. 사실이 부족하면 있는 사실만 쓰고 missing에 무엇을 더 기록하면 되는지 적습니다.
  공식 보고서 문체(~함, ~하였음, ~임), 마크다운 기호 없이, 문단은 빈 줄로 구분합니다.
- observations: 관찰의견서용 '관찰된 사실'만 (성실성: 참여 적극성·창업 의지·준비 상황 / 성장성: 피드백 수용·사업계획 구체화 /
  우수성: 아이디어 고도화 과정의 강점). 평가 형용사보다 행동·사실 위주. 근거 없으면 "".
- 멘토 수당·정산, 멘티 개인 연락처 등은 보고서에 쓰지 않습니다.
- 멘토의 재정리 요청이 주어지면 그것을 최우선으로 반영합니다.
- summary: 이 묶음의 기록 전체를 3~5문장으로 요약. 한국어로 간결하게."""

REPORT_RULES = {
    "form3": "[보고서 작성 규칙] 「회차별 멘토링 보고서」(양식3)의 '멘토링 내용'. 공백 포함 1,000자 이상(목표 1,200~1,600자). "
             "순서: 1) 진행 개요와 멘티 현황(지난 과제 결과 포함) 2) 주요 논의와 멘토 의견 3) 함께 도출한 결과물 4) 차기 과제와 다음 회차 계획.",
    "session": "[보고서 작성 규칙] 이 회차의 보고서용 정리(양식 미확인 경로). 600~1,200자. "
               "순서: 1) 진행 개요와 멘티 현황 2) 주요 논의와 멘토 의견 3) 결과물 4) 차기 과제.",
    "loose": "[보고서 작성 규칙] 회차에 속하지 않는 기록(자료조사, 멘티 제출물, 연락 등)의 정리. 400~1,000자. "
             "각 기록이 어떤 내용이고 이후 회차·보고에 어떻게 쓰일 수 있는지 정리.",
}

def _record_blocks(con, inputs) -> list:
    """기록 여러 건 → Claude에 보낼 내용 블록 (사진·PDF는 파일 경로 포함)."""
    content, body = [], []
    for inp in inputs:
        body.append(f"=== 기록 #{inp['id']} · 종류 {inp['kind']} · 올린 시각 {inp['created_at']} ===")
        if inp["text"]:
            body.append(f"[메모]\n{inp['text']}")
        for f in con.execute("SELECT * FROM input_files WHERE input_id=? ORDER BY id", (inp["id"],)):
            path = upload_path(f["stored_path"])
            if not path.exists():
                body.append(f"[첨부: {f['filename']}] (파일 없음)"); continue
            if f["media"] == "image":
                content.append({"type": "image", "_path": str(path), "source": {"type": "base64", "media_type": f["mime"],
                                "data": base64.standard_b64encode(path.read_bytes()).decode()}})
                body.append(f"[첨부 사진: {f['filename']} — 파일명 {path.name}]")
            elif f["media"] == "pdf" and not f["extracted_text"]:
                content.append({"type": "document", "_path": str(path), "source": {"type": "base64", "media_type": "application/pdf",
                                "data": base64.standard_b64encode(path.read_bytes()).decode()}})
                body.append(f"[첨부 PDF: {f['filename']} — 파일명 {path.name}]")
            elif f["extracted_text"]:
                label = "녹음 전사본" if f["media"] == "audio" else "첨부 문서"
                body.append(f"[{label}: {f['filename']}]\n{f['extracted_text']}")
            else:
                body.append(f"[첨부: {f['filename']}] (내용을 읽을 수 없음: {f['note'] or '형식 미지원'})")
    content.append({"type": "text", "text": "\n\n".join(body)})
    return content

def digest_session(pid: int, session_id: int | None, feedback: str | None = None, rules: str = "session") -> dict:
    """한 회차(session_id) 또는 회차 밖 기록(None)의 모든 기록을 한 번에 정리."""
    with get_db() as con:
        ctx = _project_context(con, pid, None)
        if session_id:
            s = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            target = (f"[정리 대상] {s['no']}회차 (일자 {s['date'] or '미정'} {s['start_time'] or ''}~{s['end_time'] or ''}, "
                      f"장소 {s['place'] or '미정'}, 방법 {s['method'] or '미정'}, 예정 주제 {s['topic'] or '-'})")
            inputs = con.execute("SELECT * FROM inputs WHERE project_id=? AND session_id=? ORDER BY created_at, id", (pid, session_id)).fetchall()
            prev = con.execute("""SELECT s.no, d.result FROM session_digests d JOIN sessions s ON s.id=d.session_id
                                  WHERE d.project_id=? AND s.no<? AND d.id IN (SELECT MAX(id) FROM session_digests WHERE project_id=? GROUP BY session_id)
                                  ORDER BY s.no""", (pid, s["no"], pid)).fetchall()
        else:
            target = "[정리 대상] 회차 밖 기록 (자료조사, 멘티 제출물, 연락 등)"
            inputs = con.execute("SELECT * FROM inputs WHERE project_id=? AND session_id IS NULL ORDER BY created_at, id", (pid,)).fetchall()
            prev = []
        blocks = _record_blocks(con, inputs)
    prev_txt = "\n\n".join(f"{r['no']}회차 정리 요약: {json.loads(r['result']).get('summary', '')}" for r in prev) or "(없음)"
    head = "\n\n".join([ctx, target, f"[이전 회차 정리 요약 — 중복 서술 피하기용]\n{prev_txt}", REPORT_RULES[rules],
                        f"[기록 {len(inputs)}건 — 아래 전부를 함께 읽고 정리]"])
    content = [{"type": "text", "text": head}] + blocks
    if feedback:
        content.append({"type": "text", "text": f"[멘토의 재정리 요청 — 이것을 반영해 다시 정리]\n{feedback}"})
    return call_json(DIGEST_SYSTEM, content, digest_schema(), "회차 정리" if session_id else "회차 밖 기록 정리", pid)
