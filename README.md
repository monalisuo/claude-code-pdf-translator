# Claude Code PDF Translator

基于 MinerU + OpenAI 兼容 LLM + 无头浏览器的 PDF 批量翻译工具，专为 VS Code 和 Claude Code 工作流设计。保留原文结构、图片、表格、公式和 Markdown 排版，输出排版完整的翻译后 PDF，可选输出带有可编辑 OMML 公式的 Word 文档。

## 前置条件

- **Python 3.8+**（仅需标准库，`markdown` 包会自动安装）
- **Edge / Chrome / Chromium** 浏览器（用于渲染 PDF，脚本自动检测）
- **Pandoc**（可选，用于 DOCX 输出；`winget install pandoc` / `brew install pandoc`）
- **MinerU API token**（[mineru.net](https://mineru.net) 注册获取）
- **OpenAI 兼容 LLM API**（DeepSeek、OpenAI、Ollama、vLLM 等）

## 安装为 Claude Code 技能

**项目级安装**（仅当前项目可用）：

```bash
mkdir -p .claude/skills/mineru-pdf-vscode
cp -r mineru-pdf-vscode/* .claude/skills/mineru-pdf-vscode/
```

**用户级安装**（所有项目可用）：

```bash
# macOS / Linux
mkdir -p ~/.claude/skills/mineru-pdf-vscode
cp -r mineru-pdf-vscode/* ~/.claude/skills/mineru-pdf-vscode/

# Windows PowerShell
New-Item -ItemType Directory -Force "$HOME\.claude\skills\mineru-pdf-vscode"
Copy-Item -Recurse mineru-pdf-vscode\* "$HOME\.claude\skills\mineru-pdf-vscode\"
```

## 快速开始

### 1. 配置凭据

**方式一：技能目录 `.env`（推荐）**

在技能目录下创建 `.env` 文件，之后所有项目自动生效：

```ini
# ~/.claude/skills/mineru-pdf-vscode/.env
MINERU_API_TOKEN=你的MinerU_token
PDF_TRANSLATE_LLM_BASE_URL=https://api.deepseek.com
PDF_TRANSLATE_LLM_API_KEY=你的API_key
PDF_TRANSLATE_MODEL=deepseek-chat
```

**方式二：环境变量**

```bash
export MINERU_API_TOKEN="你的token"
export PDF_TRANSLATE_LLM_BASE_URL="https://api.deepseek.com"
export PDF_TRANSLATE_LLM_API_KEY="你的key"
export PDF_TRANSLATE_MODEL="deepseek-chat"
```

**方式三：PDF 目录本地文件**

在 PDF 目录下创建：
- `mineru密钥.txt`：MinerU token
- `翻译大模型url以及key.txt`：第一行 LLM base URL，第二行 API key

> 凭据加载顺序：命令行参数 → PDF 目录文件 → 技能目录文件 → PDF 目录 `.env` → 技能目录 `.env` → 环境变量

### 2. 检查环境

```bash
python scripts/pdf_translate.py --workdir . --check
```

### 3. 翻译

```bash
python scripts/pdf_translate.py --workdir .
```

译文 PDF 输出到 `translated/` 子目录。

## 常用命令

```bash
# 翻译当前目录所有 PDF 为简体中文
python scripts/pdf_translate.py --workdir .

# 同时输出 Word 文档（公式可编辑）
python scripts/pdf_translate.py --workdir . --output-docx

# 强制重新生成已有输出
python scripts/pdf_translate.py --workdir . --force

# 翻译为日语
python scripts/pdf_translate.py --workdir . --target-language "Japanese" --target-suffix ja

# OCR 模式处理扫描件
python scripts/pdf_translate.py --workdir . --ocr

# 保留中间 Markdown 文件
python scripts/pdf_translate.py --workdir . --keep-markdown
```

## 管道架构

```
PDF → MinerU 批量 API（直传 OSS）→ ZIP 下载 → Markdown 提取
  → protect_segments（保护公式/代码/图片）→ LLM 分块翻译
  → restore_segments（还原保护内容）→ Chrome headless → 译文 PDF
  → （可选）Pandoc → DOCX（公式 OMML 渲染）
```

### 公式保护

翻译前自动将以下内容替换为占位符 `@@PROTECTED_N@@`，防止 LLM 破坏：
- 围栏代码块、行内代码
- `$...$` 行内公式、`$$...$$` / `\[...\]` 块级公式
- Markdown 图片

同时 `clean_formulas()` 在保护前修复 MinerU 提取的常见 LaTeX 工件（下标校正、空白环境包裹等）。

### DOCX 公式与样式

DOCX 输出通过 Pandoc 完成，公式自动转为 Word 原生 OMML 格式（可双击编辑）。首次使用 `--output-docx` 时自动生成 `assets/reference.docx` 样式模板：

| 元素 | 字体 | 字号 |
|---|---|---|
| 正文 | Times New Roman + Microsoft YaHei | 11pt |
| 标题 | Times New Roman + Microsoft YaHei | 16pt–10pt |
| 行内代码 | Consolas | 10pt |
| 代码块 | Consolas | 9pt |

用 Word 修改 `assets/reference.docx` 可自定义所有样式。

### 浏览器渲染

PDF 输出按 Chrome → Edge → Chromium 优先级选择浏览器（Edge headless 存在静默失败 bug）。渲染前在 Windows 上自动清理残留进程。

## 文件结构

```
mineru-pdf-vscode/
├── SKILL.md                        # Claude Code 技能定义
├── scripts/
│   └── pdf_translate.py            # 核心翻译脚本（单文件，~1100 行）
├── references/
│   ├── installation.md             # 详细安装指南
│   └── troubleshooting.md          # 故障排查
├── assets/
│   ├── env.example                 # 环境变量模板
│   ├── reference.docx              # （自动生成）Pandoc 样式模板
│   └── vscode/
│       └── tasks.json              # VS Code 任务配置
└── agents/
    ├── openai.yaml                 # OpenAI agent 接口
    └── deepseek.yaml               # DeepSeek agent 接口
```

## 故障排查

常见问题：
- **浏览器未检测到**：安装 Chrome 或设置 `PDF_TRANSLATE_BROWSER` 环境变量
- **LLM 请求失败**：确认 base URL 不含 `/v1` 后缀，脚本自动拼接 `/v1/chat/completions`
- **DOCX 生成失败**：确认 Pandoc 已安装（`pandoc --version`）；Windows：`winget install pandoc`
- **公式在 DOCX 中显示为原始 LaTeX**：升级到最新版本（已切换为 Pandoc 渲染）
- **图片缺失**：运行 `--keep-temp --keep-markdown --force` 检查 MinerU 提取路径

详细说明见 [references/troubleshooting.md](mineru-pdf-vscode/references/troubleshooting.md)。

## 许可证

MIT
