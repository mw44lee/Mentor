"""양식3(회차별 멘토링 보고서)·양식4(책임멘토 관찰 의견서) 초안 생성과 docx 내보내기.
회차 내용은 '입력'에 모인 원문(메모·추출 텍스트·전사본)에서 AI가 회차별로 뽑아 쓴다."""
import io, json
from datetime import datetime
from ai import call_json
from config import MENTOR_NAME
from db import get_db

FORM3_MIN_CHARS = 1000
FORM4_MAX_CHARS = 1800  # 1페이지 이내 기준(세 항목 합계, 공백 포함 대략치)

def _hours(s):
    try:
        t1 = datetime.strptime(s["start_time"], "%H:%M"); t2 = datetime.strptime(s["end_time"], "%H:%M")
        return round((t2 - t1).seconds / 3600, 2)
    except (TypeError, ValueError):
        return None

def char_counts(text: str) -> tuple[int, int]:
    text = text or ""
    return len(text), len("".join(text.split()))

def sources_text(con, pid) -> str:
    """프로젝트의 모든 입력을 시간순으로. 각 입력의 원문과 첨부에서 뽑은 텍스트를 모두 포함."""
    out = []
    rows = con.execute("""SELECT i.*, s.no sno FROM inputs i LEFT JOIN sessions s ON s.id=i.session_id
                          WHERE i.project_id=? ORDER BY i.created_at, i.id""", (pid,)).fetchall()
    for i in rows:
        r = json.loads(i["ai_result"]) if i["ai_result"] else {}
        head = f"=== 입력 #{i['id']} · {i['created_at']} · 종류 {i['kind']}"
        if i["sno"]:
            head += f" · 멘토가 지정한 회차 {i['sno']}"
        if r.get("sessions_mentioned"):
            head += f" · AI가 본 관련 회차 {r['sessions_mentioned']}"
        part = [head + " ==="]
        if i["text"]:
            part.append(f"[메모]\n{i['text']}")
        for f in con.execute("SELECT * FROM input_files WHERE input_id=? ORDER BY id", (i["id"],)):
            if f["extracted_text"]:
                part.append(f"[{'녹음 전사본' if f['media'] == 'audio' else '첨부 문서'}: {f['filename']}]\n{f['extracted_text']}")
            elif f["media"] == "image":
                part.append(f"[사진: {f['filename']}]" + (f" (AI 요약: {r['summary']})" if r.get("summary") else ""))
            else:
                part.append(f"[첨부: {f['filename']}] (텍스트 없음{': ' + f['note'] if f['note'] else ''})")
        if r.get("summary") and not i["text"]:
            part.append(f"[AI 요약] {r['summary']}")
        out.append("\n".join(part))
    return "\n\n".join(out) if out else "(입력 없음)"

def sessions_text(con, pid) -> str:
    lines = []
    for s in con.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY no", (pid,)):
        lines.append(f"{s['no']}회차: 상태 {s['status']}, 일자 {s['date'] or '미정'}, {s['start_time'] or '?'}~{s['end_time'] or '?'}, "
                     f"장소 {s['place'] or '미정'}, 방법 {s['method'] or '미정'}, 주제(예정) {s['topic'] or '-'}")
    return "\n".join(lines)

def project_text(p) -> str:
    return (f"프로젝트 {p['code']} {p['brand'] or ''} {p['service'] or ''} / 멘티(진출자) {p['mentee'] or '-'}\n"
            f"하려는 일: {p['goal'] or '-'}\n핵심 아이템: {p['item'] or '-'}\n핵심 질문: {p['critical_question'] or '-'}")

COMMON_RULES = """- 입력 원문에 있는 사실만 씁니다. 없는 사실·수치·평가를 지어내지 않습니다.
- 입력이 부족하거나 어느 회차 일인지 애매한 내용은 쓰지 말고 'missing'에 "무엇이 부족한지/확인할 것"으로 적습니다.
- 멘토 수당·정산, 멘티의 개인 연락처 등 보고서와 무관한 개인정보는 쓰지 않습니다.
- 공식 보고서 문체(~함, ~하였음, ~임)로, 한국어로 씁니다. #, * 같은 마크다운 기호는 쓰지 않습니다.
- 멘토의 피드백이 주어지면 그 피드백을 최우선으로 반영해 고쳐 씁니다."""

# ---------------- 양식3 ----------------
FORM3_SCHEMA = {"type": "object", "additionalProperties": False,
                "properties": {"topic": {"type": "string"}, "content": {"type": "string"},
                               "missing": {"type": "array", "items": {"type": "string"}}},
                "required": ["topic", "content", "missing"]}

FORM3_SYSTEM = f"""당신은 창업 멘토 {MENTOR_NAME}의 보고서 작성 보조입니다. '모두의 창업 프로젝트' 책임멘토의 「회차별 멘토링 보고서」(양식3) 중
'멘토링 주제'와 '멘토링 내용'을 씁니다.

작성 규칙:
- 지정된 회차 하나의 내용만 씁니다. 입력에는 여러 회차의 기록이 섞여 있을 수 있으니 날짜, 회차 언급, 멘토의 회차 지정을 근거로
  그 회차에 해당하는 내용만 골라냅니다. 다른 회차의 내용은 '이전 회차 과제 점검' 같은 맥락으로만 짧게 언급합니다.
- 멘토링 내용은 공백 포함 {FORM3_MIN_CHARS}자 이상(목표 1,100~1,500자). 형식: '■ 소제목' 줄로 카테고리를 나누고, 각 카테고리 아래는 '- '로 시작하는 간결한 글머리표로 씁니다. 카테고리(이 순서): ■ 진행 개요(일시·장소·목적) / ■ 팀 구성 및 현황 / ■ 아이템 개요 및 진행 사항 / ■ 주요 논의 사항 / ■ 차기 논의 사항 — 해당 사실이 없는 카테고리는 뺍니다. 멘티에 대한 평가 문장(취지가 좋다, 태도가 훌륭하다, 몰입도가 뛰어나다 등)은 이 보고서에 쓰지 않습니다(관찰의견서용 관찰 사실로만 남김). 카테고리 사이는 빈 줄 한 줄. 글머리표는 '~함/~임' 체의 짧은 문장.
  (팀 구성 및 현황에는 지난 과제 결과 포함, 차기 논의 사항에는 다음 회차 계획 포함. 글머리표 끝맺음은 '~함/~임'으로 통일)
- 길이를 맞추려고 같은 말을 반복하거나 일반론을 늘어놓지 않습니다. 사실이 부족해 {FORM3_MIN_CHARS}자를 채울 수 없으면
  있는 사실만 쓰고 'missing'에 무엇을 더 기록하면 되는지 적습니다.
- topic: 그 회차의 멘토링 주제를 한 줄(40자 이내)로.
{COMMON_RULES}"""

def form3_header(con, p, s):
    h = _hours(s) if s else None
    return [("진출자 이름", p["mentee"]), ("멘토링 회차", f"{s['no']}차" if s else None), ("멘토명", MENTOR_NAME),
            ("소속운영기관", p["org"]), ("진행방법", s["method"] if s else None),
            ("멘토링 일자", s["date"].replace("-", ". ") + "." if s and s["date"] else None),
            ("시작시간", s["start_time"].replace(":", "시 ") + "분" if s and s["start_time"] else None),
            ("종료시간", s["end_time"].replace(":", "시 ") + "분" if s and s["end_time"] else None),
            ("총시간", f"{h:g}시간" if h else None)]

def form3_checks(s, body):
    warn = []
    n, _ = char_counts(body)
    if n < FORM3_MIN_CHARS:
        warn.append(f"멘토링 내용이 {n}자입니다. {FORM3_MIN_CHARS}자 이상이어야 합니다.")
    if not s or s["method"] != "오프라인":
        warn.append("진행방법이 오프라인이 아닙니다 (오프라인 운영이 원칙).")
    if not s or not (s["photo1"] and s["photo2"]):
        warn.append("회차 탭에 시작·종료 사진 2장이 지정되지 않았습니다 (사진 2장 이상 필수).")
    if s and s["status"] != "완료":
        warn.append("회차 상태가 아직 '완료'가 아닙니다.")
    return warn

def generate_form3(pid: int, no: int, feedback: str | None = None, previous: str | None = None) -> dict:
    with get_db() as con:
        p = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        s = con.execute("SELECT * FROM sessions WHERE project_id=? AND no=?", (pid, no)).fetchone()
        prev_reports = con.execute("""SELECT r.body, s.no FROM reports r JOIN sessions s ON s.id=r.session_id
                                      WHERE r.project_id=? AND r.form=3 AND s.no<? AND r.id IN
                                      (SELECT MAX(id) FROM reports WHERE project_id=? AND form=3 GROUP BY session_id)""", (pid, no, pid)).fetchall()
        text = "\n\n".join([
            "[프로젝트]\n" + project_text(p),
            "[회차 기본 정보]\n" + sessions_text(con, pid),
            f"[작성할 회차] {no}회차 (일자 {s['date'] if s else '미정'})",
            "[이전 회차 보고서 (중복 서술 피하기용)]\n" + ("\n\n".join(f"{r['no']}회차:\n{r['body']}" for r in prev_reports) or "(없음)"),
            "[입력 원문 — 여러 회차가 섞여 있을 수 있음]\n" + sources_text(con, pid),
        ])
    if previous:
        text += f"\n\n[직전 초안]\n{previous}"
    if feedback:
        text += f"\n\n[멘토 피드백 — 이것을 반영해 다시 쓰세요]\n{feedback}"
    return call_json(FORM3_SYSTEM, [{"type": "text", "text": text}], FORM3_SCHEMA, f"양식3 {no}회차", pid)

# ---------------- 양식4 ----------------
FORM4_SCHEMA = {"type": "object", "additionalProperties": False,
                "properties": {"diligence": {"type": "string"}, "growth": {"type": "string"}, "excellence": {"type": "string"},
                               "missing": {"type": "array", "items": {"type": "string"}}},
                "required": ["diligence", "growth", "excellence", "missing"]}

FORM4_SYSTEM = f"""당신은 창업 멘토 {MENTOR_NAME}의 보고서 작성 보조입니다. '모두의 창업 프로젝트' 「책임멘토 관찰 의견서」(양식4)의
세 항목을 씁니다. 관찰의견은 평가위원회의 참고자료로 쓰이며 전체 1페이지 이내여야 합니다(세 항목 합계 공백 포함 {FORM4_MAX_CHARS}자 이내, 항목당 350~550자).

- diligence(성실성): 멘토링 참여 적극성, 대표자의 창업 의지, 창업준비 상황
- growth(성장성): 멘토링 피드백 수용 정도, 향후 사업계획의 구체성, 로컬창업자로의 발전 가능성
- excellence(우수성): 아이디어 고도화 과정에서 발견한 아이디어의 우수성, 타 아이디어 대비 우수성

작성 규칙:
- 모든 회차의 입력 원문에서 근거가 되는 관찰 사실(언제, 무엇을 했는지)을 회차별로 뽑아 각 항목의 근거로 씁니다.
  형용사로 칭찬하기보다 관찰된 행동과 변화를 씁니다.
- 근거가 없는 항목은 억지로 채우지 말고 짧게 쓰고 'missing'에 무엇이 더 필요한지 적습니다.
- '타 아이디어 대비 우수성'처럼 입력에 비교 근거가 없으면 단정하지 말고 missing에 적습니다.
{COMMON_RULES}"""

def form4_header(con, p):
    rows = con.execute("SELECT * FROM sessions WHERE project_id=? AND status='완료'", (pid := p["id"],)).fetchall()
    def agg(method):
        ss = [s for s in rows if s["method"] == method]
        hs = [_hours(s) for s in ss]
        known = [h for h in hs if h]
        unknown = len(hs) - len(known)
        return len(ss), sum(known), unknown
    on_n, on_h, on_u = agg("온라인"); off_n, off_h, off_u = agg("오프라인")
    result = f"1:1 / 온라인 {on_n}회({on_h:g}시간) 오프라인 {off_n}회({off_h:g}시간)"
    if on_u or off_u:
        result += f" — 시각 미기록 {on_u + off_u}회 제외"
    return [("운영기관", p["org"]), ("책임멘토명", f"{MENTOR_NAME} (인/서명)"),
            ("진출자 닉네임", p["mentee_nickname"]), ("진출자명", p["mentee"]), ("멘토링 결과", result)]

def form4_checks(con, p, body: dict):
    warn = []
    total = sum(char_counts(body.get(k, ""))[0] for k in ("diligence", "growth", "excellence"))
    if total > FORM4_MAX_CHARS:
        warn.append(f"세 항목 합계 {total}자 — 1페이지({FORM4_MAX_CHARS}자 안팎)를 넘을 수 있습니다.")
    if not p["mentee_nickname"]:
        warn.append("진출자 닉네임이 비어 있습니다 (프로젝트 개요에서 입력).")
    planned = con.execute("SELECT COUNT(*) FROM sessions WHERE project_id=?", (p["id"],)).fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM sessions WHERE project_id=? AND status='완료'", (p["id"],)).fetchone()[0]
    if done < planned:
        warn.append(f"완료된 회차가 {done}/{planned}회입니다. 모든 회차가 끝난 뒤 다시 생성하는 것이 좋습니다.")
    return warn

def generate_form4(pid: int, feedback: str | None = None, previous: dict | None = None) -> dict:
    with get_db() as con:
        p = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        f3 = con.execute("""SELECT r.body, s.no FROM reports r JOIN sessions s ON s.id=r.session_id WHERE r.project_id=? AND r.form=3
                            AND r.id IN (SELECT MAX(id) FROM reports WHERE project_id=? AND form=3 GROUP BY session_id) ORDER BY s.no""", (pid, pid)).fetchall()
        dgs = con.execute("""SELECT d.result, s.no FROM session_digests d LEFT JOIN sessions s ON s.id=d.session_id WHERE d.project_id=?
                             AND d.id IN (SELECT MAX(id) FROM session_digests WHERE project_id=? GROUP BY COALESCE(session_id,0)) ORDER BY s.no""",
                          (pid, pid)).fetchall()
        obs = []
        for d in dgs:
            o = json.loads(d["result"]).get("observations") or {}
            if any(o.values()):
                obs.append(f"{str(d['no']) + '회차' if d['no'] else '회차 밖 기록'}: 성실성 {o.get('diligence') or '-'} / 성장성 {o.get('growth') or '-'} / 우수성 {o.get('excellence') or '-'}")
        text = "\n\n".join([
            "[프로젝트]\n" + project_text(p),
            "[회차 기본 정보]\n" + sessions_text(con, pid),
            "[회차별 AI 정리에서 뽑은 관찰 사실]\n" + ("\n".join(obs) or "(없음)"),
            "[작성된 회차별 보고서(양식3)]\n" + ("\n\n".join(f"{r['no']}회차:\n{r['body']}" for r in f3) or "(없음)"),
            "[입력 원문 — 모든 회차]\n" + sources_text(con, pid),
        ])
    if previous:
        text += "\n\n[직전 초안]\n" + "\n".join(f"{k}: {previous.get(k, '')}" for k in ("diligence", "growth", "excellence"))
    if feedback:
        text += f"\n\n[멘토 피드백 — 이것을 반영해 다시 쓰세요]\n{feedback}"
    return call_json(FORM4_SYSTEM, [{"type": "text", "text": text}], FORM4_SCHEMA, "양식4", pid)

# ---------------- docx ----------------
def _doc():
    import docx
    from docx.shared import Pt
    d = docx.Document()
    st = d.styles["Normal"]; st.font.name = "맑은 고딕"; st.font.size = Pt(10.5)
    from docx.oxml.ns import qn
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    return d

def _body(d, text):
    from docx.shared import Pt
    """'■ 소제목' → 굵은 소제목, '- 항목' → 글머리표, 그 밖의 줄 → 문단."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("■"):
            par = d.add_paragraph(); par.add_run(line).bold = True; par.paragraph_format.space_before = Pt(6); par.paragraph_format.space_after = Pt(2)
        elif line.startswith(("- ", "• ")):
            d.add_paragraph(line[2:].strip(), style="List Bullet").paragraph_format.space_after = Pt(1)
        else:
            d.add_paragraph(line)

def _table(d, rows):
    t = d.add_table(rows=0, cols=2); t.style = "Table Grid"
    for k, v in rows:
        c = t.add_row().cells; c[0].text = k; c[1].text = v or "[확인 필요]"
    return t

def form3_docx(p, header, topic, body, photos) -> bytes:
    d = _doc()
    d.add_heading("모두의 창업 프로젝트 2기 책임멘토 멘토링 보고서", level=1)
    _table(d, header + [("멘토링 주제", topic)])
    d.add_heading("멘토링 내용", level=2)
    _body(d, body)
    d.add_heading("사진", level=2)
    # photos: [(파일명, 실제 경로 또는 None)] — 파일이 있으면 그림으로 넣는다
    # 양식처럼 사진 칸 두 개를 나란히
    from docx.shared import Cm
    photos = [x for x in photos if x and x[0]]
    placed = 0
    if photos:
        t = d.add_table(rows=1, cols=2); t.style = "Table Grid"
        for cell, (name, path) in zip(t.rows[0].cells, photos[:2]):
            par = cell.paragraphs[0]
            if path and path.exists():
                try:
                    par.add_run().add_picture(str(path), width=Cm(6.3)); placed += 1
                except Exception:
                    par.add_run("[사진을 넣지 못함 — 직접 첨부]")
            else:
                par.add_run("[파일을 찾지 못함 — 직접 첨부]")
            cell.add_paragraph(name)
    if placed < 2:
        d.add_paragraph("[사진 2장 이상 첨부 필요]" if not placed else "[사진 1장 더 필요]")
    d.add_paragraph("위와 같이 모두의 창업 프로젝트 책임멘토 멘토링을 진행 하였음을 확인합니다.")
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()

def form4_docx(p, header, body: dict) -> bytes:
    d = _doc()
    d.add_heading("책임멘토 관찰 의견서", level=1)
    _table(d, header)
    for k, label in (("diligence", "성실성"), ("growth", "성장성"), ("excellence", "우수성")):
        d.add_heading(label, level=2)
        _body(d, body.get(k))
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()
