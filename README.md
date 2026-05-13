# Claude Code PDF Translator

基于 MinerU + OpenAI 兼容 LLM + 无头浏览器的 PDF 批量翻译工具，专为 VS Code 和 Claude Code 工作流设计。

保留原文结构、图片、表格、公式和 Markdown 排版，输出可读性高的翻译后 PDF。

## 前置条件

- **Python 3.8+**（仅需标准库，`markdown` 包会自动安装）
- **Edge / Chrome / Chromium** 浏览器（用于渲染最终 PDF，脚本会自动检测）
- **MinerU API token**（[mineru.net](https://mineru.net) 注册获取）
- **OpenAI 兼容 LLM API**（OpenAI、Ollama、vLLM 等任何兼容 `/v1/chat/completions` 的服务）

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

在 PDF 所在目录创建配置文件：

**方式一：环境变量**

```bash
export MINERU_API_TOKEN="你的token"
export PDF_TRANSLATE_LLM_BASE_URL="https://api.openai.com"
export PDF_TRANSLATE_LLM_API_KEY="你的key"
export PDF_TRANSLATE_MODEL="gpt-4o-mini"
```

**方式二：本地文件**

在 PDF 目录下创建两个文件：

- `mineru密钥.txt`：写入 MinerU token
- `翻译大模型url以及key.txt`：第一行 LLM base URL，第二行 API key

**方式三：.env 文件**（参考 `assets/env.example`）

### 2. 检查环境

```bash
python scripts/pdf_translate.py --workdir <PDF所在目录> --check
```

### 3. 翻译 PDF

```bash
python scripts/pdf_translate.py --workdir <PDF所在目录>
```

翻译后的 PDF 默认输出到 `translated/` 子目录。

## 常用命令

```bash
# 翻译当前目录所有 PDF 为简体中文
python scripts/pdf_translate.py --workdir .

# 强制重新生成已有输出
python scripts/pdf_translate.py --workdir . --force

# 翻译为日语并自定义后缀
python scripts/pdf_translate.py --workdir . --target-language "Japanese" --target-suffix ja

# OCR 模式处理扫描件
python scripts/pdf_translate.py --workdir . --ocr

# 保留翻译后的 Markdown 文件
python scripts/pdf_translate.py --workdir . --keep-markdown
```

## 工作流程

```
PDF 文件 → tmpfiles.org 上传 → MinerU 内容提取 → LLM 翻译 → 无头浏览器渲染 → 翻译后 PDF
```

> **注意**：文档会被上传至外部服务（tmpfiles.org 和 MinerU），处理敏感文档前请确认合规性。

## 技能文件结构

```
mineru-pdf-vscode/
├── SKILL.md                        # Claude Code 技能定义
├── scripts/
│   └── pdf_translate.py            # 核心翻译脚本
├── references/
│   ├── installation.md             # 详细安装指南
│   └── troubleshooting.md          # 故障排查
├── assets/
│   ├── env.example                 # 环境变量模板
│   └── vscode/
│       └── tasks.json              # VS Code 任务配置
└── agents/
    └── openai.yaml                 # Agent 接口定义
```

## 故障排查

详细说明见 [references/troubleshooting.md](mineru-pdf-vscode/references/troubleshooting.md)。

常见问题：

- **浏览器未检测到**：设置 `PDF_TRANSLATE_BROWSER` 环境变量指向浏览器可执行文件路径
- **LLM 请求失败**：确认 base URL 不包含 `/v1` 后缀，脚本会自动拼接 `/v1/chat/completions`
- **图片缺失**：运行 `--keep-temp --keep-markdown --force` 检查 MinerU 提取的图片路径
- **公式渲染异常**：确认网络可访问 jsDelivr CDN（MathJax 依赖）

## 许可证

MIT
