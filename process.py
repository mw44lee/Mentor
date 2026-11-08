"""업무 프로세스 공용 데이터. 신규 프로젝트 생성 시 첫 체크리스트로 복사된다.
(업무 프로세스 페이지·다운로드는 4단계에서 docs/process.md와 함께 확장)"""

COMMON_CHECKLIST = [
    "메타데이터 6종 확인 (번호, 멘티·브랜드, 핵심 아이템, 단계, 주요 마감, 핵심 질문)",
    "멘티가 하고자 하는 일과 멘티 요청 사항 정리 (요청별 체크리스트 연결)",
    "총 회차 수·회차별 시간·수당 조건 확인 (위촉 조건·사업 가이드 기준)",
    "1회차 일시·장소 확정",
    "1회차 준비: 킬러 질문 1~2개, 타임테이블",
    "회차 종료 24시간 안에 기록 입력 (입력 탭)",
]

# 경로별 추가 항목. 확인되지 않은 경로는 규정 확인 항목만 둔다.
CHANNEL_CHECKLIST = {
    "PLAB (프랩)": [
        "운영기관·진출자 닉네임 등 보고서 기재 항목 확인",
        "오프라인 진행 원칙 확인, 회차마다 시작·종료 사진 각 1장·시작·종료 시각 기록",
        "회차마다 양식3(멘토링 내용 1,000자 이상) 초안 작성",
        "보고서 업로드·관찰의견서(양식4) 마감일 일정 등록",
    ],
    "혁신 소상공인 AI 활용 지원 사업": [   # admin\AI활용지원사업 매뉴얼 기준 (업무 프로세스 7~9장)
        "이 사업의 보고 양식·증빙·마감 규정 확인 (매뉴얼에 보고 양식 없음)",
        "1회차: 업무 동선·지출 점검, 병목 3가지 도출, 5대 분야(마케팅·디자인/업무자동화/사업고도화/경영관리/판로) 중 1개 확정 → AI 도입 진단표·과제 정의서",
        "1회차: 도입 전 기준값 기록 (작업 시간, 월 외주비, 판매 비중·객단가 등)",
        "2회차: 맞춤 AI 도구 연동·점주 직접 실습 → 프롬프트 팩·1차 파일럿 제작물",
        "2회차 과제: 매장 1~2주 A/B 테스트·파일럿 (기간·지표·비교 대상 합의)",
        "3회차: 전후 데이터 정리 → STEP 2 사업계획서·예산(정부 80%·자부담 20%)·발표평가 PPT 초안",
        "발표평가·STEP 2 일정 등록 (사업 공지 기준)",
    ],
}
UNKNOWN_CHANNEL_ITEMS = ["이 사업의 보고 양식·증빙·마감 규정 확인 (자료를 admin 폴더에 보관)"]

def checklist_for(channel: str | None) -> list[str]:
    return COMMON_CHECKLIST + CHANNEL_CHECKLIST.get(channel or "", UNKNOWN_CHANNEL_ITEMS)

NEW_PROJECT_CHECKLIST = checklist_for("PLAB (프랩)")  # 업무 프로세스 페이지 기본 표시용

# ---------------- 업무 프로세스 문서 ----------------
import io, re
from pathlib import Path
DOC_PATH = Path(__file__).resolve().parent / "docs" / "process.md"

def process_markdown() -> str:
    """docs/process.md에 경로별 신규 의뢰 체크리스트를 끼워 넣은 최종 문서 (체크리스트 원본은 이 파일 위쪽 목록 하나)."""
    from config import CHANNELS
    parts = []
    for ch in CHANNELS:
        if ch == "기타":
            continue
        parts.append(f"**{ch}**\n")
        parts += [f"- [ ] {t}" for t in checklist_for(ch)]
        parts.append("")
    return DOC_PATH.read_text(encoding="utf-8").replace("{{NEW_PROJECT_CHECKLIST}}", "\n".join(parts).strip())

def process_html() -> tuple[str, str]:
    """(본문 HTML, 목차 HTML)"""
    import markdown
    md = markdown.Markdown(extensions=["tables", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-2"}})
    html = md.convert(process_markdown())
    html = html.replace("<li>[ ] ", '<li class="todo">')
    return html, md.toc

def _runs(par, text):
    """**굵게** 표시를 docx 굵은 글씨로."""
    for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if chunk:
            par.add_run(chunk.replace("`", "")).bold = (i % 2 == 1)

def process_docx() -> bytes:
    import docx
    from docx.shared import Pt
    from docx.oxml.ns import qn
    d = docx.Document()
    st = d.styles["Normal"]; st.font.name = "맑은 고딕"; st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    lines = process_markdown().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1; continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            t = d.add_table(rows=0, cols=len(rows[0])); t.style = "Table Grid"
            for r_i, r in enumerate(rows):
                cells = t.add_row().cells
                for c_i, val in enumerate(r[:len(cells)]):
                    cells[c_i].text = ""
                    _runs(cells[c_i].paragraphs[0], f"**{val}**" if r_i == 0 else val)
            continue
        m = re.match(r"(#{1,4}) (.+)", line)
        if m:
            d.add_heading(m.group(2), level=len(m.group(1)) - 1 if len(m.group(1)) > 1 else 0)
        elif re.match(r"- \[ \] ", line):
            _runs(d.add_paragraph(style="List Bullet"), "☐ " + line[6:])
        elif line.startswith("- "):
            _runs(d.add_paragraph(style="List Bullet"), line[2:])
        elif re.match(r"\d+\. ", line):
            _runs(d.add_paragraph(style="List Number"), re.sub(r"^\d+\. ", "", line))
        else:
            _runs(d.add_paragraph(), line)
        i += 1
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()
