---
name: mineru-pdf-vscode
description: translate local pdf papers and technical documents in vs code or claude code by using mineru online extraction, an openai-compatible llm, and headless edge/chrome/chromium rendering. use when the user asks to batch translate pdfs, produce final translated pdf files, configure mineru/llm credentials, troubleshoot the translation pipeline, or set up a claude code skill for pdf translation.
---

# MinerU PDF translation for VS Code + Claude Code

Use this skill to help the user translate local PDF papers or technical documents into final translated PDFs while preserving structure, images, tables, formulas, and Markdown-derived layout as much as possible.

The bundled implementation is `scripts/pdf_translate.py`. It sends source PDFs to an upload endpoint, MinerU, and the configured LLM provider, then renders final PDFs locally with Edge/Chrome/Chromium. Treat this as an external-data workflow: before running on sensitive PDFs, make sure the user understands the documents leave the local machine.

## Standard workflow

1. Identify the working folder that contains the source PDFs. Prefer the current VS Code workspace or the folder named by the user.
2. Check prerequisites before translating:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir <pdf-folder> --check
   ```
3. Configure credentials without exposing secrets in chat. Prefer local files in the PDF folder or environment variables.
4. Run the translator from Claude Code:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir <pdf-folder>
   ```
5. Review `translated/`. If `translated/failures.json` exists, read it and explain the failed PDFs and next fix.
6. Return the final output paths and a short operational summary. Do not paste API keys, tokens, or full extracted document text.

## Configuration

The script reads config in this order: command-line flags, local config files in `--workdir`, then environment variables / `.env`.

Local config files in the PDF folder:

- `mineru密钥.txt`: MinerU API token.
- `翻译大模型url以及key.txt`: line 1 is the OpenAI-compatible base URL, line 2 is the API key.
- `.env`: optional key/value config; see `assets/env.example`.

Environment variables:

```bash
MINERU_API_TOKEN="..."
PDF_TRANSLATE_LLM_BASE_URL="https://your-provider.example"
PDF_TRANSLATE_LLM_API_KEY="..."
PDF_TRANSLATE_MODEL="gpt-4o-mini"
PDF_TRANSLATE_BROWSER="/path/to/chrome-or-edge"
```

## Common commands

Translate all top-level PDFs in the current folder to Simplified Chinese:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir .
```

Force regeneration of existing outputs:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --force
```

Translate into another language and use a custom suffix:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --target-language "Japanese" --target-suffix ja
```

Keep translated Markdown for inspection:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --keep-markdown
```

Use OCR mode for scanned PDFs:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --ocr
```

Use explicit credentials only when the user requests it. Avoid echoing secrets in terminal output:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" \
  --workdir . \
  --mineru-token "$MINERU_API_TOKEN" \
  --llm-base-url "$PDF_TRANSLATE_LLM_BASE_URL" \
  --llm-api-key "$PDF_TRANSLATE_LLM_API_KEY" \
  --llm-model "$PDF_TRANSLATE_MODEL"
```

## VS Code setup guidance

For project-level use, install the skill at:

```text
<project>/.claude/skills/mineru-pdf-vscode/
```

For personal use across projects, install at:

```text
~/.claude/skills/mineru-pdf-vscode/
```

If the user wants a VS Code task, copy `assets/vscode/tasks.json` into the project `.vscode/tasks.json` and adjust `MINERU_PDF_SKILL_SCRIPT` or the prompted script path.

For complete setup and install options, read `references/installation.md`.

## Troubleshooting rules

- If `--check` reports a missing browser, help the user install Edge/Chrome/Chromium or set `PDF_TRANSLATE_BROWSER`.
- If MinerU task creation or polling fails, verify token validity, upload URL accessibility, and source PDF size.
- If LLM translation fails, verify the base URL ends before `/v1`, the API key is valid, and the selected model supports chat completions.
- If formulas or images are broken, rerun with `--keep-temp --keep-markdown`, inspect the MinerU `full.md`, and compare image relative paths.
- If output already exists, use `--force` to rebuild.

For detailed fixes, read `references/troubleshooting.md`.
