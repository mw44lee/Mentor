"""업로드 파일 분류와 텍스트 추출. 음성은 OpenAI Whisper로 받아쓴다."""
import mimetypes, os, subprocess, tempfile
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".webm", ".mp4", ".amr", ".3gp", ".flac"}
TEXT_EXT = {".txt", ".md", ".csv"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
WHISPER_LIMIT = 24 * 1024 * 1024  # Whisper 업로드 한도 25MB보다 약간 작게

def classify(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT: return "image"
    if ext in AUDIO_EXT: return "audio"
    if ext == ".pdf": return "pdf"
    if ext == ".docx": return "docx"
    if ext in (".xlsx", ".xlsm"): return "xlsx"
    if ext in TEXT_EXT: return "text"
    return "other"

def mime_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return IMAGE_MIME.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"

def extract_text(path: Path, media: str) -> tuple[str | None, str | None]:
    """(추출 텍스트, 메모) 반환. 이미지는 Claude가 직접 보므로 텍스트 없음."""
    try:
        if media == "text":
            raw = path.read_bytes()
            for enc in ("utf-8-sig", "cp949"):
                try:
                    return raw.decode(enc), None
                except UnicodeDecodeError:
                    pass
            return raw.decode("utf-8", errors="replace"), "문자 인코딩 일부 손상"
        if media == "docx":
            import docx
            d = docx.Document(str(path))
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for t in d.tables:
                for row in t.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
            return "\n".join(parts), None
        if media == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"[시트: {ws.title}]")
                for row in ws.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        parts.append(" | ".join("" if v is None else str(v) for v in row))
            return "\n".join(parts), None
        if media == "pdf":
            from pypdf import PdfReader
            r = PdfReader(str(path))
            text = "\n".join((pg.extract_text() or "") for pg in r.pages)
            # 스캔 PDF처럼 글자가 없으면 Claude에 PDF 원본을 그대로 보냄
            return (text if text.strip() else None), (None if text.strip() else "글자 추출 불가 — 원본 PDF를 AI에 직접 전달")
    except Exception as e:  # 파일 손상 등: 원본은 보관하고 메모만 남김
        return None, f"텍스트 추출 실패: {type(e).__name__}"
    return None, None

def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def _split_audio(path: Path, workdir: Path, seconds: int = 600) -> list[Path]:
    """mono 16kHz 32kbps mp3로 변환하며 10분 단위로 나눔 (2시간 ≈ 29MB → 조각당 약 2.4MB)."""
    pattern = workdir / "part_%03d.mp3"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                    "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "segment", "-segment_time", str(seconds),
                    str(pattern)], check=True, capture_output=True)
    return sorted(workdir.glob("part_*.mp3"))

def transcribe(path: Path) -> tuple[str | None, str]:
    """(전사본, 메모). 키가 없거나 실패하면 전사본 None."""
    if not os.getenv("OPENAI_API_KEY"):
        return None, "녹음 파일은 보관만 됩니다. 텍스트로 변환한 파일(.txt)을 함께 올려 주세요"
    from openai import OpenAI
    client = OpenAI()
    try:
        with tempfile.TemporaryDirectory() as td:
            parts = [path] if path.stat().st_size <= WHISPER_LIMIT and path.suffix.lower() in (".mp3", ".m4a", ".wav", ".webm", ".mp4") \
                else _split_audio(path, Path(td))
            texts = []
            for i, part in enumerate(parts):
                with open(part, "rb") as f:
                    r = client.audio.transcriptions.create(model="whisper-1", file=f, language="ko")
                texts.append(r.text.strip())
            note = f"Whisper 받아쓰기 완료 ({len(parts)}개 구간)"
            return "\n\n".join(texts), note
    except subprocess.CalledProcessError:
        return None, "음성 파일 변환 실패 (지원하지 않는 형식이거나 손상된 파일)"
    except Exception as e:
        return None, f"받아쓰기 실패: {type(e).__name__} — 잠시 후 '다시 받아쓰기'"
