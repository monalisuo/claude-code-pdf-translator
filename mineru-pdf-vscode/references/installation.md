# 安装与 VS Code 设置

## 安装为 Claude Code 项目技能

在仓库根目录或 VS Code 工作区目录：

```bash
mkdir -p .claude/skills/mineru-pdf-vscode
# 将本技能文件夹内容复制到 .claude/skills/mineru-pdf-vscode
```

如果会话启动时 `.claude/skills` 目录尚不存在，重启 Claude Code 即可。如果 Claude Code 已在监听该目录，修改通常会被实时获取。

## 安装为个人技能（所有项目可用）

macOS / Linux：

```bash
mkdir -p ~/.claude/skills/mineru-pdf-vscode
# 将本技能文件夹内容复制到 ~/.claude/skills/mineru-pdf-vscode
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills\mineru-pdf-vscode"
# 将本技能文件夹内容复制到 $HOME\.claude\skills\mineru-pdf-vscode
```

## 从 VS Code 使用

1. 在 VS Code 中打开 PDF 所在文件夹或父项目。
2. 在该工作区启动 Claude Code。
3. 让 Claude Code 翻译文件夹中的 PDF，如果 Claude Code 版本支持斜杠命令，可直接调用技能：
   ```text
   /mineru-pdf-vscode 翻译 ./papers 中的 PDF
   ```
4. 脚本默认将最终 PDF 输出到 `translated/` 目录。

## 推荐：技能目录凭据配置

安装完成后，在技能目录创建 `.env` 一次配置凭据，之后所有项目自动生效，无需重复配置：

```bash
cp ~/.claude/skills/mineru-pdf-vscode/assets/env.example ~/.claude/skills/mineru-pdf-vscode/.env
# 编辑 .env 填入真实凭据
```

```ini
# ~/.claude/skills/mineru-pdf-vscode/.env
MINERU_API_TOKEN=你的MinerU_token
PDF_TRANSLATE_LLM_BASE_URL=https://api.deepseek.com
PDF_TRANSLATE_LLM_API_KEY=你的DeepSeek_API_Key
PDF_TRANSLATE_MODEL=deepseek-chat
```

凭据加载顺序：命令行参数 → PDF 目录文件 → 技能目录文件 → PDF 目录 `.env` → 技能目录 `.env` → 环境变量。

## 可选 VS Code 任务

将 `assets/vscode/tasks.json` 复制到项目的 `.vscode/tasks.json` 中，然后运行 **任务：运行任务** → **Translate PDFs with MinerU**。

如果任务找不到脚本，设置环境变量指向已安装的技能脚本：

macOS / Linux：

```bash
export MINERU_PDF_SKILL_SCRIPT="$HOME/.claude/skills/mineru-pdf-vscode/scripts/pdf_translate.py"
```

Windows PowerShell：

```powershell
$env:MINERU_PDF_SKILL_SCRIPT="$HOME\.claude\skills\mineru-pdf-vscode\scripts\pdf_translate.py"
```

## 凭据文件说明

以下文件可放在 PDF 工作目录或技能目录中，用于覆盖默认凭据：

```text
mineru密钥.txt                   # 单行 MinerU API token
翻译大模型url以及key.txt           # 第一行 LLM base URL，第二行 API key
.env                             # 键值配置（参考 assets/env.example）
```

请将所有凭据文件加入 `.gitignore`，切勿提交包含真实密钥的文件。
