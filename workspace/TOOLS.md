# TOOLS.md — LitExtract 工具链

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

---

## 文献处理工具链（文本锚定 + 视觉增强 + 硬校验）

### 文本锚定：PyMuPDF (fitz)
- 用途：提取 PDF 文本层，用于元数据锚定（标题/DOI/作者）、智能分页、硬校验基准
- 库：Python `PyMuPDF`（已安装）
- 特点：无需OCR，直接读取PDF文本层，速度极快（84页 < 1秒）
- 角色：Stage 0 的核心工具，提供不可幻觉的 ground truth

### PDF转图片：pdf2image
- 用途：仅对 data_page（含图表的页面）转高清图片（300 DPI）
- 库：Python `pdf2image`（底层依赖 poppler-utils）
- 安装：`pip install pdf2image` + `apt install poppler-utils`
- 注意：text_page 和 skip_page 不转图，节省 70%+ token

### 视觉精读 + 约束提参：Qwen3.6-plus
- 用途：对 data_page 做多模态视觉精读 + 文本模式约束提参
- API：百炼平台 DashScope（复用现有API Key）
- 模型：qwen3.6-plus（原生支持文本/图片/视频输入）
- 上下文：100万token
- 特点：视觉精读仅处理图表页，文字页用 PyMuPDF 文本替代

### 完整流水线
```
PDF
 ├─ PyMuPDF 文本提取 → 元数据锚点 + 智能分页 + 校验基准
 └─ pdf2image(仅data_page) → Qwen3.6-plus 多模态视觉精读(仅图表页)
       ↓
  文本层(text_page) + 视觉层(data_page) 按页码合并 → 全文Markdown
       ↓
  Qwen3.6-plus(文本模式) + 用户约束 + 元数据锚点 → 约束提参
       ↓
  三级硬校验(元数据一致性 / 实体存在性 / 数值回溯)
       ↓
  结构化 JSON（带溯源 + 校验标记）
```
