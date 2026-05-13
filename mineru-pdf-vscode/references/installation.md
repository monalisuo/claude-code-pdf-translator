# Installation and VS Code setup

## Install as a Claude Code project skill

From the repository root or VS Code workspace folder:

```bash
mkdir -p .claude/skills/mineru-pdf-vscode
# copy this skill folder's contents into .claude/skills/mineru-pdf-vscode
```

Restart Claude Code if the top-level `.claude/skills` folder did not exist when the session started. If Claude Code is already watching that folder, edits are usually picked up live.

## Install as a personal skill

macOS/Linux:

```bash
mkdir -p ~/.claude/skills/mineru-pdf-vscode
# copy this skill folder's contents into ~/.claude/skills/mineru-pdf-vscode
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills\mineru-pdf-vscode"
# copy this skill folder's contents into $HOME\.claude\skills\mineru-pdf-vscode
```

## Use from VS Code

1. Open the PDF folder or parent project in VS Code.
2. Start Claude Code in that workspace.
3. Ask Claude Code to translate PDFs in the folder, or invoke the skill directly if your Claude Code version supports slash invocation:
   ```text
   /mineru-pdf-vscode translate the PDFs in ./papers
   ```
4. The script writes final PDFs to `translated/` by default.

## Optional VS Code task

Copy `assets/vscode/tasks.json` into `.vscode/tasks.json` in your project, then run **Tasks: Run Task** → **Translate PDFs with MinerU**.

If the task cannot find the script, set an environment variable pointing to the installed skill script:

macOS/Linux:

```bash
export MINERU_PDF_SKILL_SCRIPT="$HOME/.claude/skills/mineru-pdf-vscode/scripts/pdf_translate.py"
```

Windows PowerShell:

```powershell
$env:MINERU_PDF_SKILL_SCRIPT="$HOME\.claude\skills\mineru-pdf-vscode\scripts\pdf_translate.py"
```

## Credential files

Place either these local files in the PDF workdir:

```text
mineru密钥.txt
翻译大模型url以及key.txt
```

or create `.env` from `assets/env.example`. Keep all of these files out of git.
