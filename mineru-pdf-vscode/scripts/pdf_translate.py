#!/usr/bin/env python3
"""
Batch-translate local PDF files with MinerU extraction and an OpenAI-compatible LLM.

This implementation is intentionally self-contained for VS Code + Claude Code workflows:
- stdlib HTTP client for MinerU, uploads, downloads, and chat completions
- optional auto-install of the `markdown` package for HTML rendering
- Edge/Chrome/Chromium headless printing for final PDF output
- pandoc for DOCX generation with native formula rendering
"""
from __future__ import annotations

import argparse
import html
import html.parser
import http.client
import json
import os
import re
import shutil
import ssl
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

MINERU_BASE = "https://mineru.net"
MINERU_BATCH_URL = "https://mineru.net/api/v4/file-urls/batch"
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
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
        "msedge",
        "microsoft-edge",
    ]:
        found = shutil.which(executable)
        if found:
            candidates.append(found)

    candidates.extend(
        [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
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


PANDOC_INSTALL_HINT = (
    "Pandoc is required for DOCX output. Install it from https://pandoc.org/installing.html\n"
    "  Windows: winget install pandoc   or   choco install pandoc\n"
    "  macOS:   brew install pandoc\n"
    "  Linux:   sudo apt install pandoc   or   sudo dnf install pandoc"
)


def find_pandoc() -> str | None:
    """Return the path to a working pandoc executable, or None."""
    # Common Windows install paths
    if sys.platform == "win32":
        for base in [os.environ.get("ProgramFiles", "C:\\Program Files"),
                     os.environ.get("LOCALAPPDATA", ""),
                     os.path.expandvars(r"%ProgramFiles%")]:
            if base:
                candidate = os.path.join(base, "Pandoc", "pandoc.exe")
                if os.path.isfile(candidate):
                    return candidate
    path = shutil.which("pandoc")
    return path if path else None


def ensure_pandoc(auto_install: bool) -> str:
    """Find pandoc, or raise PipelineError with install instructions."""
    pandoc_path = find_pandoc()
    if pandoc_path:
        return pandoc_path
    if auto_install:
        raise PipelineError(PANDOC_INSTALL_HINT)
    raise PipelineError("Pandoc not found. Run with --no-auto-install to suppress install hints.")


def ensure_reference_docx(pandoc_path: str, skill_dir: Path) -> Path | None:
    """Return path to a reference.docx with CJK-friendly defaults.

    Uses *skill_dir*/assets/reference.docx if it exists; otherwise generates
    one from pandoc's default template and patches styles for Chinese readability.
    Returns None when generation fails (caller falls back to no --reference-doc).
    """
    ref_path = skill_dir / "assets" / "reference.docx"
    if ref_path.exists():
        return ref_path
    try:
        result = subprocess.run(
            [pandoc_path, "--print-default-data-file", "reference.docx"],
            capture_output=True, check=True,
        )
        _patch_reference_styles(result.stdout, ref_path)
        log(f"  Generated reference.docx → {ref_path}")
        return ref_path
    except Exception as exc:
        log(f"  Could not generate reference.docx: {exc}")
        return None


# ---- reference.docx style patching -----------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _style_by_id(root, style_id: str):
    for el in root.findall(f"{{{_W_NS}}}style"):
        if el.get(f"{{{_W_NS}}}styleId") == style_id:
            return el
    return None


def _sub_element(parent, tag: str):
    from xml.etree import ElementTree as _ET
    return _ET.SubElement(parent, f"{{{_W_NS}}}{tag}")


def _ensure_child(parent, tag: str):
    child = parent.find(f"{{{_W_NS}}}{tag}")
    if child is None:
        child = _sub_element(parent, tag)
    return child


def _patch_reference_styles(docx_bytes: bytes, out_path: Path) -> None:
    """Adjust Normal / Heading / Code fonts in a pandoc reference docx."""
    import io as _io
    from xml.etree import ElementTree as _ET

    _ET.register_namespace("", _W_NS)
    _ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")

    with zipfile.ZipFile(_io.BytesIO(docx_bytes), "r") as zin:
        files = {name: zin.read(name) for name in zin.namelist()}

    styles_xml = files.get("word/styles.xml")
    if styles_xml is None:
        return

    root = _ET.fromstring(styles_xml)

    # ---- Normal style ------------------------------------------------------
    normal = _style_by_id(root, "Normal")
    if normal is not None:
        rpr = _ensure_child(normal, "rPr")

        # Font: Times New Roman (Latin) + Microsoft YaHei (CJK)
        rfonts = rpr.find(f"{{{_W_NS}}}rFonts")
        if rfonts is None:
            rfonts = _sub_element(rpr, "rFonts")
        rfonts.set(f"{{{_W_NS}}}ascii", "Times New Roman")
        rfonts.set(f"{{{_W_NS}}}hAnsi", "Times New Roman")
        rfonts.set(f"{{{_W_NS}}}eastAsia", "SimSun")
        rfonts.set(f"{{{_W_NS}}}cs", "Times New Roman")

        # 小四 = 12 pt = 24 half-pts
        for tag in ("sz", "szCs"):
            sz_el = rpr.find(f"{{{_W_NS}}}{tag}")
            if sz_el is None:
                sz_el = _sub_element(rpr, tag)
            sz_el.set(f"{{{_W_NS}}}val", "24")

        # Paragraph spacing: 1.5× line height, 0 pt before/after
        ppr = _ensure_child(normal, "pPr")
        spacing = ppr.find(f"{{{_W_NS}}}spacing")
        if spacing is None:
            spacing = _sub_element(ppr, "spacing")
        spacing.set(f"{{{_W_NS}}}line", "360")
        spacing.set(f"{{{_W_NS}}}lineRule", "auto")
        spacing.set(f"{{{_W_NS}}}before", "0")
        spacing.set(f"{{{_W_NS}}}after", "0")

    # ---- Heading styles ----------------------------------------------------
    # 黑体, descending from 三号(16pt) → 四号(14pt) → 小四(12pt)
    heading_sizes = {"1": "32", "2": "28", "3": "28", "4": "24", "5": "24", "6": "24"}
    for lvl, sz in heading_sizes.items():
        heading = _style_by_id(root, f"Heading{lvl}")
        if heading is None:
            continue
        rpr = _ensure_child(heading, "rPr")
        rfonts = rpr.find(f"{{{_W_NS}}}rFonts")
        if rfonts is None:
            rfonts = _sub_element(rpr, "rFonts")
        rfonts.set(f"{{{_W_NS}}}ascii", "Times New Roman")
        rfonts.set(f"{{{_W_NS}}}hAnsi", "Times New Roman")
        rfonts.set(f"{{{_W_NS}}}eastAsia", "SimHei")
        for tag in ("sz", "szCs"):
            sz_el = rpr.find(f"{{{_W_NS}}}{tag}")
            if sz_el is None:
                sz_el = _sub_element(rpr, tag)
            sz_el.set(f"{{{_W_NS}}}val", sz)

    # ---- Verbatim Char (inline code) ---------------------------------------
    vc = _style_by_id(root, "VerbatimChar")
    if vc is not None:
        rpr = _ensure_child(vc, "rPr")
        rfonts = rpr.find(f"{{{_W_NS}}}rFonts")
        if rfonts is None:
            rfonts = _sub_element(rpr, "rFonts")
        rfonts.set(f"{{{_W_NS}}}ascii", "Consolas")
        rfonts.set(f"{{{_W_NS}}}hAnsi", "Consolas")
        rfonts.set(f"{{{_W_NS}}}cs", "Consolas")
        for tag in ("sz", "szCs"):
            sz_el = rpr.find(f"{{{_W_NS}}}{tag}")
            if sz_el is None:
                sz_el = _sub_element(rpr, tag)
            sz_el.set(f"{{{_W_NS}}}val", "20")  # 10 pt

    # ---- Source Code (code blocks) -----------------------------------------
    sc = _style_by_id(root, "SourceCode")
    if sc is not None:
        rpr = _ensure_child(sc, "rPr")
        rfonts = rpr.find(f"{{{_W_NS}}}rFonts")
        if rfonts is None:
            rfonts = _sub_element(rpr, "rFonts")
        rfonts.set(f"{{{_W_NS}}}ascii", "Consolas")
        rfonts.set(f"{{{_W_NS}}}hAnsi", "Consolas")
        rfonts.set(f"{{{_W_NS}}}cs", "Consolas")
        for tag in ("sz", "szCs"):
            sz_el = rpr.find(f"{{{_W_NS}}}{tag}")
            if sz_el is None:
                sz_el = _sub_element(rpr, tag)
            sz_el.set(f"{{{_W_NS}}}val", "18")  # 9 pt

    files["word/styles.xml"] = _ET.tostring(root, encoding="UTF-8", xml_declaration=True)

    with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)

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


def mineru_batch_upload(pdf_path: Path, token: str, source_language: str, ocr: bool) -> str:
    """Upload PDF directly to MinerU via batch API and return the full_zip_url."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "source": "claude-code"}

    log("  Requesting MinerU presigned upload URL")
    batch_payload = {
        "files": [{"name": pdf_path.name}],
        "model_version": "pipeline",
        "enable_formula": True,
    }
    batch_resp = json_request("POST", MINERU_BATCH_URL, headers=headers, payload=batch_payload)
    if batch_resp.get("code") != 0:
        raise PipelineError(f"Batch upload request failed: {json.dumps(batch_resp, ensure_ascii=False)[:1000]}")

    batch_id = batch_resp.get("data", {}).get("batch_id")
    file_urls = batch_resp.get("data", {}).get("file_urls", [])
    if not batch_id or not file_urls:
        raise PipelineError(f"Batch response missing batch_id/file_urls: {json.dumps(batch_resp, ensure_ascii=False)[:1000]}")

    log("  Uploading PDF to MinerU directly")
    file_bytes = pdf_path.read_bytes()
    parsed = urllib.parse.urlparse(file_urls[0])
    host = parsed.hostname
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    port = parsed.port or 443
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=300)
    try:
        conn.putrequest("PUT", path)
        conn.putheader("User-Agent", "mineru-pdf-vscode/1.0")
        conn.putheader("Content-Length", str(len(file_bytes)))
        conn.endheaders()
        conn.send(file_bytes)
        resp = conn.getresponse()
        if resp.status not in (200, 204):
            body = resp.read().decode("utf-8", errors="replace")
            raise PipelineError(f"Direct upload failed: HTTP {resp.status}: {body[:1000]}")
    except (http.client.HTTPException, OSError) as exc:
        raise PipelineError(f"Direct upload failed: {exc}") from exc
    finally:
        conn.close()

    log(f"  MinerU batch id: {batch_id}")
    log("  Waiting for MinerU to process extraction")
    batch_poll_url = f"{MINERU_BASE}/api/v4/extract-results/batch/{batch_id}"
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        batch_resp = json_request("GET", batch_poll_url, headers=headers, timeout=120)
        if batch_resp.get("code") != 0:
            log(f"  Batch poll: code={batch_resp.get('code')}, msg={batch_resp.get('msg', '?')}")
            continue
        items = (batch_resp.get("data") or {}).get("extract_result") or []
        if not items:
            log("  Batch poll: waiting for results")
            continue
        item = items[0]
        state = item.get("state") or ""
        if state == "done":
            zip_url = item.get("full_zip_url")
            if zip_url:
                return zip_url
            raise PipelineError(f"Batch done but no full_zip_url: {item}")
        if state == "failed":
            raise PipelineError(f"Batch extraction failed: {item.get('err_msg') or item}")
        log(f"  Batch state: {state}")

    raise PipelineError(f"Batch polling timed out for batch {batch_id}")


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
        r"\$\$[\s\S]*?\$\$",               # display math $$...$$
        r"\\\[[\s\S]*?\\\]",              # \[ ... \]
        r"\\\([\s\S]*?\\\)",              # \( ... \)
        r"\$[^\d$][^$]*?\$",              # inline math $...$ (exclude $50)
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


def clean_formulas(markdown_text: str) -> str:
    """Fix known MinerU formula extraction corruption patterns in markdown."""
    text = markdown_text

    # ---- Fix 0: Normalize whitespace around math delimiters (preparatory) ----
    text = re.sub(r"\$\s+(.+?)\s+\$", r"$\1$", text)
    text = re.sub(r"\$\$\s+(.+?)\s+\$\$", r"$$\1$$", text)
    text = re.sub(r"\\\[\s+(.+?)\s+\\\]", r"\\[\1\\]", text)
    text = re.sub(r"\\\(\s+(.+?)\s+\\\)", r"\\(\1\\)", text)

    # ---- Fix 1: Wrap bare LaTeX math environments with $$...$$ ----
    BARE_ENV_NAMES = (
        r"array|aligned|align|alignat|gathered|split|cases"
        r"|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix"
    )
    BARE_ENV_RE = re.compile(
        r"(?<!\$)(\\begin\{(" + BARE_ENV_NAMES + r")\}[\s\S]*?\\end\{\2\})(?!\$)"
    )
    text = BARE_ENV_RE.sub(r"$$\n\1\n$$", text)

    # ---- Fix 2: Unwrap single-cell array environments ----
    SINGLE_CELL_ARRAY_RE = re.compile(
        r"\$\$\s*\\begin\{array\}\s*\{\s*([rcl])\s*\}\s*\n?\s*"
        r"([^\\&]+?)"
        r"\s*\n?\s*\\end\{array\}\s*\$\$"
    )

    def _unwrap_array(match: re.Match[str]) -> str:
        content = match.group(2).strip()
        if "\\\\" not in content and "&" not in content:
            return f"$$\n{content}\n$$"
        return match.group(0)

    text = SINGLE_CELL_ARRAY_RE.sub(_unwrap_array, text)

    # ---- Fix 3: Repair \sum \bigl(...\bigr) subscript errors ----
    BIG_OPERATORS = (
        r"\\sum|\\prod|\\coprod|\\bigcup|\\bigcap"
        r"|\\bigvee|\\bigwedge|\\bigoplus|\\bigotimes"
        r"|\\biguplus|\\bigsqcup|\\int|\\oint|\\iint|\\iiint"
    )
    BIGL_SUBSCRIPT_RE = re.compile(
        r"(" + BIG_OPERATORS + r")"
        r"\s*\\bigl\s*([\(\[\{])\s*"
        r"(.+?)"
        r"\s*\\bigr\s*([\)\]\}])"
    )
    text = BIGL_SUBSCRIPT_RE.sub(r"\1_{\2\3\4}", text)

    BIGL_VBAR_RE = re.compile(
        r"(" + BIG_OPERATORS + r")"
        r"\s*\\bigl\s*\|\s*"
        r"(.+?)"
        r"\s*\\bigr\s*\|"
    )
    text = BIGL_VBAR_RE.sub(r"\1_{|\2|}", text)

    # ---- Fix 4: Remove \cal{X} hallucination ----
    text = re.sub(r"\\cal\s*\{\s*([A-Za-z])\s*\}", r"\1", text)

    # ---- Fix 5: Remove \stackrel{\cdot}{\in} hallucination ----
    text = re.sub(
        r"\\stackrel\s*\{\s*\\cdot\s*\}\s*\{\s*\\in\s*\}",
        r"\\in",
        text,
    )

    # ---- Fix 6: Remove stray dash in subscript ----
    text = re.sub(
        r"(\w)\s*_\s*\{\s*-\s*\}\s*(\w[\w\s]*)",
        r"\1_{\2}",
        text,
    )
    text = re.sub(r"(\w)\s*_\s*\{\s*-\s*\}", r"\1", text)

    # ---- Fix 7: Strip \mathrm{punctuation} artifacts ----
    text = re.sub(
        r"\{\s*\\mathrm\s*\{\s*([:;,.!?])\s*\}\s*\}",
        r"\1",
        text,
    )
    text = re.sub(
        r"\\mathrm\s*\{\s*([:;,.!?])\s*\}",
        r"\1",
        text,
    )

    # ---- Fix 8: Collapse excessive LaTeX whitespace (applied LAST) ----
    def _compress_subscript(match: re.Match[str]) -> str:
        base = match.group(1)
        content = match.group(2).strip()
        content = re.sub(r"(\b\w)\s+(?=\w\b)", r"\1", content)
        return f"{base}_{{{content}}}"

    text = re.sub(
        r"([a-zA-Z0-9\\])\s*_\s*\{\s*([^}]+?)\s*\}",
        _compress_subscript,
        text,
    )

    def _compress_superscript(match: re.Match[str]) -> str:
        base = match.group(1)
        content = match.group(2).strip()
        content = re.sub(r"(\b\w)\s+(?=\w\b)", r"\1", content)
        return f"{base}^{{{content}}}"

    text = re.sub(
        r"([a-zA-Z0-9\\])\s*\^\s*\{\s*([^}]+?)\s*\}",
        _compress_superscript,
        text,
    )

    text = re.sub(r"([a-zA-Z0-9\\])\s*_\s*(\w)", r"\1_{\2}", text)
    text = re.sub(r"([a-zA-Z0-9\\])\s*\^\s*(\w)", r"\1^{\2}", text)

    return text


def translate_markdown(markdown_path: Path, llm: LlmConfig, target_language: str, max_chars: int, temperature: float, no_strip_headers: bool = False) -> str:
    source = markdown_path.read_text(encoding="utf-8")
    if no_strip_headers:
        filtered_source = source
    else:
        filtered_source = strip_headers_footers(source)
        removed = len(source.splitlines()) - len(filtered_source.splitlines())
        if removed:
            log(f"  Stripped {removed} header/footer lines")
    cleaned_source = clean_formulas(filtered_source)
    protected_text, placeholders = protect_segments(cleaned_source)
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
  <script src="https://cdn.bootcdn.net/ajax/libs/mathjax/3.2.2/es5/tex-svg.js"></script>
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
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["pkill", "-f", "msedge|chrome|chromium"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        cmd = [
            browser_path,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=60000",
            f"--print-to-pdf={out_pdf.resolve()}",
            "--print-to-pdf-no-header",
            str(html_path.resolve()),
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode != 0:
            raise PipelineError(f"Browser PDF render failed (exit code {result.returncode})")
    finally:
        # Keep HTML for debugging when small PDF
        if html_path.exists() and out_pdf.exists() and out_pdf.stat().st_size < 500000:
            log(f"  Small PDF ({out_pdf.stat().st_size} bytes), keeping debug HTML: {html_path}")
        elif html_path.exists():
            html_path.unlink()


# ---- HTML table → markdown pipe table converter -----------------------------
# MinerU outputs HTML <table> blocks, which pandoc's markdown reader silently
# drops (even with +raw_html).  Convert them to pipe tables beforehand.

_HTML_TABLE_RE = re.compile(r"<table[\s>][\s\S]*?</table>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class _HtmlTableToGrid(html.parser.HTMLParser):
    """Parse an HTML <table> into a 2-D grid, expanding colspan."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: str = ""
        self._in_cell = False
        self._colspan_stack: list[int] = []   # remaining colspan to fill after cell
        self._cell_accum: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "tr":
            self._current_row = []
            self._colspan_stack = []
        elif tag_lower in ("td", "th"):
            self._in_cell = True
            self._current_cell = ""
            self._cell_accum = []
            cs = 1
            for k, v in attrs:
                if k == "colspan" and v:
                    try:
                        cs = int(v)
                    except ValueError:
                        cs = 1
            self._colspan_stack.append(cs)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("td", "th"):
            self._in_cell = False
            cell_text = "".join(self._cell_accum).strip()
            cs = self._colspan_stack.pop(0) if self._colspan_stack else 1
            for _ in range(cs):
                self._current_row.append(cell_text)
        elif tag_lower == "tr":
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_accum.append(data)

    def error(self, message: str) -> None:
        pass  # tolerate malformed HTML


def _convert_html_tables_to_pipe(markdown_text: str) -> str:
    """Replace every HTML <table>...</table> with a markdown pipe table."""
    parts = _HTML_TABLE_RE.split(markdown_text)
    tables = _HTML_TABLE_RE.findall(markdown_text)

    if not tables:
        return markdown_text

    result: list[str] = []
    for idx, part in enumerate(parts):
        result.append(part)
        if idx >= len(tables):
            break

        parser = _HtmlTableToGrid()
        parser.feed(tables[idx])
        parser.close()
        rows = parser.rows
        if not rows or not any(any(cell for cell in row) for row in rows):
            result.append("\n")
            continue

        # Normalise column count
        col_count = max((len(row) for row in rows), default=1)
        norm_rows: list[list[str]] = []
        for row in rows:
            norm = list(row)
            while len(norm) < col_count:
                norm.append("")
            norm_rows.append(norm[:col_count])

        # If first row is a full-width title (all cells identical), extract it
        # as a preceding paragraph so it doesn't inflate the pipe table.
        table_title = ""
        if norm_rows and col_count >= 2:
            first = norm_rows[0]
            if all(c == first[0] for c in first) and first[0]:
                table_title = first[0]
                norm_rows.pop(0)

        if not norm_rows:
            if table_title:
                result.append(f"\n**{table_title}**\n\n")
            continue

        # ---- Merge continuation rows (MinerU splits cells at PDF line-breaks) ----
        # A row is a "continuation" when most of its cells are empty — the
        # non-empty cells complete the content of the row above.
        header = norm_rows[0]
        data_rows: list[list[str]] = []
        for row in norm_rows[1:]:
            filled = sum(1 for c in row if c)
            # If this is a sparse row *and* there is a previous row to merge into,
            # append each non-empty cell to the same column of the previous row.
            if filled <= col_count // 2 and data_rows:
                prev = data_rows[-1]
                for ci in range(col_count):
                    if row[ci]:
                        prev[ci] = (prev[ci] + " " + row[ci]).strip()
            else:
                data_rows.append(list(row))

        # Build pipe table
        lines: list[str] = []
        # Header row
        lines.append("| " + " | ".join(_clean_cell(h) for h in header) + " |")
        # Separator
        lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
        # Data rows (merged)
        for row in data_rows:
            lines.append("| " + " | ".join(_clean_cell(c) for c in row) + " |")

        # Pandoc requires a blank line before a pipe table.  Ensure \n\n
        # precedes every opening | and separate the optional title.
        table_block = "\n".join(lines)
        if table_title:
            result.append(f"\n\n**{table_title}**\n\n{table_block}\n\n")
        else:
            result.append(f"\n\n{table_block}\n\n")

    return "".join(result)


def _clean_cell(text: str) -> str:
    """Strip HTML tags and escape pipe characters for markdown table cells."""
    cleaned = _HTML_TAG_RE.sub("", text)
    cleaned = cleaned.replace("|", "\\|")
    cleaned = cleaned.replace("\n", " ")
    return cleaned


def render_docx(markdown_text: str, asset_base_dir: Path, out_docx: Path, title: str, pandoc_path: str, skill_dir: Path, reference_docx: str | None = None) -> None:
    """Convert translated Markdown to DOCX via pandoc, with native OMML formula rendering.

    Automatically generates/uses a reference docx with Times New Roman + Microsoft YaHei
    fonts (11pt), unless an explicit *reference_docx* path is provided.
    """
    ref = reference_docx
    if ref is None:
        generated = ensure_reference_docx(pandoc_path, skill_dir)
        ref = str(generated) if generated else None

    # Pre-process: convert HTML tables to markdown pipe tables for pandoc
    html_table_count = len(_HTML_TABLE_RE.findall(markdown_text))
    markdown_text = _convert_html_tables_to_pipe(markdown_text)
    log(f"  DOCX preprocess: {html_table_count} HTML tables → pipe tables")

    tmp_md = asset_base_dir / "_translated_docx.md"
    tmp_md.write_text(markdown_text, encoding="utf-8")
    try:
        cmd = [
            pandoc_path, str(tmp_md),
            "--from", "markdown+raw_html+tex_math_dollars",
            "--to", "docx",
            "--resource-path", str(asset_base_dir),
            "--output", str(out_docx.resolve()),
        ]
        if ref:
            cmd.extend(["--reference-doc", ref])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise PipelineError(f"Pandoc conversion failed (exit {result.returncode}): {result.stderr}")
    finally:
        if tmp_md.exists():
            tmp_md.unlink()


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
    zip_url = mineru_batch_upload(pdf_path, runtime.mineru_token, args.source_language, args.ocr)
    if not zip_url:
        raise PipelineError("MinerU batch upload did not return a ZIP URL")

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

    if args.output_docx:
        out_docx = output_dir / f"{pdf_path.stem}_{args.target_suffix}.docx"
        log(f"  Rendering Word document: {out_docx.name}")
        pandoc_path = ensure_pandoc(auto_install=not args.no_auto_install)
        render_docx(translated_markdown, markdown_path.parent, out_docx, pdf_path.stem, pandoc_path, SKILL_DIR)

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
    parser.add_argument("--output-docx", action="store_true", dest="output_docx", help="Also output translated Word document (.docx) alongside PDF.")
    parser.add_argument("--no-auto-install", action="store_true", help="Do not auto-install Python packages if missing.")
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
    pandoc_path = find_pandoc()
    log(f"pandoc: {pandoc_path if pandoc_path else 'missing (required for --output-docx)'}")
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
