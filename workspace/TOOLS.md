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

### PDF转图片：PyMuPDF 内置渲染（推荐，无需 poppler）
- 用途：仅对 data_page（含图表的页面）渲染为 PNG
- 方法：`page.get_pixmap(dpi=150)` — 无需额外安装 poppler-utils
- 回退方案：pdf2image（需要 `pip install pdf2image` + `apt install poppler-utils`）
- 注意：text_page 和 skip_page 不渲染，节省 70%+ token

### 并行预处理器（⚡ 关键优化）
- 脚本：`scripts/preprocess.py`
- 用途：Stage 0 文本锚定 + 所有 data_page 的**并行**视觉精读
- 效果：17 次 API 调用同时发起，12 分钟 → ~1 分钟
- 用法：`python3 scripts/preprocess.py paper.pdf --api-key <KEY>`
- 输出：`paper_visual_cache.json`（Agent 自动检测并加载）

### 视觉精读 + 约束提参：Qwen3.6-plus
- 用途：对 data_page 做多模态视觉精读 + 文本模式约束提参
- API：百炼平台 DashScope（复用现有API Key）
- 模型：qwen3.6-plus（原生支持文本/图片/视频输入）
- 上下文：100万token
- 特点：视觉精读仅处理图表页，文字页用 PyMuPDF 文本替代

### 完整流水线（并行优化版）
```
PDF
 ├─ [并行预处理器] PyMuPDF 文本提取 → 元数据锚点 + 智能分页
 └─ [并行预处理器] 17 data_page 同时调 API → 视觉精读（~1 min）
       ↓
  加载缓存 → 文本层(text_page) + 视觉层(data_page) 合并
       ↓
  Qwen3.6-plus(文本模式, 关闭thinking) + 用户约束 + 锚点 → 提参（~1-2 min）
       ↓
  三级硬校验(元数据一致性 / 实体存在性 / 数值回溯)
       ↓
  结构化 JSON（带溯源 + 校验标记）

总耗时: ~5-7 min（优化后）vs ~24 min（优化前）
```
