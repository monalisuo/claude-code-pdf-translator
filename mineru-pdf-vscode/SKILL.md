---
name: mineru-pdf-vscode
description: 使用 MinerU 在线提取、OpenAI 兼容大模型（DeepSeek、OpenAI 等）和无头 Edge/Chrome/Chromium 浏览器，在 VS Code 或 Claude Code 中批量翻译本地 PDF 论文和技术文档。当用户需要批量翻译 PDF、生成翻译后的 PDF 文件、配置 MinerU/LLM 凭据、排查翻译流程问题，或设置 Claude Code PDF 翻译技能时使用。
---

# MinerU PDF 翻译 — VS Code + Claude Code 技能

使用此技能帮助用户将本地 PDF 论文或技术文档翻译为排版完整的译文 PDF，尽可能保留原文结构、图片、表格、公式和 Markdown 排版。

核心实现为 `scripts/pdf_translate.py`。它将源 PDF 发送至上传服务、MinerU 和所配置的 LLM，然后使用 Edge/Chrome/Chromium 无头浏览器在本地渲染最终 PDF。这是外部数据处理流程：处理敏感 PDF 前，请确保用户了解文档会离开本地机器。

## 标准流程

1. 确定包含源 PDF 的工作目录。优先使用当前 VS Code 工作区或用户指定的目录。
2. 翻译前检查环境：
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir <PDF目录> --check
   ```
3. 配置凭据。推荐将凭据放在技能目录下，一次配置永久生效，避免在对话中暴露密钥。
4. 从 Claude Code 运行翻译：
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir <PDF目录>
   ```
5. 检查 `translated/` 输出目录。如果存在 `translated/failures.json`，读取并解释失败原因和修复方案。
6. 返回最终输出路径和简要操作摘要。禁止粘贴 API 密钥、token 或提取的文档全文。

## 配置

脚本按以下顺序读取配置：命令行参数 → PDF 目录本地文件 → 技能目录本地文件 → PDF 目录 `.env` → 技能目录 `.env` → 环境变量。

### 技能目录配置（推荐，一次配置永久生效）

在技能目录下放置 `.env` 文件：

```bash
# ~/.claude/skills/mineru-pdf-vscode/.env
MINERU_API_TOKEN="..."
PDF_TRANSLATE_LLM_BASE_URL="https://api.deepseek.com"
PDF_TRANSLATE_LLM_API_KEY="..."
PDF_TRANSLATE_MODEL="deepseek-chat"
```

也可在技能目录放置：

- `mineru密钥.txt`：MinerU API token。
- `翻译大模型url以及key.txt`：第一行为 OpenAI 兼容 base URL，第二行为 API key。

### PDF 目录本地配置

适合项目级覆盖：

- `mineru密钥.txt`：MinerU API token。
- `翻译大模型url以及key.txt`：第一行为 OpenAI 兼容 base URL，第二行为 API key。
- `.env`：键值配置，参考 `assets/env.example`。

### 环境变量

```bash
MINERU_API_TOKEN="..."
PDF_TRANSLATE_LLM_BASE_URL="https://api.deepseek.com"
PDF_TRANSLATE_LLM_API_KEY="..."
PDF_TRANSLATE_MODEL="deepseek-chat"
PDF_TRANSLATE_BROWSER="/path/to/chrome-or-edge"
```

## 常用命令

翻译当前目录下所有顶层 PDF 为简体中文：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir .
```

强制重新生成已有输出：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --force
```

翻译为其他语言并自定义后缀：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --target-language "Japanese" --target-suffix ja
```

保留翻译后的 Markdown 用于检查：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --keep-markdown
```

OCR 模式处理扫描件：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" --workdir . --ocr
```

仅在用户明确要求时使用命令行传递凭据，避免在终端输出中回显密钥：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/pdf_translate.py" \
  --workdir . \
  --mineru-token "$MINERU_API_TOKEN" \
  --llm-base-url "$PDF_TRANSLATE_LLM_BASE_URL" \
  --llm-api-key "$PDF_TRANSLATE_LLM_API_KEY" \
  --llm-model "$PDF_TRANSLATE_MODEL"
```

## VS Code 设置指南

项目级安装：

```text
<project>/.claude/skills/mineru-pdf-vscode/
```

跨项目个人安装：

```text
~/.claude/skills/mineru-pdf-vscode/
```

如需 VS Code 任务，将 `assets/vscode/tasks.json` 复制到项目 `.vscode/tasks.json` 并调整 `MINERU_PDF_SKILL_SCRIPT` 或脚本路径。

完整安装选项见 `references/installation.md`。

## 支持的大模型

- **DeepSeek V4 Pro**（`deepseek-chat`，`api.deepseek.com`）
- **OpenAI**（`gpt-4o-mini`、`gpt-4o` 等，`api.openai.com`）
- 任何兼容 `/v1/chat/completions` 的服务（Ollama、vLLM 等）

切换模型只需修改 `PDF_TRANSLATE_MODEL` 和 `PDF_TRANSLATE_LLM_BASE_URL`。

## 故障排查规则

- 如果 `--check` 报告缺少浏览器，帮助用户安装 Edge/Chrome/Chromium 或设置 `PDF_TRANSLATE_BROWSER`。
- 如果 MinerU 任务创建或轮询失败，验证 token 有效性、上传 URL 可访问性和源 PDF 大小。
- 如果 LLM 翻译失败，验证 base URL 末尾不含 `/v1`（脚本自动拼接）、API key 有效、所选模型支持 chat completions。
- 如果公式或图片损坏，使用 `--keep-temp --keep-markdown` 重新运行，检查 MinerU `full.md` 并对比图片相对路径。
- 如果输出已存在，使用 `--force` 重新生成。
- 凭据查找失败时，检查是否在技能目录（`~/.claude/skills/mineru-pdf-vscode/`）下放置了 `.env` 文件。

详细修复方案见 `references/troubleshooting.md`。
