# Troubleshooting

## `--check` says browser is missing

Install Microsoft Edge, Google Chrome, or Chromium. Then rerun:

```bash
python scripts/pdf_translate.py --workdir . --check
```

If the browser is installed but not detected, set:

```bash
PDF_TRANSLATE_BROWSER="/absolute/path/to/browser"
```

or pass:

```bash
--browser-path "/absolute/path/to/browser"
```

## MinerU upload or parsing fails

Check these in order:

1. The source PDF is not password-protected.
2. The temporary upload service returns a public URL that MinerU can fetch.
3. `MINERU_API_TOKEN` or `mineru密钥.txt` contains the active MinerU token.
4. Very large PDFs may need to be split before translation.
5. For scanned documents, rerun with `--ocr`.

## LLM request fails

Check these in order:

1. `PDF_TRANSLATE_LLM_BASE_URL` should usually be the provider root, not the full chat completions path. The script appends `/v1/chat/completions`.
2. The model named by `PDF_TRANSLATE_MODEL` or `--llm-model` must support OpenAI-compatible chat completions.
3. The API key must have enough quota for long document translation.
4. Reduce chunk size if a provider has a smaller context window:
   ```bash
   python scripts/pdf_translate.py --workdir . --max-chars 6000
   ```

## Images missing in final PDF

Run with:

```bash
python scripts/pdf_translate.py --workdir . --keep-temp --keep-markdown --force
```

Inspect the MinerU extraction folder under `.pdf_translate_tmp/<pdf-name>/mineru/` and verify that Markdown image paths point to existing files. If MinerU changed the output layout, update image paths in the translated Markdown before rendering.

## Formulas render poorly

The HTML renderer loads MathJax from jsDelivr. If the machine cannot access the CDN, formulas may not render before print. Use a network that can reach the CDN or customize the HTML template in `scripts/pdf_translate.py` to point to a local MathJax bundle.

## Final PDF is skipped

The script skips outputs that already exist. Add `--force` to rebuild.

## `failures.json` exists

Open `translated/failures.json`, identify per-file errors, fix the root cause, then rerun with `--force` for only the affected files after moving unrelated PDFs out of the top-level workdir or translating them in a separate folder.
