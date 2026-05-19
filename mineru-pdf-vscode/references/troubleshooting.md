# 故障排查

## `--check` 报告浏览器缺失

安装 Microsoft Edge、Google Chrome 或 Chromium，然后重新运行：

```bash
python scripts/pdf_translate.py --workdir . --check
```

如果浏览器已安装但未被检测到，手动设置：

```bash
PDF_TRANSLATE_BROWSER="/absolute/path/to/browser"
```

或通过命令行参数：

```bash
--browser-path "/absolute/path/to/browser"
```

Windows 上常见路径：
- `C:\Program Files\Microsoft\Edge\Application\msedge.exe`
- `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- `C:\Program Files\Google\Chrome\Application\chrome.exe`

## MinerU 上传或解析失败

按以下顺序排查：

1. 源 PDF 是否被密码保护。
2. 临时上传服务是否返回 MinerU 可拉取的公开 URL。
3. `MINERU_API_TOKEN` 或 `mineru密钥.txt` 是否包含有效的 MinerU token。检查是否已在技能目录（`~/.claude/skills/mineru-pdf-vscode/`）下配置。
4. 大型 PDF 可能需要在翻译前先拆分。
5. 扫描件请使用 `--ocr` 重新运行。

## LLM 请求失败

按以下顺序排查：

1. `PDF_TRANSLATE_LLM_BASE_URL` 应为服务商根路径，不要包含 `/v1` 后缀。脚本会自动拼接 `/v1/chat/completions`。
   - DeepSeek：`https://api.deepseek.com`
   - OpenAI：`https://api.openai.com`
   - Ollama：`http://localhost:11434`
2. `PDF_TRANSLATE_MODEL` 或 `--llm-model` 指定的模型必须支持 OpenAI 兼容的 chat completions。
   - DeepSeek：`deepseek-chat`
   - OpenAI：`gpt-4o-mini`、`gpt-4o` 等
3. API key 配额是否充足，长文档翻译消耗较大。
4. 如果服务商上下文窗口较小，可减小分块大小：
   ```bash
   python scripts/pdf_translate.py --workdir . --max-chars 6000
   ```
5. 确认凭据配置正确。可运行 `--check` 验证：
   ```bash
   python scripts/pdf_translate.py --workdir . --check
   ```

## 凭据未被找到

脚本按以下顺序查找凭据：
1. 命令行参数（`--mineru-token`、`--llm-base-url`、`--llm-api-key`）
2. PDF 工作目录下的 `mineru密钥.txt` 和 `翻译大模型url以及key.txt`
3. 技能目录下的同名文件
4. PDF 工作目录下的 `.env`
5. 技能目录（`~/.claude/skills/mineru-pdf-vscode/`）下的 `.env`
6. 系统环境变量

推荐将凭据放在技能目录下的 `.env` 中，一次配置永久生效。

## 最终 PDF 中图片缺失

使用以下命令重新运行：

```bash
python scripts/pdf_translate.py --workdir . --keep-temp --keep-markdown --force
```

检查 MinerU 提取目录（`.pdf_translate_tmp/<pdf名称>/mineru/`），确认 Markdown 中的图片路径指向存在的文件。如果 MinerU 输出布局发生变化，在渲染前更新译文 Markdown 中的图片路径。

## 公式渲染异常

### PDF 中公式渲染异常

HTML 渲染器通过 jsDelivr CDN 加载 MathJax 3。如果机器无法访问 CDN，公式可能在打印前无法渲染。请使用可访问 CDN 的网络环境，或修改 `scripts/pdf_translate.py` 中的 HTML 模板，将 MathJax 指向本地文件。

### DOCX 中公式显示为原始 LaTeX

确认 Pandoc 已安装且版本足够新（≥ 3.0）：

```bash
pandoc --version
```

如果未安装：
- Windows：`winget install pandoc`
- macOS：`brew install pandoc`
- Linux：`sudo apt install pandoc`

Pandoc 会自动将 `$...$` 和 `$$...$$` 转换为 Word 原生 OMML 公式（可双击编辑）。

## DOCX 生成失败

按以下顺序排查：

1. Pandoc 是否已安装（`pandoc --version`）。
2. 磁盘空间是否充足。
3. 图片路径是否正确 —— 脚本将临时 markdown 写入 MinerU 提取目录以确保相对路径可解析。
4. 若 `assets/reference.docx` 损坏，删除后脚本会在下次运行时自动重新生成。

## DOCX 样式不符合预期

脚本首次运行 `--output-docx` 时自动在 `assets/reference.docx` 生成样式模板（正文：Times New Roman + Microsoft YaHei 11pt，代码：Consolas）。用 Word 打开该文件修改样式后保存，后续生成的 DOCX 即使用新样式。



## 最终 PDF 被跳过

脚本默认跳过已存在的输出文件。添加 `--force` 强制重新生成：

```bash
python scripts/pdf_translate.py --workdir . --force
```

## `failures.json` 存在

打开 `translated/failures.json`，逐一确认文件错误信息，修复根本原因后，将无关 PDF 移出顶层工作目录或在单独文件夹中翻译受影响的文件，然后使用 `--force` 重新运行。
