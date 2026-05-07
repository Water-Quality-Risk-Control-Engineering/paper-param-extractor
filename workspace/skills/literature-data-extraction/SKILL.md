---
name: literature-data-extraction
description: 文本锚定+视觉增强+硬校验混合提参：PyMuPDF文本层锚定元数据与校验基准，仅对图表页做多模态视觉精读，三级硬校验消除幻觉，输出带溯源的结构化JSON。内置 ADRMATS 评估智能体测试集构建 Profile（§11），支持 visible_input/hidden_oracle_label 三段式输出。
metadata:
  openclaw:
    emoji: "📑"
    always: true
---

# 文献数据提参技能（Literature Data Extraction）

## 1. 定位与触发

本 Skill 赋予 LitExtract **PDF文献阅读**和**约束驱动的结构化数据提取**能力。采用 **文本锚定 + 视觉增强 + 硬校验** 混合架构，杜绝纯视觉流水线的幻觉风险。

**触发条件**（满足任一即激活）：
- 用户提供了 PDF 文件路径，要求阅读或分析
- 用户要求从文献中提取特定数据/参数
- 用户指定了提取的键（key）和值类型（value type），要求输出 JSON
- 用户提到"提参"、"数据提取"、"文献提取"、"提取参数"等关键词

**核心原则**：
- **文本锚定**：先用 PyMuPDF 提取文本层获得不可幻觉的元数据锚点（标题/DOI/作者/关键词），全程绑定
- **视觉增强**：仅对含图表的页面做多模态视觉精读，文字页直接用文本层，跳过无关页（参考文献等）
- **硬校验**：提取结果必须通过三级校验（元数据一致性 / 实体存在性 / 数值回溯），不通过则标记或删除
- **忠于原文**：提取数据必须可追溯到文献原文位置（页码、表格、Figure编号）
- **缺失透明**：文献中无法找到的字段标记为 `null` 并注明原因

---

## 2. 混合流水线架构

### 2.1 架构总览

```
PDF 文件
  │
  ├─ [Stage 0] PyMuPDF 文本提取（< 1秒，无需API）
  │   ├─ 元数据锚点：标题、DOI、作者、关键词（不可覆盖）
  │   ├─ 智能分页：标记每页类型（data_page / text_page / skip_page）
  │   └─ 校验基准：全文纯文本，供后续硬校验使用
  │
  ├─ [Stage 1] 选择性视觉精读（仅 data_page）
  │   ├─ pdf2image 转图（仅图表页，300 DPI）
  │   └─ Qwen3.6-plus 多模态精读（可并发）
  │
  ├─ [Stage 2] 合并 + 约束提参
  │   ├─ text_page 用 PyMuPDF 文本，data_page 用视觉精读 Markdown
  │   ├─ 按页码顺序合并为全文
  │   └─ Qwen3.6-plus 文本模式 + 用户约束 + 元数据锚点 → 结构化提取
  │
  └─ [Stage 3] 三级硬校验
      ├─ Level 1: 元数据一致性（标题/DOI/作者）
      ├─ Level 2: 实体存在性（材料名/污染物名在原文中的出现次数）
      ├─ Level 3: 数值回溯（关键数值在原文文本中可查）
      └─ 输出最终 JSON
```

**为什么不纯视觉？**
纯视觉流水线对长文档（>30页）存在严重幻觉风险——模型可能将训练语料中相似主题论文的内容注入到当前文献的提取结果中。文本锚定提供了不可幻觉的ground truth基准。

### 2.2 Stage 0 — 文本锚定与智能分页

**执行工具**：PyMuPDF (`import fitz`)，本地执行，无需API调用

**Step 0.1 — 全文文本提取**：
```python
import fitz
doc = fitz.open(pdf_path)
pages_text = []
for i, page in enumerate(doc):
    pages_text.append({"page": i + 1, "text": page.get_text()})
full_text = "\n".join([p["text"] for p in pages_text])
```

**Step 0.2 — 元数据锚点硬提取**：

从前2页文本中提取以下信息，作为**不可覆盖的锚点**：

| 锚点字段 | 提取方法 | 用途 |
|----------|----------|------|
| `anchor_title` | 第1页正文中最大字号/最靠前的标题文本 | 校验提取结果的论文标题 |
| `anchor_doi` | 正则匹配 `10\.\d{4,}/[^\s]+` | 校验DOI |
| `anchor_authors` | 标题与摘要之间的作者列表 | 校验作者 |
| `anchor_keywords` | "Keywords:" 后的关键词列表 | 辅助识别研究主题 |
| `anchor_material_keywords` | 全文高频名词短语（出现>5次的专有名词） | 校验材料实体 |

**Step 0.3 — 智能分页**：

扫描每页文本，标记页面类型：

| 页面类型 | 判定规则 | 处理方式 |
|----------|----------|----------|
| `data_page` | 页面含 "Figure"/"Table"/"Fig."/"Tab." 且含数值数据，或页面含图片对象 | pdf2image + 多模态视觉精读 |
| `text_page` | 纯文字为主，无图表标记 | 直接使用 PyMuPDF 文本 |
| `skip_page` | 满足以下任一条件则跳过：(1) 以 "References" / "Bibliography" 开头；(2) 全页为晶体学参数表（含 "CCDC"/"R1 ="/"wR2 ="）；(3) 全页为NMR峰列表（连续 δ/ppm 数据）；(4) 全页为参考文献编号列表 | 完全跳过，不纳入提参上下文 |

```python
def classify_page(page_text, page_num, doc_page):
    text = page_text.strip()
    # skip_page 判定
    if text.startswith("References") or text.startswith("Bibliography"):
        return "skip_page"
    if any(kw in text for kw in ["CCDC number", "R1 =", "wR2 =", "Crystal system"]):
        if text.count("CCDC") + text.count("R1 =") > 2:
            return "skip_page"
    # data_page 判定
    has_figure_table = any(kw in text for kw in ["Figure", "Table", "Fig.", "Tab."])
    has_images = len(doc_page.get_images()) > 0
    if has_figure_table or has_images:
        return "data_page"
    return "text_page"
```

### 2.3 Stage 1 — 选择性视觉精读（优先使用并行预处理器）

**首选方案**：运行并行预处理器（推荐——12 分钟 → 1 分钟）

```bash
python3 scripts/preprocess.py <PDF路径> --api-key <百炼API_KEY>
```

预处理器自动完成：
- Stage 0 文本锚定 + 智能分页
- Stage 1 全部 data_page 并发视觉精读（17 线程）
- 输出 `<PDF>_visual_cache.json` 缓存文件

Agent 检测到缓存文件后直接加载，跳过 Stage 0 和 Stage 1。

**回退方案**：Agent 手动逐页调用（仅当预处理器不可用时）

**执行者**：Qwen3.6-plus（DashScope，多模态模式）

**只处理 `data_page` 类型的页面**，其余页面跳过视觉精读。

**输入**：单页PDF的截图（150 DPI，PNG格式，使用 PyMuPDF 内置渲染）

**逐页视觉精读 Prompt**（含防幻觉约束）同上。

**并发策略**：多个 data_page API 调用必须同时提交（并行），不得串行等待。

### 2.4 Stage 2 — 合并与约束提参

**执行者**：Qwen3.6-plus（文本模式，**关闭 reasoning/thinking**）

调用 API 时必须设置 `enable_thinking: false`，节省推理 token 和时间（5 分钟 → 1-2 分钟）。

**合并策略**：
- `text_page`：直接使用 PyMuPDF 提取的文本，在前面标注 `[Page N - text]`
- `data_page`：使用 Stage 1 视觉精读的 Markdown，在前面标注 `[Page N - visual]`
- `skip_page`：完全不纳入

**提参 Prompt 模板**（含强制元数据锚点注入）：

```
你是一个严谨的科学文献数据提取专家。

## 论文元数据（已从PDF文本层硬提取，不可修改，你的提取结果必须与此一致）
- 标题: {anchor_title}
- DOI: {anchor_doi}
- 第一作者: {anchor_first_author}
- 关键词: {anchor_keywords}
- 核心材料/实体关键词（文中高频出现）: {anchor_material_keywords}

## 重要约束
- extraction_meta 中的 source、doi、authors 必须使用上面的锚点值，不可修改
- data 中的 material_type 和 chemical_formula 必须是上面"核心材料关键词"中出现的实体
- data 中的 target_pollutant 必须是本文实际研究的污染物，不可引入文中未提及的物质
- 所有数值必须直接来源于下面的文献内容，不得从你的知识库中补充

## 文献内容
{merged_full_text}

## 用户约束
- 提取字段：{field_definitions}
- 筛选条件：{constraints}
- 输出粒度：{granularity}

## 提取规则
1. 每个值必须直接来源于上面的文献内容，不得推断或编造
2. 数值必须保留原文单位，若需转换须注明
3. 文献中未明确给出的字段，值设为 null，并在 _source 字段说明原因
4. 若同一字段在文献不同位置有多个值，全部列出并标注出处
5. 表格数据优先从 [TABLE] 标记的内容中提取
6. Figure中的数值（来自 [FIGURE] 标记）如为估读值，在 _quality 中标记 needs_review
7. 注意跨页内容的连续性，同一个表格可能分布在相邻页面

## 输出格式
返回严格的 JSON，结构如下：
{output_schema}
```

---

## 3. 三级硬校验协议（Anti-Hallucination Validation）

提取完成后，**必须**对结果执行以下三级校验。校验使用 Stage 0 保存的 PyMuPDF 全文文本（`full_text`）作为基准。

### 3.1 Level 1 — 元数据一致性校验

| 校验项 | 校验方法 | 不通过处理 |
|--------|----------|-----------|
| `extraction_meta.source` | 必须包含 `anchor_title` 的主要词组 | 替换为 `anchor_title` |
| `extraction_meta.doi` | 必须等于 `anchor_doi` | 替换为 `anchor_doi` |
| `extraction_meta.authors` | 第一作者必须匹配 `anchor_authors[0]` | 替换为 `anchor_authors` |

**如果元数据校验全部失败（标题+DOI+作者都不匹配），说明提取结果整体来自幻觉，必须丢弃并重新执行 Stage 2。**

### 3.2 Level 2 — 实体存在性校验

对 `data` 数组中的每条记录：

```python
def validate_entity(record, full_text):
    issues = []
    # 检查材料实体
    material = record.get("chemical_formula", "") or record.get("material_type", "")
    material_keywords = extract_keywords(material)  # 拆分为关键词
    found = any(kw in full_text for kw in material_keywords)
    if not found:
        issues.append(f"材料 '{material}' 在原文中未出现，疑似幻觉")

    # 检查污染物实体
    pollutant = record.get("target_pollutant", "")
    pollutant_abbr = extract_abbreviation(pollutant)  # 提取缩写如 "PFBA"
    if pollutant_abbr and pollutant_abbr not in full_text:
        issues.append(f"污染物 '{pollutant_abbr}' 在原文中未出现，疑似幻觉")

    return issues
```

**处理规则**：
- 材料实体在原文出现 0 次 → **删除该记录**，在 `extraction_notes` 中标注 "已删除: {material} 在原文中未出现（幻觉）"
- 污染物缩写在原文出现 0 次 → **删除该记录**，在 `extraction_notes` 中标注
- 材料实体出现 < 3 次 → 在 `_quality` 中标记 `suspicious`

### 3.3 Level 3 — 数值回溯校验

对每条记录中的关键数值字段：

```python
def validate_value(field_name, value, full_text):
    if value is None:
        return "unavailable"
    value_str = str(value)
    # 在原文中搜索该数值
    if value_str in full_text:
        return "reliable"
    # 尝试近似匹配（±1%）
    try:
        num = float(value_str.replace(">", "").replace("<", "").replace("~", ""))
        for offset in [0, 0.01, -0.01, 0.1, -0.1]:
            if str(round(num + offset, 1)) in full_text:
                return "reliable"
    except ValueError:
        pass
    # 来自视觉精读页面
    return "needs_review"
```

**处理规则**：
- 数值在文本层中精确匹配 → `reliable`
- 数值在文本层找不到但来自 `data_page`（视觉精读）→ `needs_review`（可能是Figure估读值）
- 数值在文本层和视觉精读中都找不到 → `suspicious`（可能是幻觉值）

---

## 4. 数据提参协议（Execution Protocol）

### 4.1 用户输入规范

| 输入项 | 必需 | 说明 | 示例 |
|--------|------|------|------|
| **PDF文件路径** | 是 | 本地PDF文件路径或多个文件路径 | `~/papers/Zhang2024.pdf` |
| **提取字段定义** | 是 | 键名 + 值类型/含义描述 | `{"adsorbent_name": "吸附剂名称", "BET_surface_area": "比表面积(m²/g)"}` |
| **约束条件** | 否 | 筛选或过滤条件 | "只提取温度25°C下的实验数据" |
| **输出粒度** | 否 | 每条数据对应的实体单位 | "每种吸附剂材料一条记录" |

### 4.2 完整执行流程

```
Step 0: 检查缓存
  查找 <PDF>_visual_cache.json 缓存文件
  若存在 → 直接加载锚点 + 视觉精读结果，跳至 Step 3

Step 1: 解析用户约束
  理解用户定义的 key-value 结构和筛选条件
  若无可用的并行预处理器，执行 Step 2a-2b

Step 2a: Stage 0 — 文本锚定（本地 PyMuPDF, < 1s）
  PyMuPDF 提取全文文本 → 硬提取元数据锚点 → 智能分页

Step 2b: Stage 1 — 并行视觉精读
  对所有 data_page 同时发起 API 调用（17 线程并发）
  耗时: max(单页延迟) ≈ 40-60s（vs 串行 12 min）
  text_page 直接使用 PyMuPDF 文本，skip_page 完全跳过

Step 3: Stage 2 — 合并与提参（关闭 thinking）
  文本层(text_page) + 视觉层(data_page) 按页码合并
  注入元数据锚点到提参 Prompt
  Qwen3.6-plus 文本模式约束提取（enable_thinking: false, 1-2 min）

Step 4: Stage 3 — 三级硬校验（本地 Python, < 1s）
  Level 1: 元数据一致性 / Level 2: 实体存在性 / Level 3: 数值回溯
  不通过的记录标记或删除

Step 5: 输出综合 JSON
  附带校验结果、溯源和质量标记
```
  总耗时: **5-7 分钟**（优化后） vs 24 分钟（优化前）

---

## 5. 输出规范

### 5.1 标准 JSON 输出结构

```json
{
  "extraction_meta": {
    "source": "论文标题（必须等于 anchor_title）",
    "doi": "DOI（必须等于 anchor_doi）",
    "authors": "作者列表（必须等于 anchor_authors）",
    "extraction_date": "YYYY-MM-DD",
    "total_pages": 84,
    "pages_visual_read": 15,
    "pages_text_only": 40,
    "pages_skipped": 29,
    "constraints_applied": "用户约束条件描述",
    "total_records": 7,
    "records_removed_by_validation": 0,
    "pipeline": "text-anchored + visual-enhanced + hard-validated"
  },
  "field_definitions": {
    "key_name": "用户定义的值含义描述"
  },
  "data": [
    {
      "key_1": "extracted_value_1",
      "key_2": 123.4,
      "key_3": null,
      "_source": {
        "key_1": "Page 5, Table 2, Row 3",
        "key_2": "Page 8, Section 3.2, para 1",
        "key_3": "文献未提供该参数"
      },
      "_quality": {
        "key_1": "reliable",
        "key_2": "reliable",
        "key_3": "unavailable"
      }
    }
  ],
  "validation_report": {
    "level_1_metadata": "PASS",
    "level_2_entities_checked": 7,
    "level_2_entities_removed": 0,
    "level_3_values_reliable": 15,
    "level_3_values_needs_review": 3,
    "level_3_values_suspicious": 0
  },
  "extraction_notes": [
    "Figure 3 中的去除率数据为从柱状图估读，精度约±2%"
  ]
}
```

### 5.2 字段级溯源规则

每条提取记录必须包含 `_source` 对象，格式统一带页码：

| 溯源类型 | 格式 | 示例 |
|----------|------|------|
| 表格数据 | `Page N, Table X, Row M` | `"Page 6, Table 2, Row 5"` |
| 正文段落 | `Page N, Section X.Y, para M` | `"Page 3, Section 2.3, para 2"` |
| Figure数据 | `Page N, Figure X` + 读取方式 | `"Page 9, Figure 4, 柱状图估读"` |
| 图注 | `Page N, Figure X caption` | `"Page 9, Figure 4 caption"` |
| 摘要 | `Page 1, Abstract` | `"Page 1, Abstract"` |
| 补充材料 | `Supplementary, Table SN` | `"Supplementary, Table S2"` |
| 未找到 | 原因说明 | `"全文未报告该参数"` |

### 5.3 数据质量标记

| 质量等级 | 标记 | 含义 | 典型场景 |
|----------|------|------|----------|
| **可靠** | `reliable` | 原文文本层可查，数值/单位清晰 | 表格中的精确数值，Level 3 回溯通过 |
| **需确认** | `needs_review` | 值来自视觉精读的图表估读，文本层中无对应文本 | Figure中的柱状图高度、折线图数据点 |
| **可疑** | `suspicious` | Level 3 回溯未通过，可能是幻觉值 | 数值在文本层和视觉层都找不到 |
| **推断值** | `inferred` | 非原文直接给出，由相关数据推算 | 由进出水浓度推算去除率 |
| **不可用** | `unavailable` | 文献中未提供该信息 | 字段值为 null |

---

## 6. 多文献综合提参

对多篇文献，**逐篇独立执行完整流水线（Stage 0-3）**，最后合并：

```
Paper_1.pdf → Stage 0-3 → JSON_1（含 validation_report）
Paper_2.pdf → Stage 0-3 → JSON_2（含 validation_report）
                  ↓
         合并为统一JSON（附 paper_id 索引）
```

---

## 7. 性能与成本参考

以单篇 **84页论文（含SI）** 为基准（优化后 vs 优化前）：

| 环节 | 优化后 | 优化前（纯视觉） | 改善 |
|------|--------|-----------------|------|
| 环节 | 优化后（并行 + 关闭 thinking） | 优化前（串行视觉） | 改善 |
|------|--------|-----------------|------|
| Stage 0 文本锚定 | ~1s, ¥0 | ~1s, ¥0 | — |
| Stage 1 视觉精读 | **~1 min（17 页并行）**, ¥0.3-0.5 | ~12 min（17 页串行）, ¥0.3-0.5 | **-90% 时间** |
| Stage 2 提参 | **~1-2 min（disable_thinking）**, ¥0.1-0.2 | ~5 min（reasoning on）, ¥0.1-0.2 | **-60% 时间** |
| Stage 3 硬校验 | ~1s, ¥0 | ~1s, ¥0 | — |
| **总计** | **~5-7 min, ¥0.5-0.8** | **~24 min, ¥0.5-0.8** | **-70% 时间** |

---

## 8. 异常处理

| 异常场景 | Agent 行为 |
|----------|----------|
| PDF加密/无法打开 | 告知用户，要求提供无密码版本 |
| PyMuPDF 文本层为空（纯扫描PDF） | 回退到全页视觉精读模式，在 extraction_notes 中声明 |
| 页面为纯扫描图且分辨率极低 | VL模型仍可尝试，但在 extraction_notes 中声明精度风险 |
| Figure数据密集且数值密集重叠 | 标记为 needs_review，建议用户人工校验 |
| 跨页表格 | 逐页读取后，Stage 2 负责跨页关联拼接 |
| 单位不统一 | 保留原文单位，在 extraction_notes 中提供换算建议 |
| Level 1 校验全部失败 | 说明提取结果整体来自幻觉，丢弃并重新执行 Stage 2 |
| Level 2 删除了所有记录 | 在 extraction_notes 中说明，建议用户检查PDF是否正确 |
| 字段值存在矛盾（摘要vs正文vs表格） | 优先级：表格 > 正文 > 摘要，在 _source 中记录矛盾 |

---

## 9. 典型使用场景

### 场景 A：单篇论文提参（图文混合型，含SI）

**用户输入**：
> 帮我从 `~/papers/Andersson2026.pdf` 中提取所有PFAS吸附去除数据：
> - material_type: 吸附剂类型
> - target_pollutant: 目标PFAS
> - removal_rate_percent: 去除率(%)
> - adsorption_capacity_mg_g: 吸附容量(mg/g)
> - binding_thermodynamics: 热力学参数
> 
> 约束：每种PFAS一条记录，含水质信息。

**Agent 执行**：
1. Stage 0: PyMuPDF 提取 → 锚点={title, doi, authors, keywords}，智能分页 → 84页中约15页为data_page
2. Stage 1: 仅对15个data_page做视觉精读
3. Stage 2: 合并 + 锚点注入 + 约束提参
4. Stage 3: 三级硬校验 → 输出JSON

### 场景 B：多文献对比提参

**用户输入**：
> 从这3篇PDF中提取MOF材料性能对比数据。

**Agent 执行**：3篇并行处理（各自 Stage 0-3）→ 合并为带 paper_id 的统一JSON

---

## 10. 与其他 Skill 的协作

| Skill | 协作方式 |
|-------|---------|
| water_data_analysis | 本 Skill 提取的文献参数可作为水质分析的参考输入 |
| 未来扩展 | 提取的 JSON 可直接供下游技能消费（材料筛选、对比分析、知识图谱构建等） |

---

## 11. 业务 Profile：ADRMATS 评估智能体测试集构建

### 11.1 Profile 定位

本 Profile 用于生成 **ADRMATS 评估智能体（Evaluation Agent）** 的性能测试集。测试对象仅限评估智能体本身（不含约束识别智能体、设计智能体、提取模块）。测试输入必须对齐评估智能体在主链路中实际接收的两类结构化信息：

```
visible_input = constraint_context + merged_proposals
hidden_oracle_label（不得随 visible_input 传入）
```

**激活条件**（满足任一）：
- 用户明确提到「ADRMATS 评估智能体测试集」「评估智能体性能测试」「排序准确率测试集」
- 用户要求输出包含 `visible_input` / `hidden_oracle_label` / `source_trace` 的三段式记录
- 用户要求提取结果同时包含 `constraint_context` 与 `merged_proposals`

激活后，本 Profile 作为 §4 Stage 2 提参 Prompt 的业务约束层注入，Stage 0/1/3 的文本锚定、视觉增强与三级硬校验机制全部保留。

### 11.2 最小记录粒度与拆分规则

单条记录以 **一个材料 × 一个污染物 × 一个水质场景 × 一个实验类型 × 一组独立实验条件** 为最小单元。以下场景强制拆分为多条 records：

1. 文献中存在多个材料 → 每种材料一条
2. 单一材料对应多种水质场景（超纯水 / 自来水 / 地下水 / 模拟废水 / 真实废水）→ 每种水质一条
3. 单一材料对应多种污染物（如 PFBA / PFOA / PFOS / OTC）→ 每种污染物一条
4. 单一材料在不同 pH / 盐度 / 硬度 / 共存离子 / 有机物条件下测试 → 按独立实验条件拆分
5. 批量吸附实验与柱实验 → 分开记录（工程意义不同）

**来源标记**（写入 `source_trace.record_origin`）：

| 取值 | 适用场景 |
|------|----------|
| `this_work` | 本文实验获得的数据 |
| `control` | 本文设置的对照 / 未改性 / 商业基准材料 |
| `literature_comparison` | 本文引用自其他文献的对比表数据 |
| `synthetic_noise` | 人工构造的错配 / 单位错置 / 矛盾样本 |

### 11.3 字段提取要求

**A. 构造 `constraint_context` 所需文献信息**

- 水质：`water_source_type` / `real_or_synthetic_water` / `pH` / `temperature` / `salinity_or_ionic_strength` / `hardness` / `major_ions` / `organic_load` / `coexisting_species` / `microbial_activity` / `special_water_characteristics`
- 污染物：`pollutant_name` / `CAS` / `molecular_formula` / `molecular_weight` / `pollutant_class` / `initial_concentration` / `pKa` / `charge_state_at_test_pH` / `molecular_size_or_chain_length` / `hydrophobicity` / `functional_groups` / `special_properties`
- `evaluation_weights` 与 `design_guidelines` 由下游脚本基于以上水质/污染物信息生成，本阶段可置 null；文献提参阶段仅需保证水质与污染物信息足以支撑权重生成

**B. 构造 `merged_proposals` 所需文献信息**

| 目标字段 | 文献对应内容 |
|----------|-------------|
| `material_type` | carbon / resin / MOF / COF / polymer / cage / xerogel / hybrid |
| `chemical_formula` | 化学式、材料缩写、复合材料主组成 |
| `element_composition` | 元素组成、掺杂元素、金属比例、XPS/EDS 结果 |
| `active_sites` | 胺基 / 季铵基 / 羧基 / 羟基 / 氟碳链 / 金属位点 / 孔穴 / 疏水域 / π 结构 |
| `pore_structure` | 微孔 / 介孔 / 大孔 / 孔径 / 孔容 / 孔径分布 |
| `specific_surface_area` | BET 比表面积 |
| `morphology` | 粉末 / 颗粒 / 树脂 / 凝胶 / 干凝胶 / 磁性颗粒 / 柱填料等 |
| `zeta_potential` | zeta 电位；若无则记录 pHpzc 或表面电荷推断并注明 |
| `target_pollutant` | 污染物名称 + CAS |
| `adsorption_performance.capacity_mg_g` | qmax / qe / 穿透容量 / 估算容量 |
| `adsorption_performance.removal_rate` | 平衡去除率 / 低浓度去除率 / 柱实验去除率 |
| `adsorption_performance.kinetics` | 平衡时间 / 速率常数 / t90 / 动力学模型 |
| `design_rationale` | 机理、水质适配性、稳定性、合成可行性、再生性、环保/经济风险的压缩说明 |
| `literature_support` | 支撑该方案的文献证据、页码、图表、关键结论 |
| `do_not_compliance` | 是否违反水质约束或设计禁忌（高盐失效 / pH 不稳 / 二次污染等） |

**C. 需提取但不单列为 visible 字段的支撑证据**

下列信息不作为 `merged_proposals` 的一级字段直接暴露，必须压缩写入 `design_rationale` / `literature_support` / `do_not_compliance`：

- 合成：`synthesis_method` / `key_reagents` / `solvents` / `temperature` / `reaction_time` / `equipment` / `steps_count` / `scale_up_claim` / `commercial_availability` / `hazard_notes`
- 稳定性：`water_stability` / `pH_stability` / `mechanical_strength` / `swelling` / `leaching` / `solid_liquid_separation` / `long_term_operation`
- 再生性：`regeneration_method` / `regenerant` / `cycle_count` / `performance_retention` / `desorption_efficiency` / `secondary_waste_risk`
- 经济/环保：`raw_material_source` / `renewable_or_biomass_source` / `commercial_material_basis` / `toxic_precursors` / `fluorinated_reagent_risk` / `metal_leaching_risk` / `LCA_or_carbon_footprint` / `end_of_life`
- 抗菌：`antibacterial_tested` / `tested_microorganisms` / `inhibition_rate` / `biofilm_resistance` / `antimicrobial_durability`

**未报告抗菌不等同于抗菌性差**，应写 `not_reported`，是否扣分由下游动态权重决定。

### 11.4 领域启发（供 Stage 2 Prompt 注入）

以下启发用于帮助模型在 `design_rationale` 与 `do_not_compliance` 中给出合理判断，**不得直接写入 `evaluation_weights`**（权重生成由下游脚本负责）：

- **短链 PFAS / 痕量浓度 / 共存阴离子**：选择性与抗竞争能力重要性上升
- **高盐 / 高硬度 / 真实水样**：水稳定性、结构稳定性、再生性权重上升
- **高微生物活性 / 长期运行场景**：抗菌 / 抗生物污染维度重要性上升
- **含氟试剂 / 重金属浸出风险**：`do_not_compliance` 应显式标注
- **综述表格或引用数据**：禁止误判为本文实验，应标记 `record_origin = literature_comparison`

### 11.5 隐藏标签设计（`hidden_oracle_label`）

每条记录必须保留隐藏标签，**严禁并入 `visible_input`**：

| 字段 | 说明 |
|------|------|
| `quality_tier` | `high` / `low` / `noise` |
| `quality_reason` | 判定依据的一句话说明 |
| `expected_rank_group` | 预期排序分组（如 top / middle / bottom） |
| `noise_type` | 仅 noise 类需填：`unit_mismatch` / `mechanism_mismatch` / `capacity_implausible` / `water_condition_fake` / `regeneration_contradiction` 等 |
| `corruption_fields` | 被人工污染的字段名列表 |

**等级判定参考**：
- `high`：材料与污染物机制匹配、性能强、水质条件明确、有稳定性/再生性证据
- `low`：真实文献中的低效对照 / 未改性 / 商业基准 / 缺关键稳定性证据
- `noise`：人工构造的错配 / 矛盾样本（单位错置、机制错配、容量明显不合理等）

**下游评价指标建议**（不在本 Skill 执行，仅提示）：优先使用排序类指标——pairwise ranking accuracy / Spearman correlation / NDCG@k / top-k high-quality hit rate，而非绝对分数阈值。

### 11.6 Profile 专用输出 Schema

本 Profile 激活时，**覆盖** §5.1 标准 JSON 输出结构，改用如下三段式。`extraction_meta` 与 `validation_report` 保留（来自通用流水线），`data` 替换为 `records`：

```json
{
  "extraction_meta": { "...同 §5.1，锚点由 Stage 0 硬提取": "" },
  "paper_id": "{paper_id}",
  "records": [
    {
      "case_id": null,
      "visible_input": {
        "constraint_context": {
          "water_quality_and_pollutant": {
            "water_quality_profile": {
              "water_source_type": null,
              "ph_range": null,
              "temperature_range": null,
              "salinity_level": null,
              "typical_ion_composition": {},
              "organic_load": null,
              "microbial_activity": null,
              "special_characteristics": []
            },
            "pollutant_characteristics": [
              {
                "name": null,
                "cas_number": null,
                "typical_concentration": null,
                "pka_value": null,
                "dissociation_analysis": null,
                "charge_state": null,
                "molecular_size": null,
                "hydrophobicity": null,
                "special_properties": []
              }
            ]
          },
          "evaluation_weights": {
            "adsorption_performance": null,
            "structural_stability": null,
            "synthesis_feasibility": null,
            "economic_viability": null,
            "environmental_impact": null,
            "antimicrobial_property": null,
            "regenerability": null,
            "weight_adjustment_rationale": null
          },
          "design_guidelines": {
            "mandatory_requirements": [],
            "do_not_list": [],
            "recommended_approaches": [],
            "cautionary_notes": []
          }
        },
        "merged_proposals": [
          {
            "proposal_id": 1,
            "material_type": null,
            "chemical_formula": null,
            "element_composition": {},
            "active_sites": [],
            "pore_structure": {},
            "specific_surface_area": null,
            "morphology": null,
            "zeta_potential": null,
            "target_pollutant": null,
            "adsorption_performance": {
              "capacity_mg_g": null,
              "removal_rate": null,
              "kinetics": null
            },
            "design_rationale": null,
            "literature_support": [],
            "do_not_compliance": null
          }
        ]
      },
      "hidden_oracle_label": {
        "quality_tier": null,
        "quality_reason": null,
        "expected_rank_group": null,
        "noise_type": null,
        "corruption_fields": []
      },
      "source_trace": {
        "paper_title": null,
        "record_origin": "this_work | control | literature_comparison | synthetic_noise",
        "source_pages": [],
        "source_tables_or_figures": [],
        "evidence_text": [],
        "extraction_warnings": []
      }
    }
  ],
  "validation_report": { "...同 §5.1": "" }
}
```

### 11.7 Profile 与通用流水线的衔接

| 流水线阶段 | 本 Profile 的影响 |
|-----------|-------------------|
| Stage 0 文本锚定 | 不变，锚点（title/doi/authors/keywords）仍强制注入 |
| Stage 1 视觉精读 | 不变，data_page 仍由 Qwen3.6-plus 多模态处理 |
| Stage 2 合并提参 | **替换** `output_schema` 为 §11.6；将 §11.2–11.4 作为用户约束与领域启发注入 Prompt |
| Stage 3 三级硬校验 | 不变；对 `records[*].visible_input.merged_proposals[*].chemical_formula` 与 `target_pollutant` 执行实体存在性校验；对 `adsorption_performance.capacity_mg_g` 与 `removal_rate` 执行数值回溯校验 |

**校验失败处理**：
- Level 2 实体校验失败的 record 必须删除，并在 `extraction_meta.extraction_notes` 中记录
- Level 3 数值回溯失败但来自 `data_page` 的 → 保留，在 `source_trace.extraction_warnings` 中标注 `needs_review`
- `record_origin = literature_comparison` 的记录豁免 Level 3 数值回溯（数据本不在本文实验中）

### 11.8 典型触发示例

**用户输入示例**：
> 我要为 ADRMATS 评估智能体构建测试集，请从 `~/papers/` 的 PDF 中按 `high/low/noise` 三级提取，输出 `visible_input` + `hidden_oracle_label`。噪声样本占 10%，低质量占 20%，高质量占 70%。

**Agent 执行**：
1. 激活本 Profile（检测到 `visible_input` / `hidden_oracle_label` 关键词）
2. 对每篇 PDF 执行 Stage 0–3 通用流水线
3. Stage 2 使用 §11.6 的 Profile Schema，按 §11.2 规则拆分 records，按 §11.4 启发生成 `design_rationale` / `do_not_compliance`
4. Stage 3 按 §11.7 衔接规则执行校验
5. 合并所有 PDF 的 records 为最终测试集 JSON，由用户后续脚本挂接动态权重生成与质量标签采样

