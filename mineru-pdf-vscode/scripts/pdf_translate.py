#!/usr/bin/env python3
"""
Batch-translate local PDF files with MinerU extraction and an OpenAI-compatible LLM.

This implementation is intentionally self-contained for VS Code + Claude Code workflows:
- stdlib HTTP client for MinerU, uploads, downloads, and chat completions
- optional auto-install of the `markdown` package for HTML rendering
- Edge/Chrome/Chromium headless printing for final PDF output
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SKILL_DIR = Path(__file__).resolve().parent.parent

MINERU_CREATE_TASK_URL = "https://mineru.net/api/v4/extract/task"
MINERU_TASK_URL_TEMPLATE = "https://mineru.net/api/v4/extract/task/{task_id}"
DEFAULT_UPLOAD_API_URL = "https://tmpfiles.org/api/v1/upload"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TARGET_LANGUAGE = "Simplified Chinese"
DEFAULT_TARGET_SUFFIX = "zh"
DEFAULT_SOURCE_LANGUAGE = "en"
DEFAULT_OUTPUT_DIR = "translated"
DEFAULT_TEMP_DIR = ".pdf_translate_tmp"
DEFAULT_MAX_CHARS = 10000
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 30 * 60
LLM_MAX_RETRIES = 5


class PipelineError(RuntimeError):
    """Raised when a recoverable pipeline step fails."""


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class RuntimeConfig:
    mineru_token: str
    llm: LlmConfig
    browser_path: str


def log(message: str) -> None:
    print(message, flush=True)


def read_text_if_exists(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def env_value(workdir: Path, name: str) -> str | None:
    env_file_values = read_env_file(workdir / ".env")
    skill_env_values = read_env_file(SKILL_DIR / ".env")
    return os.environ.get(name) or env_file_values.get(name) or skill_env_values.get(name)


def load_mineru_token(workdir: Path, override: str | None) -> str:
    token = (
        override
        or read_text_if_exists(workdir / "mineru密钥.txt")
        or read_text_if_exists(SKILL_DIR / "mineru密钥.txt")
        or env_value(workdir, "MINERU_API_TOKEN")
    )
    if not token:
        raise PipelineError(
            "MinerU token not found. Set MINERU_API_TOKEN, create .env "
            "in the PDF working directory or skill directory, "
            "or create mineru密钥.txt in the working or skill directory."
        )
    return token


def load_llm_config(workdir: Path, base_url: str | None, api_key: str | None, model: str | None) -> LlmConfig:
    file_base_url = None
    file_api_key = None
    for config_file in (workdir / "翻译大模型url以及key.txt", SKILL_DIR / "翻译大模型url以及key.txt"):
        local_text = read_text_if_exists(config_file)
        if local_text:
            lines = [line.strip() for line in local_text.splitlines() if line.strip()]
            if len(lines) >= 2:
                file_base_url = lines[0]
                file_api_key = lines[1]
                break

    final_base_url = (base_url or file_base_url or env_value(workdir, "PDF_TRANSLATE_LLM_BASE_URL") or "").rstrip("/")
    final_api_key = api_key or file_api_key or env_value(workdir, "PDF_TRANSLATE_LLM_API_KEY") or ""
    final_model = model or env_value(workdir, "PDF_TRANSLATE_MODEL") or DEFAULT_MODEL

    if not final_base_url or not final_api_key:
        raise PipelineError(
            "LLM config not found. Set PDF_TRANSLATE_LLM_BASE_URL and PDF_TRANSLATE_LLM_API_KEY, "
            "create .env in the PDF working directory or skill directory, "
            "or create 翻译大模型url以及key.txt in the working or skill directory."
        )
    return LlmConfig(base_url=final_base_url, api_key=final_api_key, model=final_model)


def detect_browser(explicit_path: str | None, workdir: Path | None = None) -> str:
    candidates: list[str] = []
    env_browser = os.environ.get("PDF_TRANSLATE_BROWSER")
    if workdir:
        env_browser = env_browser or env_value(workdir, "PDF_TRANSLATE_BROWSER")
    if explicit_path:
        candidates.append(explicit_path)
    if env_browser:
        candidates.append(env_browser)

    for executable in [
        "msedge",
        "microsoft-edge",
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
    ]:
        found = shutil.which(executable)
        if found:
            candidates.append(found)

    candidates.extend(
        [
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if Path(candidate).exists():
            return candidate
    raise PipelineError("No Edge/Chrome/Chromium executable found. Set --browser-path or PDF_TRANSLATE_BROWSER.")


def import_markdown(auto_install: bool):
    try:
        import markdown as markdown_module  # type: ignore
        return markdown_module
    except ImportError:
        if not auto_install:
            raise PipelineError("Python package `markdown` is missing. Install it with: python -m pip install markdown")
        log("Python package `markdown` not found; installing it now...")
        subprocess.run([sys.executable, "-m", "pip", "install", "markdown"], check=True)
        import markdown as markdown_module  # type: ignore
        return markdown_module


def json_request(method: str, url: str, *, headers: dict[str, str] | None = None, payload: dict | None = None, timeout: int = 120) -> dict:
    body = None
    final_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PipelineError(f"HTTP {exc.code} for {url}: {detail[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise PipelineError(f"Request failed for {url}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Invalid JSON from {url}: {text[:1000]}") from exc


def download_file(url: str, out_path: Path, timeout: int = 600) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "mineru-pdf-vscode/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response, out_path.open("wb") as output:
            shutil.copyfileobj(response, output)
    except urllib.error.URLError as exc:
        raise PipelineError(f"Download failed for {url}: {exc}") from exc


def multipart_upload_file(pdf_path: Path, upload_api_url: str) -> str:
    boundary = f"----mineru-vscode-{uuid.uuid4().hex}"
    file_bytes = pdf_path.read_bytes()
    filename = pdf_path.name
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
        b"Content-Type: application/pdf\r\n\r\n",
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        upload_api_url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "mineru-pdf-vscode/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PipelineError(f"Upload HTTP {exc.code}: {detail[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise PipelineError(f"Upload failed: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Upload response is not JSON: {text[:1000]}") from exc

    if payload.get("status") != "success" or not payload.get("data", {}).get("url"):
        raise PipelineError(f"Upload service did not return a usable URL: {json.dumps(payload, ensure_ascii=False)[:1000]}")

    raw_url = payload["data"]["url"]
    if "tmpfiles.org/" in raw_url and "/dl/" not in raw_url:
        raw_url = raw_url.replace("http://tmpfiles.org/", "https://tmpfiles.org/dl/").replace(
            "https://tmpfiles.org/", "https://tmpfiles.org/dl/"
        )
    return raw_url


def create_mineru_task(file_url: str, token: str, source_language: str, ocr: bool) -> str:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "source": "claude-code"}
    payload = {
        "url": file_url,
        "is_ocr": ocr,
        "enable_formula": True,
        "enable_table": True,
        "language": source_language,
    }
    response = json_request("POST", MINERU_CREATE_TASK_URL, headers=headers, payload=payload, timeout=180)
    if response.get("code") != 0:
        raise PipelineError(f"MinerU task creation failed: {json.dumps(response, ensure_ascii=False)[:1000]}")
    task_id = response.get("data", {}).get("task_id")
    if not task_id:
        raise PipelineError(f"MinerU response missing task_id: {json.dumps(response, ensure_ascii=False)[:1000]}")
    return task_id


def wait_for_mineru(task_id: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "source": "claude-code"}
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = json_request("GET", MINERU_TASK_URL_TEMPLATE.format(task_id=task_id), headers=headers, timeout=120)
        if response.get("code") != 0:
            raise PipelineError(f"MinerU polling failed: {json.dumps(response, ensure_ascii=False)[:1000]}")
        data = response.get("data", {})
        state = data.get("state")
        if state == "done":
            return data
        if state == "failed":
            raise PipelineError(f"MinerU parsing failed: {data.get('err_msg') or data}")
        progress = data.get("extract_progress") or {}
        extracted = progress.get("extracted_pages")
        total = progress.get("total_pages")
        if extracted is not None and total is not None:
            log(f"  MinerU progress: {extracted}/{total}")
        else:
            log(f"  MinerU state: {state or 'unknown'}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise PipelineError(f"MinerU polling timed out after {POLL_TIMEOUT_SECONDS} seconds for task {task_id}")


def safe_extract(zip_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_root = out_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            target = (out_dir / member.filename).resolve()
            if out_root not in target.parents and target != out_root:
                raise PipelineError(f"Unsafe ZIP member path: {member.filename}")
        archive.extractall(out_dir)


def find_markdown_file(extract_dir: Path) -> Path:
    preferred = sorted(extract_dir.rglob("full.md"))
    if preferred:
        return preferred[0]
    markdown_files = sorted(extract_dir.rglob("*.md"))
    if markdown_files:
        return markdown_files[0]
    raise PipelineError(f"No Markdown file found in MinerU result: {extract_dir}")


def iter_input_pdfs(workdir: Path, target_suffix: str) -> Iterable[Path]:
    translated_suffix = f"_{target_suffix}.pdf"
    for path in sorted(workdir.glob("*.pdf")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("_tmp_") or name.endswith(translated_suffix.lower()):
            continue
        yield path


def protect_segments(markdown_text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}

    patterns = [
        r"```[\s\S]*?```",                  # fenced code blocks
        r"!\[[^\]]*\]\([^\)\n]+\)",       # markdown images
        r"`[^`\n]+`",                        # inline code
        r"\$\$[\s\S]*?\$\$",               # display math
        r"\\\[[\s\S]*?\\\]",              # \[ ... \]
        r"\\\([\s\S]*?\\\)",              # \( ... \)
    ]
    combined = re.compile("|".join(f"({pattern})" for pattern in patterns))

    def replace(match: re.Match[str]) -> str:
        key = f"@@PROTECTED_{len(placeholders)}@@"
        placeholders[key] = match.group(0)
        return key

    return combined.sub(replace, markdown_text), placeholders


def restore_segments(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for key, value in placeholders.items():
        restored = restored.replace(key, value)
    return restored


def split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    blocks = re.split(r"(\n\s*\n)", text)
    for block in blocks:
        if not block:
            continue
        if len(block) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            lines = block.splitlines(keepends=True)
            piece = ""
            for line in lines:
                if len(piece) + len(line) > max_chars and piece:
                    chunks.append(piece)
                    piece = ""
                piece += line
            if piece:
                chunks.append(piece)
            continue
        if len(current) + len(block) > max_chars and current:
            chunks.append(current)
            current = ""
        current += block
    if current:
        chunks.append(current)
    return chunks


def translate_chunk(chunk: str, llm: LlmConfig, target_language: str, temperature: float) -> str:
    prompt = (
        f"Translate the following Markdown into {target_language}. "
        "Preserve Markdown structure, heading levels, tables, numbering, citations, URLs, protected placeholders, "
        "file paths, formulas, and code exactly. Do not add explanations, notes, or code fences. "
        "Return only translated Markdown."
    )
    payload = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": "You are a precise technical and academic document translator."},
            {"role": "user", "content": prompt + "\n\n" + chunk},
        ],
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"}
    last_error: Exception | None = None
    endpoint = f"{llm.base_url}/v1/chat/completions"
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            response = json_request("POST", endpoint, headers=headers, payload=payload, timeout=300)
            return response["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == LLM_MAX_RETRIES:
                break
            delay = min(30, attempt * 5)
            log(f"  LLM request failed; retrying in {delay}s ({attempt}/{LLM_MAX_RETRIES}): {exc}")
            time.sleep(delay)
    assert last_error is not None
    raise PipelineError(f"LLM translation failed: {last_error}")


def strip_headers_footers(markdown_text: str, min_repeat: int = 2, max_line_len: int = 150) -> str:
    lines = markdown_text.splitlines()
    freq: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        freq[stripped] = freq.get(stripped, 0) + 1

    line_blacklist = {k for k, v in freq.items() if v >= min_repeat and len(k) <= max_line_len}

    header_pattern = re.compile(
        r"^\s*\d{1,4}\s*$"
        r"|^\s*[ivxlcdm]{1,6}\s*$"
        r"|^\s*https?://doi\.org/"
        r"|^\s*DOI:\s*"
        r"|^\s*(©|\(?[cC]\)?\s*)?(20\d{2}|19\d{2})\b"
        r"|^\s*ISSN[\s:]\d{4}-\d{4}"
        r"|^\s*ISBN[\s:]\d"
        r"|^\s*(Received|Accepted|Published)[\s:].*\b(20\d{2}|19\d{2})\b"
        r"|^\s*Vol(ume)?\.?\s*\d+"
        r"|^\s*(No\.|Number|Issue)\s*\d+"
        r"|^\s*(pp?\.?\s*)?\d{1,4}[-–]\d{1,4}\s*$"
    )

    filtered: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if prev_blank:
                continue
            prev_blank = True
            filtered.append(line)
            continue
        prev_blank = False
        if stripped in line_blacklist:
            continue
        if header_pattern.search(stripped):
            continue
        filtered.append(line)

    return "\n".join(filtered).strip() + "\n"


def translate_markdown(markdown_path: Path, llm: LlmConfig, target_language: str, max_chars: int, temperature: float, no_strip_headers: bool = False) -> str:
    source = markdown_path.read_text(encoding="utf-8")
    if no_strip_headers:
        filtered_source = source
    else:
        filtered_source = strip_headers_footers(source)
        removed = len(source.splitlines()) - len(filtered_source.splitlines())
        if removed:
            log(f"  Stripped {removed} header/footer lines")
    protected_text, placeholders = protect_segments(filtered_source)
    chunks = split_text(protected_text, max_chars=max_chars)
    translated_chunks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        log(f"  Translating chunk {index}/{len(chunks)}")
        translated_chunks.append(translate_chunk(chunk, llm, target_language, temperature))
    return restore_segments("".join(translated_chunks), placeholders)


def markdown_to_html(markdown_module, markdown_text: str) -> str:
    return markdown_module.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists", "toc", "nl2br"],
        output_format="html5",
    )


def build_html_document(title: str, body_html: str) -> str:
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] }},
      svg: {{ fontCache: 'global' }},
      options: {{ enableMenu: false }}
    }};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mathjax@4/tex-svg.js"></script>
  <style>
    @page {{ margin: 20mm 16mm; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif; line-height: 1.65; color: #111; }}
    img {{ max-width: 100%; height: auto; display: block; margin: 1rem auto; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.92rem; }}
    th, td {{ border: 1px solid #bbb; padding: 0.35rem 0.5rem; vertical-align: top; }}
    pre, code {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }}
    pre {{ white-space: pre-wrap; background: #f6f8fa; padding: 0.8rem; border-radius: 6px; overflow-wrap: anywhere; }}
    h1, h2, h3 {{ break-after: avoid; }}
    mjx-container {{ overflow-x: auto; overflow-y: hidden; max-width: 100%; }}
  </style>
</head>
<body>
{body_html}
</body>
</html>"""


def render_pdf(markdown_module, markdown_text: str, asset_base_dir: Path, out_pdf: Path, title: str, browser_path: str) -> None:
    body = markdown_to_html(markdown_module, markdown_text)
    html_doc = build_html_document(title, body)
    html_path = asset_base_dir / "_translated_render.html"
    html_path.write_text(html_doc, encoding="utf-8")
    try:
        cmd = [
            browser_path,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=15000",
            f"--print-to-pdf={out_pdf.resolve()}",
            "--print-to-pdf-no-header",
            html_path.resolve().as_uri(),
        ]
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise PipelineError(f"Browser PDF render failed ({result.returncode}): {detail[:2000]}")
    finally:
        if html_path.exists():
            html_path.unlink()


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    temp_root: Path,
    runtime: RuntimeConfig,
    markdown_module,
    args: argparse.Namespace,
) -> None:
    doc_temp = temp_root / pdf_path.stem
    if doc_temp.exists():
        shutil.rmtree(doc_temp)
    doc_temp.mkdir(parents=True, exist_ok=True)

    log(f"  Uploading {pdf_path.name}")
    public_pdf_url = multipart_upload_file(pdf_path, args.upload_api_url)

    log("  Creating MinerU extraction task")
    task_id = create_mineru_task(public_pdf_url, runtime.mineru_token, args.source_language, args.ocr)
    log(f"  MinerU task id: {task_id}")

    mineru_data = wait_for_mineru(task_id, runtime.mineru_token)
    zip_url = mineru_data.get("full_zip_url") or mineru_data.get("zip_url")
    if not zip_url:
        raise PipelineError(f"MinerU result does not include full_zip_url: {json.dumps(mineru_data, ensure_ascii=False)[:1000]}")

    zip_path = doc_temp / "mineru_result.zip"
    log("  Downloading MinerU ZIP result")
    download_file(zip_url, zip_path)

    extract_dir = doc_temp / "mineru"
    safe_extract(zip_path, extract_dir)
    markdown_path = find_markdown_file(extract_dir)

    translated_markdown = translate_markdown(
        markdown_path,
        runtime.llm,
        args.target_language,
        max_chars=args.max_chars,
        temperature=args.temperature,
        no_strip_headers=args.no_strip_headers,
    )

    if args.keep_markdown:
        md_out = output_dir / f"{pdf_path.stem}_{args.target_suffix}.md"
        md_out.write_text(translated_markdown, encoding="utf-8")

    out_pdf = output_dir / f"{pdf_path.stem}_{args.target_suffix}.pdf"
    log(f"  Rendering final PDF: {out_pdf.name}")
    render_pdf(markdown_module, translated_markdown, markdown_path.parent, out_pdf, pdf_path.stem, runtime.browser_path)

    if not args.keep_temp:
        shutil.rmtree(doc_temp, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translate PDFs with MinerU + OpenAI-compatible LLM + headless browser rendering.")
    parser.add_argument("--workdir", default=".", help="Folder containing source PDFs and optional config files.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output folder relative to workdir unless absolute.")
    parser.add_argument("--temp-dir", default=DEFAULT_TEMP_DIR, help="Temp folder relative to workdir unless absolute.")
    parser.add_argument("--target-language", default=DEFAULT_TARGET_LANGUAGE, help="Target translation language.")
    parser.add_argument("--target-suffix", default=DEFAULT_TARGET_SUFFIX, help="Suffix appended to translated output names.")
    parser.add_argument("--source-language", default=DEFAULT_SOURCE_LANGUAGE, help="Source language hint for MinerU, e.g. en or ch.")
    parser.add_argument("--ocr", action="store_true", help="Ask MinerU to use OCR mode.")
    parser.add_argument("--mineru-token", default=None, help="Override MinerU API token.")
    parser.add_argument("--llm-base-url", default=None, help="Override OpenAI-compatible base URL.")
    parser.add_argument("--llm-api-key", default=None, help="Override OpenAI-compatible API key.")
    parser.add_argument("--llm-model", default=None, help=f"LLM model name. Default: {DEFAULT_MODEL}.")
    parser.add_argument("--browser-path", default=None, help="Path to Edge/Chrome/Chromium executable.")
    parser.add_argument("--upload-api-url", default=DEFAULT_UPLOAD_API_URL, help="Temporary upload API URL.")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Maximum characters per translation chunk.")
    parser.add_argument("--temperature", type=float, default=0.2, help="LLM temperature for translation.")
    parser.add_argument("--force", action="store_true", help="Rebuild outputs even when translated PDFs already exist.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary extraction files after completion.")
    parser.add_argument("--keep-markdown", action="store_true", help="Also save translated Markdown next to final PDFs.")
    parser.add_argument("--no-auto-install", action="store_true", help="Do not auto-install the `markdown` package if missing.")
    parser.add_argument("--no-strip-headers", action="store_true", dest="no_strip_headers", help="Do not strip repeated headers/footers from MinerU Markdown output.")
    parser.add_argument("--check", action="store_true", help="Check local environment and config without running translation.")
    return parser


def resolve_child_path(workdir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else workdir / path


def run_check(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    log(f"workdir: {workdir}")
    log(f"skill dir: {SKILL_DIR}")
    log(f"pdf files: {len(list(iter_input_pdfs(workdir, args.target_suffix))) if workdir.exists() else 0}")
    for label, ok in [
        ("mineru token", bool(args.mineru_token or read_text_if_exists(workdir / "mineru密钥.txt") or read_text_if_exists(SKILL_DIR / "mineru密钥.txt") or env_value(workdir, "MINERU_API_TOKEN"))),
        ("llm base url", bool(args.llm_base_url or env_value(workdir, "PDF_TRANSLATE_LLM_BASE_URL"))),
        ("llm api key", bool(args.llm_api_key or env_value(workdir, "PDF_TRANSLATE_LLM_API_KEY"))),
    ]:
        log(f"{label}: {'ok' if ok else 'missing'}")
    for local_two_line in (workdir / "翻译大模型url以及key.txt", SKILL_DIR / "翻译大模型url以及key.txt"):
        if local_two_line.exists():
            log(f"two-line llm config file: {local_two_line}")
    try:
        browser = detect_browser(args.browser_path, workdir)
        log(f"browser: {browser}")
    except PipelineError as exc:
        log(f"browser: missing ({exc})")
    try:
        import_markdown(auto_install=False)
        log("python markdown package: ok")
    except Exception:
        log("python markdown package: missing")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.check:
        return run_check(args)

    workdir = Path(args.workdir).expanduser().resolve()
    if not workdir.exists():
        raise PipelineError(f"Workdir does not exist: {workdir}")

    output_dir = resolve_child_path(workdir, args.output_dir).resolve()
    temp_root = resolve_child_path(workdir, args.temp_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    runtime = RuntimeConfig(
        mineru_token=load_mineru_token(workdir, args.mineru_token),
        llm=load_llm_config(workdir, args.llm_base_url, args.llm_api_key, args.llm_model),
        browser_path=detect_browser(args.browser_path, workdir),
    )
    markdown_module = import_markdown(auto_install=not args.no_auto_install)

    pdfs = list(iter_input_pdfs(workdir, args.target_suffix))
    if not pdfs:
        log("No input PDFs found in the top level of workdir.")
        return 0

    failures: list[dict[str, str]] = []
    for index, pdf_path in enumerate(pdfs, start=1):
        out_pdf = output_dir / f"{pdf_path.stem}_{args.target_suffix}.pdf"
        if out_pdf.exists() and not args.force:
            log(f"[{index}/{len(pdfs)}] Skipping existing output: {out_pdf.name}")
            continue
        log(f"[{index}/{len(pdfs)}] Processing {pdf_path.name}")
        try:
            process_pdf(pdf_path, output_dir, temp_root, runtime, markdown_module, args)
            log(f"[{index}/{len(pdfs)}] Done: {pdf_path.name}")
        except Exception as exc:  # noqa: BLE001
            log(f"[{index}/{len(pdfs)}] Failed: {pdf_path.name}")
            log(f"  Error: {exc}")
            failures.append({"pdf": str(pdf_path), "error": str(exc)})

    if not args.keep_temp:
        for child in temp_root.glob("*"):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    failures_path = output_dir / "failures.json"
    if failures:
        failures_path.write_text(json.dumps({"failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Completed with {len(failures)} failure(s). See {failures_path}")
        return 1
    if failures_path.exists():
        failures_path.unlink()
    log("All PDFs processed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
