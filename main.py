from datetime import date, datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
import json, re
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
from config import APP_DIR, PHASES, PROJECT_STATUSES, INPUT_KINDS, PRIORITIES, HOST, PORT, CHANNELS, CHANNEL_FORMS
from db import get_db, init_db

init_db()
app = FastAPI(title="멘토링 업무 관리", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
tpl = Jinja2Templates(directory=APP_DIR / "templates")

WEEKDAYS = "월화수목금토일"
def fmt_date(s):
    if not s:
        return ""
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        return s
    return f"{d.month}/{d.day:02d}({WEEKDAYS[d.weekday()]})"
tpl.env.filters["d"] = fmt_date
tpl.env.filters["fromjson"] = lambda s: json.loads(s) if s else []
tpl.env.globals.update(PHASES=PHASES, PROJECT_STATUSES=PROJECT_STATUSES, INPUT_KINDS=INPUT_KINDS, PRIORITIES=PRIORITIES,
                       CHANNELS=CHANNELS, CHANNEL_FORMS=CHANNEL_FORMS)

PUBLIC = ("/login", "/static")

@app.middleware("http")
async def require_login(request: Request, call_next):
    if not request.url.path.startswith(PUBLIC) and not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)

# 나중에 추가한 미들웨어가 바깥에서 먼저 실행되므로, 세션은 로그인 검사보다 뒤에 등록
app.add_middleware(SessionMiddleware, secret_key=auth.secret_key(), same_site="lax",
                   max_age=60 * 60 * 24 * 14, session_cookie="mentor_session")

def render(request, name, **ctx):
    ctx.setdefault("flash", request.session.pop("flash", None) if "session" in request.scope else None)
    return tpl.TemplateResponse(request, name, ctx)

def back(url):
    return RedirectResponse(url, status_code=303)

def blank(v):
    v = (v or "").strip()
    return v or None

# ---------- 로그인 ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", error=None, pw_set=auth.password_is_set())

@app.post("/login")
def login(request: Request, password: str = Form(...)):
    ip = request.client.host if request.client else "?"
    if auth.too_many_attempts(ip):
        return render(request, "login.html", error="로그인 실패가 많아 10분 동안 잠겼습니다.", pw_set=True)
    if not auth.verify_password(password):
        auth.record_fail(ip)
        return render(request, "login.html", error="비밀번호가 맞지 않습니다.", pw_set=auth.password_is_set())
    request.session["user"] = "mentor"
    return back("/")

@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return back("/login")

# ---------- 대시보드 ----------
def project_summary(con, p):
    pid = p["id"]
    today = date.today().isoformat()
    sessions = con.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY no", (pid,)).fetchall()
    done_sessions = [s for s in sessions if s["status"] == "완료"]
    ck = con.execute("SELECT COUNT(*) n, SUM(done) d FROM checklist WHERE project_id=? AND approved=1", (pid,)).fetchone()
    open_q = con.execute("SELECT COUNT(*) FROM questions WHERE project_id=? AND status='미결'", (pid,)).fetchone()[0]
    pending_inputs = con.execute("SELECT COUNT(*) FROM inputs WHERE project_id=? AND state='대기'", (pid,)).fetchone()[0]
    # 다음 단계: 남은 일정 + 마감 있는 미완료 체크리스트 중 가장 이른 2개
    steps = [dict(date=r["date"], label=r["label"]) for r in con.execute(
        "SELECT date,label FROM milestones WHERE project_id=? AND done=0 AND date>=? ORDER BY date LIMIT 3", (pid, today))]
    for s in sessions:
        if s["status"] == "예정" and s["date"] and s["date"] >= today:
            label = f"{s['no']}회차 멘토링" + (f" {s['start_time']}" if s["start_time"] else "") + (f" · {s['place']}" if s["place"] else "")
            steps.append(dict(date=s["date"], label=label))
    steps += [dict(date=r["due"], label=r["text"]) for r in con.execute(
        "SELECT due,text FROM checklist WHERE project_id=? AND approved=1 AND done=0 AND due IS NOT NULL AND due>=? ORDER BY due LIMIT 3", (pid, today))]
    seen, uniq = set(), []
    for s in sorted(steps, key=lambda x: x["date"]):
        if s["label"] not in seen:
            seen.add(s["label"]); uniq.append(s)
    hours = 0.0
    for s in done_sessions:
        try:
            t1 = datetime.strptime(s["start_time"], "%H:%M"); t2 = datetime.strptime(s["end_time"], "%H:%M")
            hours += (t2 - t1).seconds / 3600
        except (TypeError, ValueError):
            pass
    reports_done = con.execute("SELECT COUNT(DISTINCT form) FROM reports WHERE project_id=? AND form=4", (pid,)).fetchone()[0]
    return dict(p=p, sessions=sessions, planned=max(p["planned_sessions"], len(sessions)),
                done_n=len(done_sessions), hours=hours, ck_total=ck["n"] or 0, ck_done=ck["d"] or 0,
                open_q=open_q, pending_inputs=pending_inputs, next_steps=uniq[:2], report_done=reports_done > 0)

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, view: str = "active", ch: str = ""):
    with get_db() as con:
        if view == "done":
            rows = con.execute("SELECT * FROM projects WHERE status='완료' ORDER BY completed_at DESC").fetchall()
        else:
            rows = con.execute("SELECT * FROM projects WHERE status!='완료' ORDER BY code").fetchall()
        ch_counts = {}
        for p in rows:
            ch_counts[p["channel"] or "미지정"] = ch_counts.get(p["channel"] or "미지정", 0) + 1
        if ch:
            rows = [p for p in rows if (p["channel"] or "미지정") == ch]
        cards = [project_summary(con, p) for p in rows]
        counts = dict(con.execute("SELECT CASE WHEN status='완료' THEN 'done' ELSE 'active' END k, COUNT(*) FROM projects GROUP BY k").fetchall())
    return render(request, "dashboard.html", cards=cards, view=view, counts=counts, ch=ch, ch_counts=ch_counts)

# ---------- 프로젝트 ----------
@app.get("/projects/new", response_class=HTMLResponse)
def project_new_page(request: Request, intake: int = 0):
    with get_db() as con:
        n = con.execute("SELECT COALESCE(MAX(CAST(code AS INTEGER)),0)+1 FROM projects").fetchone()[0]
        it = con.execute("SELECT * FROM intakes WHERE id=?", (intake,)).fetchone() if intake else None
    d = json.loads(it["result"]) if it and it["result"] else {}
    files = json.loads(it["files"]) if it and it["files"] else []
    return render(request, "project_new.html", next_code=f"{n:03d}", d=d, intake=it, intake_files=files,
                  ai_ok=ai.ai_ready(), model=ai.model_label())

@app.post("/projects/intake")
async def project_intake(request: Request, text: str = Form(""), files: list[UploadFile] = File(default=[])):
    """의뢰 자료(파일·텍스트)를 받아 AI로 새 프로젝트 초안을 만든다. 저장은 멘토가 검토한 뒤."""
    files = [f for f in files if f.filename]
    if not text.strip() and not files:
        request.session["flash"] = "텍스트를 붙여 넣거나 파일을 하나 이상 첨부하세요."
        return back("/projects/new")
    with get_db() as con:
        iid = con.execute("INSERT INTO intakes(text) VALUES(?)", (blank(text),)).lastrowid
    folder = UPLOAD_DIR / "_intake" / str(iid)
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, up in enumerate(files):
        name = _safe_name(up.filename)
        dest = folder / f"{i}_{name}"
        size = 0
        with open(dest, "wb") as out:
            while chunk := await up.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    out.close(); dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"파일이 {MAX_UPLOAD_MB}MB를 넘습니다: {name}")
                out.write(chunk)
        media = extract.classify(name)
        txt, note = (None, None) if media == "audio" else extract.extract_text(dest, media)
        saved.append({"path": str(dest), "filename": name, "media": media, "mime": extract.mime_of(name), "size": size,
                      "extracted_text": txt, "note": note})
    with get_db() as con:
        con.execute("UPDATE intakes SET folder=?, files=? WHERE id=?", (str(folder), json.dumps(saved, ensure_ascii=False), iid))
    return _run_intake(request, iid)

def _run_intake(request, iid):
    with get_db() as con:
        it = con.execute("SELECT * FROM intakes WHERE id=?", (iid,)).fetchone()
    if not it:
        raise HTTPException(404)
    try:
        result = ai.intake_draft(it["text"] or "", json.loads(it["files"] or "[]"))
    except ai.AIError as e:
        request.session["flash"] = f"자동 작성 실패: {e}"
        return back(f"/projects/new?intake={iid}")
    with get_db() as con:
        con.execute("UPDATE intakes SET result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), iid))
    request.session["flash"] = "자료를 바탕으로 초안을 채웠습니다. 내용을 확인·수정한 뒤 [만들기]를 누르세요."
    return back(f"/projects/new?intake={iid}")

@app.post("/projects/intake/{iid}/retry")
def project_intake_retry(request: Request, iid: int):
    return _run_intake(request, iid)

@app.post("/projects/new")
async def project_create(request: Request):
    form = await request.form()
    val = lambda k, d="": (form.get(k) or d).strip()
    on = lambda k: form.get(k) == "1"
    channel = val("channel")
    if channel not in CHANNELS or not val("code"):
        raise HTTPException(400)
    try:
        planned_sessions = max(1, int(val("planned_sessions", "3")))
        planned_hours = float(val("planned_hours", "6"))
    except ValueError:
        raise HTTPException(400)
    plan_confirmed = on("plan_confirmed")
    intake_id = int(val("intake_id", "0") or 0)
    with get_db() as con:
        pid = con.execute("""INSERT INTO projects(code,channel,brand,service,mentee,goal,item,critical_question,org,planned_sessions,planned_hours,
                             start_date,end_date,plan_confirmed,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (val("code"), channel, blank(val("brand")), blank(val("service")), blank(val("mentee")), blank(val("goal")),
                           blank(val("item")), blank(val("critical_question")), blank(val("org")), planned_sessions, planned_hours,
                           blank(val("start_date")), blank(val("end_date")), 1 if plan_confirmed else 0, blank(val("notes")))).lastrowid
        for i in range(1, planned_sessions + 1):
            con.execute("INSERT INTO sessions(project_id,no) VALUES(?,?)", (pid, i))
        if any(val(f"fs_{k}") for k in ("date", "start_time", "end_time", "place")):
            con.execute("UPDATE sessions SET date=?, start_time=?, end_time=?, place=?, method=? WHERE project_id=? AND no=1",
                        (blank(val("fs_date")), blank(val("fs_start_time")), blank(val("fs_end_time")), blank(val("fs_place")),
                         val("fs_method") or "오프라인", pid))
        # 업무 프로세스의 신규 의뢰 체크리스트(경로별)를 첫 체크리스트로 복사
        from process import checklist_for
        for i, text in enumerate(checklist_for(channel)):
            con.execute("INSERT INTO checklist(project_id,text,origin,sort) VALUES(?,?, '프로세스', ?)", (pid, text, i))
        qn = 0
        def add_q(text, owner, pri):
            nonlocal qn
            qn += 1
            con.execute("INSERT INTO questions(project_id,code,text,owner,priority) VALUES(?,?,?,?,?)", (pid, f"Q{qn:02d}", text, owner, pri))
        picked_q = [val(f"q_text_{i}") for i in range(200) if f"q_text_{i}" in form and on(f"q_{i}")]
        has = lambda *words: any(all(w in q for w in words) for q in picked_q)   # AI가 이미 같은 확인 사항을 뽑았으면 중복 등록 안 함
        if not plan_confirmed and not has("회차", "시간"):
            add_q("총 회차 수·회차별 시간 확인 (입력한 계획은 미확인 값)", "멘토 (운영기관)", "높음")
        if channel not in CHANNEL_FORMS and not has("양식"):
            add_q(f"'{channel}' 경로의 보고 양식·증빙·마감 규정 확인", "멘토 (운영기관)", "높음")
        # 자료에서 뽑은 항목 중 멘토가 체크한 것만
        i = 0
        while f"rq_text_{i}" in form:
            if on(f"rq_{i}") and val(f"rq_text_{i}"):
                n = con.execute("SELECT COUNT(*)+1 FROM requests WHERE project_id=?", (pid,)).fetchone()[0]
                con.execute("INSERT INTO requests(project_id,code,text,source) VALUES(?,?,?,?)", (pid, f"R{n}", val(f"rq_text_{i}"), blank(val(f"rq_src_{i}"))))
            i += 1
        i = 0
        while f"ms_label_{i}" in form:
            if on(f"ms_{i}") and val(f"ms_label_{i}") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", val(f"ms_date_{i}")):
                con.execute("INSERT INTO milestones(project_id,date,label) VALUES(?,?,?)", (pid, val(f"ms_date_{i}"), val(f"ms_label_{i}")))
            i += 1
        i = 0
        while f"q_text_{i}" in form:
            if on(f"q_{i}") and val(f"q_text_{i}"):
                add_q(val(f"q_text_{i}"), blank(val(f"q_owner_{i}")), val(f"q_pri_{i}") or "보통")
            i += 1
        # 의뢰 자료는 '회차 밖 기록'으로 보관 (파일을 프로젝트 폴더로 옮김)
        if intake_id:
            it = con.execute("SELECT * FROM intakes WHERE id=? AND project_id IS NULL", (intake_id,)).fetchone()
            if it:
                files = json.loads(it["files"] or "[]")
                summary = json.loads(it["result"]).get("summary") if it["result"] else None
                inp = con.execute("INSERT INTO inputs(project_id,kind,media,text,ai_result,state) VALUES(?,?,?,?,?,?)",
                                  (pid, "의뢰 자료", ",".join(sorted({f["media"] for f in files})) or "text", it["text"],
                                   json.dumps({"summary": summary, "_applied": {"at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                               "items": ["새 프로젝트 등록에 사용"]}}, ensure_ascii=False) if summary else None,
                                   "반영됨")).lastrowid
                dest_dir = UPLOAD_DIR / _safe_name(val("code"))
                dest_dir.mkdir(parents=True, exist_ok=True)
                for f in files:
                    src = upload_path(f["path"]); dest = dest_dir / f"intake{intake_id}_{src.name}"
                    if src.exists():
                        src.replace(dest)
                    con.execute("""INSERT INTO input_files(input_id,filename,stored_path,media,mime,size,extracted_text,note)
                                   VALUES(?,?,?,?,?,?,?,?)""", (inp, f["filename"], str(dest), f["media"], f["mime"], f.get("size"),
                                                                  f.get("extracted_text"), f.get("note")))
                con.execute("UPDATE intakes SET project_id=? WHERE id=?", (pid, intake_id))
    return back(f"/projects/{pid}")

def load_project(con, pid):
    p = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404)
    return p

TABS = [("overview", "개요"), ("sessions", "회차·기록"), ("checklist", "체크리스트"), ("questions", "미결 사항"), ("reports", "보고서")]

@app.get("/projects/{pid}", response_class=HTMLResponse)
def project_detail(request: Request, pid: int, tab: str = "overview", edit: int = 0):
    if tab == "inputs":   # 예전 주소: 입력 탭은 회차·기록 탭으로 합쳐짐
        tab = "sessions"
    with get_db() as con:
        p = load_project(con, pid)
        ctx = dict(p=p, tab=tab, tabs=TABS, edit=edit, summary=project_summary(con, p))
        ctx["requests"] = con.execute("SELECT * FROM requests WHERE project_id=? ORDER BY id", (pid,)).fetchall()
        ctx["checklist"] = con.execute("""SELECT c.*, r.code rcode FROM checklist c LEFT JOIN requests r ON r.id=c.request_id
                                          WHERE c.project_id=? ORDER BY c.request_id IS NULL, c.request_id, c.sort, c.id""", (pid,)).fetchall()
        ctx["sessions"] = con.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY no", (pid,)).fetchall()
        ctx["questions"] = con.execute("SELECT * FROM questions WHERE project_id=? ORDER BY status DESC, CASE priority WHEN '긴급' THEN 0 WHEN '높음' THEN 1 ELSE 2 END, id", (pid,)).fetchall()
        ctx["milestones"] = con.execute("SELECT * FROM milestones WHERE project_id=? ORDER BY date", (pid,)).fetchall()
        ctx["memos"] = con.execute("SELECT * FROM memos WHERE project_id=? ORDER BY sort, id", (pid,)).fetchall()
        ctx["inputs"] = con.execute("""SELECT i.*, s.no session_no FROM inputs i LEFT JOIN sessions s ON s.id=i.session_id
                                       WHERE i.project_id=? ORDER BY i.id DESC""", (pid,)).fetchall()
        files = {}
        for f in con.execute("""SELECT f.* FROM input_files f JOIN inputs i ON i.id=f.input_id WHERE i.project_id=? ORDER BY f.id""", (pid,)):
            files.setdefault(f["input_id"], []).append(f)
        ctx["input_files"] = files
        ctx["input_results"] = {i["id"]: json.loads(i["ai_result"]) for i in ctx["inputs"] if i["ai_result"]}
        ctx["digests"] = {}
        for d in con.execute("""SELECT * FROM session_digests WHERE project_id=? AND id IN
                                (SELECT MAX(id) FROM session_digests WHERE project_id=? GROUP BY COALESCE(session_id,0))""", (pid, pid)):
            ctx["digests"][d["session_id"] or 0] = dict(row=d, r=json.loads(d["result"]), applied=json.loads(d["applied"]) if d["applied"] else None)
        ctx["report_latest"] = {(r["form"], r["session_id"]): r for r in con.execute(
            """SELECT * FROM reports WHERE project_id=? AND id IN (SELECT MAX(id) FROM reports WHERE project_id=? GROUP BY form, COALESCE(session_id,0))""",
            (pid, pid))}
    from config import OPENAI_KEY_SET
    ctx.update(err=request.query_params.get("err"), anthropic_ok=ai.ai_ready(), openai_ok=OPENAI_KEY_SET, model=ai.model_label())
    return render(request, "project.html", **ctx)

@app.post("/projects/{pid}/edit")
def project_update(pid: int, brand: str = Form(""), service: str = Form(""), mentee: str = Form(""), mentee_nickname: str = Form(""),
                   goal: str = Form(""), item: str = Form(""), critical_question: str = Form(""), org: str = Form(""),
                   phase: int = Form(1), status: str = Form("진행 중"), planned_sessions: int = Form(3), planned_hours: float = Form(6),
                   start_date: str = Form(""), end_date: str = Form(""), notes: str = Form(""), plan_confirmed: str = Form(""),
                   channel: str = Form("")):
    with get_db() as con:
        load_project(con, pid)
        con.execute("""UPDATE projects SET brand=?,service=?,mentee=?,mentee_nickname=?,goal=?,item=?,critical_question=?,org=?,
                       phase=?,status=?,planned_sessions=?,planned_hours=?,start_date=?,end_date=?,notes=?,plan_confirmed=?,channel=?,
                       completed_at=CASE WHEN ?='완료' THEN COALESCE(completed_at, datetime('now','localtime')) ELSE NULL END WHERE id=?""",
                    (blank(brand), blank(service), blank(mentee), blank(mentee_nickname), blank(goal), blank(item), blank(critical_question),
                     blank(org), phase, status, planned_sessions, planned_hours, blank(start_date), blank(end_date), blank(notes),
                     1 if plan_confirmed else 0, channel if channel in CHANNELS else None, status, pid))
    return back(f"/projects/{pid}")

@app.post("/projects/{pid}/status")
def project_status(pid: int, status: str = Form(...)):
    if status not in PROJECT_STATUSES:
        raise HTTPException(400)
    with get_db() as con:
        con.execute("""UPDATE projects SET status=?, completed_at=CASE WHEN ?='완료' THEN datetime('now','localtime') ELSE NULL END WHERE id=?""",
                    (status, status, pid))
    return back(f"/projects/{pid}")

# ---------- 요청 / 체크리스트 ----------
@app.post("/projects/{pid}/requests")
def request_add(pid: int, text: str = Form(...), source: str = Form("")):
    with get_db() as con:
        n = con.execute("SELECT COUNT(*)+1 FROM requests WHERE project_id=?", (pid,)).fetchone()[0]
        con.execute("INSERT INTO requests(project_id,code,text,source) VALUES(?,?,?,?)", (pid, f"R{n}", text.strip(), blank(source)))
    return back(f"/projects/{pid}?tab=overview")

@app.post("/projects/{pid}/checklist")
def checklist_add(pid: int, text: str = Form(...), request_id: str = Form(""), due: str = Form("")):
    with get_db() as con:
        con.execute("INSERT INTO checklist(project_id,request_id,text,due) VALUES(?,?,?,?)",
                    (pid, int(request_id) if request_id else None, text.strip(), blank(due)))
    return back(f"/projects/{pid}?tab=checklist")

@app.post("/checklist/{cid}/toggle")
def checklist_toggle(cid: int):
    with get_db() as con:
        r = con.execute("SELECT project_id, done FROM checklist WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404)
        con.execute("UPDATE checklist SET done=?, done_at=CASE WHEN ?=1 THEN datetime('now','localtime') END WHERE id=?",
                    (1 - r["done"], 1 - r["done"], cid))
    return back(f"/projects/{r['project_id']}?tab=checklist")

@app.post("/checklist/{cid}/approve")
def checklist_approve(cid: int, approve: int = Form(1)):
    with get_db() as con:
        r = con.execute("SELECT project_id FROM checklist WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404)
        if approve:
            con.execute("UPDATE checklist SET approved=1 WHERE id=?", (cid,))
        else:
            con.execute("DELETE FROM checklist WHERE id=?", (cid,))
    return back(f"/projects/{r['project_id']}?tab=checklist")

@app.post("/checklist/{cid}/delete")
def checklist_delete(cid: int):
    with get_db() as con:
        r = con.execute("SELECT project_id FROM checklist WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404)
        con.execute("DELETE FROM checklist WHERE id=?", (cid,))
    return back(f"/projects/{r['project_id']}?tab=checklist")

# ---------- 회차 ----------
# 회차 탭은 기본 정보만 다룬다. 내용 기록은 '입력'에 모으고, 보고서를 만들 때 AI가 회차별로 뽑아낸다.
SESSION_FIELDS = ["date", "start_time", "end_time", "place", "method", "topic", "status", "photo1", "photo2"]

@app.post("/projects/{pid}/sessions/add")
def session_add(pid: int):
    with get_db() as con:
        n = con.execute("SELECT COALESCE(MAX(no),0)+1 FROM sessions WHERE project_id=?", (pid,)).fetchone()[0]
        con.execute("INSERT INTO sessions(project_id,no) VALUES(?,?)", (pid, n))
    return back(f"/projects/{pid}?tab=sessions")

@app.post("/sessions/{sid}/edit")
async def session_edit(request: Request, sid: int):
    form = await request.form()
    with get_db() as con:
        r = con.execute("SELECT project_id FROM sessions WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404)
        vals = [blank(form.get(f)) for f in SESSION_FIELDS]
        con.execute(f"UPDATE sessions SET {', '.join(f + '=?' for f in SESSION_FIELDS)} WHERE id=?", (*vals, sid))
    return back(f"/projects/{r['project_id']}?tab=sessions#s{sid}")

# ---------- 미결 사항 ----------
@app.post("/projects/{pid}/questions")
def question_add(pid: int, text: str = Form(...), owner: str = Form(""), due: str = Form(""), priority: str = Form("보통")):
    with get_db() as con:
        n = con.execute("SELECT COALESCE(MAX(CAST(SUBSTR(code,2) AS INTEGER)),0)+1 FROM questions WHERE project_id=?", (pid,)).fetchone()[0]
        con.execute("INSERT INTO questions(project_id,code,text,owner,due,priority) VALUES(?,?,?,?,?,?)",
                    (pid, f"Q{n:02d}", text.strip(), blank(owner), blank(due), priority))
    return back(f"/projects/{pid}?tab=questions")

@app.post("/questions/{qid}/decide")
def question_decide(qid: int, decision: str = Form(...), decided_by: str = Form("멘토"), basis: str = Form("")):
    with get_db() as con:
        r = con.execute("SELECT project_id FROM questions WHERE id=?", (qid,)).fetchone()
        if not r:
            raise HTTPException(404)
        con.execute("UPDATE questions SET status='결정', decision=?, decided_by=?, basis=?, decided_at=date('now','localtime') WHERE id=?",
                    (decision.strip(), blank(decided_by), blank(basis), qid))
    return back(f"/projects/{r['project_id']}?tab=questions")

@app.post("/questions/{qid}/reopen")
def question_reopen(qid: int):
    with get_db() as con:
        r = con.execute("SELECT project_id FROM questions WHERE id=?", (qid,)).fetchone()
        if not r:
            raise HTTPException(404)
        con.execute("UPDATE questions SET status='미결', decision=NULL, decided_by=NULL, basis=NULL, decided_at=NULL WHERE id=?", (qid,))
    return back(f"/projects/{r['project_id']}?tab=questions")

# ---------- 일정 ----------
@app.post("/projects/{pid}/milestones")
def milestone_add(pid: int, date_: str = Form(..., alias="date"), label: str = Form(...)):
    with get_db() as con:
        con.execute("INSERT INTO milestones(project_id,date,label) VALUES(?,?,?)", (pid, date_, label.strip()))
    return back(f"/projects/{pid}?tab=overview")

@app.post("/milestones/{mid}/toggle")
def milestone_toggle(mid: int):
    with get_db() as con:
        r = con.execute("SELECT project_id FROM milestones WHERE id=?", (mid,)).fetchone()
        if not r:
            raise HTTPException(404)
        con.execute("UPDATE milestones SET done=1-done WHERE id=?", (mid,))
    return back(f"/projects/{r['project_id']}?tab=overview")

# ---------- 메모(쟁점·가설) ----------
@app.post("/projects/{pid}/memos")
def memo_add(pid: int, title: str = Form(...), body: str = Form("")):
    with get_db() as con:
        con.execute("INSERT INTO memos(project_id,title,body) VALUES(?,?,?)", (pid, title.strip(), blank(body)))
    return back(f"/projects/{pid}?tab=overview")

@app.post("/memos/{mid}/edit")
def memo_edit(mid: int, title: str = Form(...), body: str = Form("")):
    with get_db() as con:
        r = con.execute("SELECT project_id FROM memos WHERE id=?", (mid,)).fetchone()
        if not r:
            raise HTTPException(404)
        con.execute("UPDATE memos SET title=?, body=?, updated_at=datetime('now','localtime') WHERE id=?", (title.strip(), blank(body), mid))
    return back(f"/projects/{r['project_id']}?tab=overview")

# ---------- 입력 올리기 ----------
import json, re, threading
from fastapi import UploadFile, File
from fastapi.responses import FileResponse
import extract, ai
from config import UPLOAD_DIR, MAX_UPLOAD_MB, upload_path

def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:120] or "file"

def _transcribe_bg(file_id: int):
    with get_db() as con:
        f = con.execute("SELECT * FROM input_files WHERE id=?", (file_id,)).fetchone()
    text, note = extract.transcribe(upload_path(f["stored_path"]))
    with get_db() as con:
        con.execute("UPDATE input_files SET extracted_text=?, note=? WHERE id=?", (text, note, file_id))
        busy = con.execute("""SELECT COUNT(*) FROM input_files WHERE input_id=? AND media='audio' AND note LIKE '받아쓰는 중%'""",
                           (f["input_id"],)).fetchone()[0]
        if not busy:
            con.execute("UPDATE inputs SET state='대기' WHERE id=? AND state='받아쓰는 중'", (f["input_id"],))

def _start_transcribe(con, input_id, file_id):
    con.execute("UPDATE input_files SET note='받아쓰는 중… (긴 녹음은 몇 분 걸립니다)' WHERE id=?", (file_id,))
    con.execute("UPDATE inputs SET state='받아쓰는 중' WHERE id=?", (input_id,))
    threading.Thread(target=_transcribe_bg, args=(file_id,), daemon=True).start()

@app.post("/projects/{pid}/inputs")
async def input_create(pid: int, kind: str = Form("미팅"), session_id: str = Form(""), text: str = Form(""),
                       files: list[UploadFile] = File(default=[])):
    files = [f for f in files if f.filename]
    if not text.strip() and not files:
        return back(f"/projects/{pid}?tab=sessions&err=empty")
    with get_db() as con:
        p = load_project(con, pid)
        iid = con.execute("INSERT INTO inputs(project_id,session_id,kind,media,text) VALUES(?,?,?,?,?)",
                          (pid, int(session_id) if session_id else None, kind,
                           "text" if not files else ",".join(sorted({extract.classify(f.filename) for f in files})),
                           blank(text))).lastrowid
        folder = UPLOAD_DIR / p["code"]
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_ids = []
        for i, up in enumerate(files):
            name = _safe_name(up.filename)
            dest = folder / f"{stamp}_{iid}_{i}_{name}"
            size = 0
            with open(dest, "wb") as out:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_MB * 1024 * 1024:
                        out.close(); dest.unlink(missing_ok=True)
                        raise HTTPException(413, f"파일이 {MAX_UPLOAD_MB}MB를 넘습니다: {name}")
                    out.write(chunk)
            media = extract.classify(name)
            text_out, note = (None, None) if media == "audio" else extract.extract_text(dest, media)
            fid = con.execute("""INSERT INTO input_files(input_id,filename,stored_path,media,mime,size,extracted_text,note)
                                 VALUES(?,?,?,?,?,?,?,?)""", (iid, name, str(dest), media, extract.mime_of(name), size, text_out, note)).lastrowid
            if media == "audio":
                audio_ids.append(fid)
        for fid in audio_ids:
            _start_transcribe(con, iid, fid)
    return back(f"/projects/{pid}?tab=sessions#i{iid}")

@app.post("/input_files/{fid}/retranscribe")
def input_retranscribe(fid: int):
    with get_db() as con:
        f = con.execute("SELECT f.*, i.project_id FROM input_files f JOIN inputs i ON i.id=f.input_id WHERE f.id=?", (fid,)).fetchone()
        if not f or f["media"] != "audio":
            raise HTTPException(404)
        _start_transcribe(con, f["input_id"], fid)
    return back(f"/projects/{f['project_id']}?tab=sessions#i{f['input_id']}")

@app.get("/files/{fid}")
def file_download(fid: int):
    with get_db() as con:
        f = con.execute("SELECT * FROM input_files WHERE id=?", (fid,)).fetchone()
    if not f or not upload_path(f["stored_path"]).exists():
        raise HTTPException(404)
    return FileResponse(upload_path(f["stored_path"]), filename=f["filename"], media_type=f["mime"] or None)

SESSION_META_FIELDS = [("date", "일자"), ("start_time", "시작"), ("end_time", "종료"), ("place", "장소"), ("method", "진행방법"), ("topic", "주제")]

@app.post("/inputs/{iid}/delete")
def input_delete(request: Request, iid: int):
    """기록 삭제. 첨부 파일은 바로 지우지 않고 data\\uploads\\_deleted로 옮겨 둔다 (실수 대비)."""
    with get_db() as con:
        inp = con.execute("SELECT * FROM inputs WHERE id=?", (iid,)).fetchone()
        if not inp:
            raise HTTPException(404)
        files = con.execute("SELECT * FROM input_files WHERE input_id=?", (iid,)).fetchall()
        trash = UPLOAD_DIR / "_deleted" / f"{datetime.now():%Y%m%d_%H%M%S}_기록{iid}"
        for f in files:
            src = upload_path(f["stored_path"])
            if src.exists():
                trash.mkdir(parents=True, exist_ok=True)
                src.replace(trash / src.name)
        con.execute("DELETE FROM inputs WHERE id=?", (iid,))
    request.session["flash"] = "기록을 삭제했습니다." + (" 첨부 파일은 data\\uploads\\_deleted에 옮겨 두었습니다." if files else "") + \
                               " 이 회차를 다시 정리하면 보고서 초안에도 반영됩니다."
    anchor = f"s{inp['session_id']}" if inp["session_id"] else "loose"
    return back(f"/projects/{inp['project_id']}?tab=sessions#{anchor}")

@app.post("/projects/{pid}/digest/{sid}")
def group_digest(request: Request, pid: int, sid: int, feedback: str = Form("")):
    """회차(sid) 또는 회차 밖 기록(sid=0)의 모든 기록을 한 번에 AI로 정리. 결과: 승인 대기 제안 + 보고서 초안."""
    session_id = sid or None
    anchor = f"s{sid}" if sid else "loose"
    with get_db() as con:
        p = load_project(con, pid)
        q = "SELECT * FROM inputs WHERE project_id=? AND " + ("session_id=?" if session_id else "session_id IS NULL")
        inputs = con.execute(q, (pid, session_id) if session_id else (pid,)).fetchall()
    if not inputs:
        request.session["flash"] = "정리할 기록이 없습니다. 먼저 기록을 올려 주세요."
        return back(f"/projects/{pid}?tab=sessions#{anchor}")
    if any(i["state"] == "받아쓰는 중" for i in inputs):
        return back(f"/projects/{pid}?tab=sessions&err=busy#{anchor}")
    forms = CHANNEL_FORMS.get(p["channel"], [])
    rules = ("form3" if "form3" in forms else "session") if session_id else "loose"
    try:
        r = ai.digest_session(pid, session_id, blank(feedback), rules)
    except ai.AIError as e:
        request.session["flash"] = str(e)
        return back(f"/projects/{pid}?tab=sessions#{anchor}")
    with get_db() as con:
        again = con.execute("SELECT COUNT(*) FROM session_digests WHERE project_id=? AND COALESCE(session_id,0)=?", (pid, sid)).fetchone()[0]
        did = con.execute("INSERT INTO session_digests(project_id,session_id,input_ids,result,feedback) VALUES(?,?,?,?,?)",
                          (pid, session_id, json.dumps([i["id"] for i in inputs]), json.dumps(r, ensure_ascii=False), blank(feedback))).lastrowid
        con.execute(f"UPDATE inputs SET state='정리됨' WHERE id IN ({','.join('?' * len(inputs))}) AND state!='반영됨'", [i["id"] for i in inputs])
        if rules == "form3":   # PLAB: 정리 결과를 양식3 초안 새 버전으로
            rep = r["report"]
            rid = _save_report(con, pid, 3, session_id, rep["content"], {"topic": rep["topic"], "missing": rep["missing"], "digest_id": did},
                               feedback, "AI 재정리" if again else "AI 정리")
            con.execute("UPDATE session_digests SET report_id=? WHERE id=?", (rid, did))
    return back(f"/digests/{did}/review")

@app.get("/digests/{did}/review", response_class=HTMLResponse)
def digest_review(request: Request, did: int):
    with get_db() as con:
        dg = con.execute("SELECT * FROM session_digests WHERE id=?", (did,)).fetchone()
        if not dg:
            raise HTTPException(404)
        p = load_project(con, dg["project_id"])
        r = json.loads(dg["result"])
        sessions = con.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY no", (p["id"],)).fetchall()
        s = next((x for x in sessions if x["id"] == dg["session_id"]), None)
        ck = {c["id"]: c for c in con.execute("SELECT * FROM checklist WHERE project_id=?", (p["id"],))}
        qs = {q["code"]: q for q in con.execute("SELECT * FROM questions WHERE project_id=? AND status='미결'", (p["id"],))}
        requests_ = con.execute("SELECT * FROM requests WHERE project_id=?", (p["id"],)).fetchall()
        ids = json.loads(dg["input_ids"] or "[]")
        recs = con.execute(f"SELECT * FROM inputs WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids).fetchall() if ids else []
        report = con.execute("SELECT * FROM reports WHERE id=?", (dg["report_id"],)).fetchone() if dg["report_id"] else None
    by_no = {x["no"]: x for x in sessions}
    meta = []
    for m in r.get("session_meta", []):
        cur = by_no.get(m["no"])
        changes = [(k, label, m[k], (cur[k] if cur else None)) for k, label in SESSION_META_FIELDS
                   if m.get(k) and (not cur or (cur[k] or "") != m[k])]
        held = m.get("held") and (not cur or cur["status"] != "완료")
        if changes or held:
            meta.append(dict(no=m["no"], exists=bool(cur), changes=changes, held=held))
    return render(request, "review.html", p=p, dg=dg, r=r, s=s, recs=recs, meta=meta, report=report,
                  done_items=[(d, ck.get(d["id"])) for d in r["checklist_done"] if ck.get(d["id"]) and not ck[d["id"]]["done"]],
                  resolved=[(q, qs.get(q["code"])) for q in r["questions_resolved"] if qs.get(q["code"])],
                  requests=requests_)

@app.post("/digests/{did}/apply")
async def digest_apply(request: Request, did: int):
    form = await request.form()
    on = lambda k: form.get(k) == "1"
    val = lambda k: (form.get(k) or "").strip()
    applied = []
    with get_db() as con:
        dg = con.execute("SELECT * FROM session_digests WHERE id=?", (did,)).fetchone()
        if not dg:
            raise HTTPException(404)
        pid = dg["project_id"]
        # 회차 기본 정보
        nos = sorted({int(k.split("_")[1]) for k in form.keys() if re.fullmatch(r"m_\d+_\w+", k) and on(k)})
        for no in nos:
            s = con.execute("SELECT * FROM sessions WHERE project_id=? AND no=?", (pid, no)).fetchone()
            if not s:
                sid = con.execute("INSERT INTO sessions(project_id,no) VALUES(?,?)", (pid, no)).lastrowid
                s = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
                applied.append(f"{no}회차 추가")
            for k, label in SESSION_META_FIELDS:
                if on(f"m_{no}_{k}") and val(f"mv_{no}_{k}"):
                    con.execute(f"UPDATE sessions SET {k}=? WHERE id=?", (val(f"mv_{no}_{k}"), s["id"])); applied.append(f"{no}회차 {label}")
            if on(f"m_{no}_held"):
                con.execute("UPDATE sessions SET status='완료' WHERE id=?", (s["id"],)); applied.append(f"{no}회차 완료 표시")
        for k in form.keys():
            if k.startswith("done_") and on(k):
                cid = int(k[5:])
                con.execute("UPDATE checklist SET done=1, done_at=datetime('now','localtime') WHERE id=? AND project_id=?", (cid, pid))
                applied.append(f"체크리스트 완료 #{cid}")
        reqs = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM requests WHERE project_id=?", (pid,))}
        i = 0
        while f"nck_text_{i}" in form:
            if on(f"nck_{i}") and val(f"nck_text_{i}"):
                con.execute("INSERT INTO checklist(project_id,request_id,text,due,origin) VALUES(?,?,?,?, 'AI 정리(승인)')",
                            (pid, reqs.get(val(f"nck_req_{i}")), val(f"nck_text_{i}"), blank(val(f"nck_due_{i}"))))
                applied.append("체크리스트 추가")
            i += 1
        i = 0
        while f"nq_text_{i}" in form:
            if on(f"nq_{i}") and val(f"nq_text_{i}"):
                n = con.execute("SELECT COALESCE(MAX(CAST(SUBSTR(code,2) AS INTEGER)),0)+1 FROM questions WHERE project_id=? AND code LIKE 'Q%'", (pid,)).fetchone()[0]
                con.execute("INSERT INTO questions(project_id,code,text,owner,due,priority,origin) VALUES(?,?,?,?,?,?, 'AI 정리(승인)')",
                            (pid, f"Q{n:02d}", val(f"nq_text_{i}"), blank(val(f"nq_owner_{i}")), blank(val(f"nq_due_{i}")), val(f"nq_pri_{i}") or "보통"))
                applied.append("미결 추가")
            i += 1
        for k in form.keys():
            if k.startswith("rq_") and on(k):
                code = k[3:]
                con.execute("""UPDATE questions SET status='결정', decision=?, basis=?, decided_by='멘토(정리 반영)', decided_at=date('now','localtime')
                               WHERE project_id=? AND code=? AND status='미결'""", (val(f"rq_dec_{code}"), blank(val(f"rq_basis_{code}")), pid, code))
                applied.append(f"{code} 결정")
        i = 0
        while f"nr_text_{i}" in form:
            if on(f"nr_{i}") and val(f"nr_text_{i}"):
                n = con.execute("SELECT COUNT(*)+1 FROM requests WHERE project_id=?", (pid,)).fetchone()[0]
                con.execute("INSERT INTO requests(project_id,code,text,source) VALUES(?,?,?,?)", (pid, f"R{n}", val(f"nr_text_{i}"), blank(val(f"nr_src_{i}"))))
                applied.append("요청 추가")
            i += 1
        i = 0
        while f"nm_label_{i}" in form:
            if on(f"nm_{i}") and val(f"nm_label_{i}") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", val(f"nm_date_{i}")):
                con.execute("INSERT INTO milestones(project_id,date,label) VALUES(?,?,?)", (pid, val(f"nm_date_{i}"), val(f"nm_label_{i}")))
                applied.append("일정 추가")
            i += 1
        if on("research") and val("research_text"):
            m = con.execute("SELECT * FROM memos WHERE project_id=? AND title='자료조사 메모'", (pid,)).fetchone()
            entry = f"[{date.today().isoformat()}]\n{val('research_text')}"
            if m:
                con.execute("UPDATE memos SET body=?, updated_at=datetime('now','localtime') WHERE id=?", (entry + "\n\n" + (m["body"] or ""), m["id"]))
            else:
                con.execute("INSERT INTO memos(project_id,title,body,sort) VALUES(?, '자료조사 메모', ?, 50)", (pid, entry))
            applied.append("자료조사 메모")
        con.execute("UPDATE session_digests SET state='반영됨', applied=? WHERE id=?",
                    (json.dumps({"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "items": applied}, ensure_ascii=False), did))
        ids = json.loads(dg["input_ids"] or "[]")
        if ids:
            con.execute(f"UPDATE inputs SET state='반영됨' WHERE id IN ({','.join('?' * len(ids))})", ids)
    request.session["flash"] = f"{len(applied)}개 항목을 반영했습니다." if applied else "반영할 항목을 선택하지 않아 '반영됨'으로만 표시했습니다."
    return back(f"/projects/{pid}?tab=sessions#{'s%d' % dg['session_id'] if dg['session_id'] else 'loose'}")

# ---------- 보고서 (양식3·4) ----------
import reports as rp
from fastapi.responses import Response
from urllib.parse import quote

def _latest(con, pid, form, session_id=None):
    q = "SELECT * FROM reports WHERE project_id=? AND form=?" + (" AND session_id=?" if session_id else "") + " ORDER BY version DESC, id DESC"
    return con.execute(q, (pid, form, session_id) if session_id else (pid, form)).fetchall()

def _save_report(con, pid, form, session_id, body, meta, feedback, origin):
    v = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM reports WHERE project_id=? AND form=? AND COALESCE(session_id,0)=COALESCE(?,0)",
                    (pid, form, session_id)).fetchone()[0]
    n = rp.char_counts(body if isinstance(body, str) else "".join(body.values()))[0]
    return con.execute("""INSERT INTO reports(project_id,form,session_id,body,char_count,version,feedback,origin,meta) VALUES(?,?,?,?,?,?,?,?,?)""",
                       (pid, form, session_id, body if isinstance(body, str) else json.dumps(body, ensure_ascii=False), n, v,
                        blank(feedback), origin, json.dumps(meta, ensure_ascii=False))).lastrowid

def _dl(data: bytes, filename: str):
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

@app.get("/projects/{pid}/form3/{no}", response_class=HTMLResponse)
def form3_page(request: Request, pid: int, no: int, v: int = 0):
    with get_db() as con:
        p = load_project(con, pid)
        s = con.execute("SELECT * FROM sessions WHERE project_id=? AND no=?", (pid, no)).fetchone()
        if not s:
            raise HTTPException(404)
        versions = _latest(con, pid, 3, s["id"])
        cur = next((r for r in versions if r["id"] == v), versions[0] if versions else None)
        meta = json.loads(cur["meta"]) if cur and cur["meta"] else {}
        header = rp.form3_header(con, p, s)
        n_inputs = con.execute("SELECT COUNT(*) FROM inputs WHERE project_id=?", (pid,)).fetchone()[0]
    chan_warn = [] if "form3" in CHANNEL_FORMS.get(p["channel"], []) else [f"이 프로젝트의 의뢰 경로({p['channel'] or '미지정'})에 양식3이 맞는지 확인되지 않았습니다."]
    return render(request, "form3.html", p=p, s=s, cur=cur, meta=meta, versions=versions, header=header, n_inputs=n_inputs,
                  # 초안 전에는 글자 수 점검 생략
                  warns=chan_warn + (rp.form3_checks(s, cur["body"]) if cur else rp.form3_checks(s, "")[1:]),
                  counts=rp.char_counts(cur["body"] if cur else ""),
                  MIN=rp.FORM3_MIN_CHARS, anthropic_ok=ai.ai_ready())

@app.post("/projects/{pid}/form3/{no}/generate")
def form3_generate(request: Request, pid: int, no: int, feedback: str = Form(""), base_id: str = Form("")):
    with get_db() as con:
        s = con.execute("SELECT * FROM sessions WHERE project_id=? AND no=?", (pid, no)).fetchone()
        prev = con.execute("SELECT body FROM reports WHERE id=?", (int(base_id),)).fetchone() if base_id else None
    try:
        r = rp.generate_form3(pid, no, blank(feedback), prev["body"] if prev and feedback.strip() else None)
    except ai.AIError as e:
        request.session["flash"] = str(e)
        return back(f"/projects/{pid}/form3/{no}")
    with get_db() as con:
        rid = _save_report(con, pid, 3, s["id"], r["content"], {"topic": r["topic"], "missing": r["missing"]}, feedback,
                           "AI 재생성(피드백)" if feedback.strip() else "AI 생성")
    return back(f"/projects/{pid}/form3/{no}?v={rid}")

@app.post("/reports/{rid}/save")
async def report_save(request: Request, rid: int):
    form = await request.form()
    with get_db() as con:
        old = con.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not old:
            raise HTTPException(404)
        meta = json.loads(old["meta"] or "{}")
        if old["form"] == 3:
            meta["topic"] = (form.get("topic") or "").strip()
            body = (form.get("body") or "").replace("\r\n", "\n").strip()
        else:
            body = {k: (form.get(k) or "").replace("\r\n", "\n").strip() for k in ("diligence", "growth", "excellence")}
        new = _save_report(con, old["project_id"], old["form"], old["session_id"], body, meta, None, "직접 수정")
        no = con.execute("SELECT no FROM sessions WHERE id=?", (old["session_id"],)).fetchone() if old["session_id"] else None
    request.session["flash"] = "수정본을 새 버전으로 저장했습니다."
    return back(f"/projects/{old['project_id']}/form3/{no['no']}?v={new}" if old["form"] == 3 else f"/projects/{old['project_id']}/form4?v={new}")

@app.get("/reports/{rid}/docx")
def report_docx(rid: int):
    with get_db() as con:
        r = con.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404)
        p = load_project(con, r["project_id"])
        meta = json.loads(r["meta"] or "{}")
        if r["form"] == 3:
            s = con.execute("SELECT * FROM sessions WHERE id=?", (r["session_id"],)).fetchone()
            def photo(name):
                if not name:
                    return (None, None)
                f = con.execute("""SELECT f.stored_path FROM input_files f JOIN inputs i ON i.id=f.input_id
                                   WHERE i.project_id=? AND f.filename=? ORDER BY f.id DESC""", (p["id"], name)).fetchone()
                return (name, upload_path(f["stored_path"]) if f else None)
            data = rp.form3_docx(p, rp.form3_header(con, p, s), meta.get("topic", ""), r["body"], [photo(s["photo1"]), photo(s["photo2"])])
            return _dl(data, f"양식3_{p['code']}_{s['no']}회차_v{r['version']}.docx")
        data = rp.form4_docx(p, rp.form4_header(con, p), json.loads(r["body"]))
        return _dl(data, f"양식4_관찰의견서_{p['code']}_v{r['version']}.docx")

@app.get("/projects/{pid}/form4", response_class=HTMLResponse)
def form4_page(request: Request, pid: int, v: int = 0):
    with get_db() as con:
        p = load_project(con, pid)
        versions = _latest(con, pid, 4)
        cur = next((r for r in versions if r["id"] == v), versions[0] if versions else None)
        body = json.loads(cur["body"]) if cur else {}
        meta = json.loads(cur["meta"]) if cur and cur["meta"] else {}
        header = rp.form4_header(con, p)
        warns = ([] if "form4" in CHANNEL_FORMS.get(p["channel"], []) else [f"이 프로젝트의 의뢰 경로({p['channel'] or '미지정'})에 양식4가 맞는지 확인되지 않았습니다."]) \
            + (rp.form4_checks(con, p, body) if cur else [])
    total = sum(rp.char_counts(body.get(k, ""))[0] for k in ("diligence", "growth", "excellence"))
    return render(request, "form4.html", p=p, cur=cur, body=body, meta=meta, versions=versions, header=header, warns=warns,
                  total=total, MAX=rp.FORM4_MAX_CHARS, anthropic_ok=ai.ai_ready())

@app.post("/projects/{pid}/form4/generate")
def form4_generate(request: Request, pid: int, feedback: str = Form(""), base_id: str = Form("")):
    with get_db() as con:
        prev = con.execute("SELECT body FROM reports WHERE id=?", (int(base_id),)).fetchone() if base_id else None
    try:
        r = rp.generate_form4(pid, blank(feedback), json.loads(prev["body"]) if prev and feedback.strip() else None)
    except ai.AIError as e:
        request.session["flash"] = str(e)
        return back(f"/projects/{pid}/form4")
    with get_db() as con:
        rid = _save_report(con, pid, 4, None, {k: r[k] for k in ("diligence", "growth", "excellence")}, {"missing": r["missing"]},
                           feedback, "AI 재생성(피드백)" if feedback.strip() else "AI 생성")
    return back(f"/projects/{pid}/form4?v={rid}")

# ---------- 업무 프로세스 ----------
import process as proc

@app.get("/process", response_class=HTMLResponse)
def process_page(request: Request):
    html, toc = proc.process_html()
    updated = datetime.fromtimestamp(proc.DOC_PATH.stat().st_mtime).strftime("%Y-%m-%d")
    return render(request, "process.html", html=html, toc=toc, updated=updated)

@app.get("/process/download.md")
def process_md():
    return Response(proc.process_markdown().encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('멘토링_업무프로세스.md')}"})

@app.get("/process/download.docx")
def process_docx():
    return _dl(proc.process_docx(), "멘토링_업무프로세스.docx")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT)
