---
name: literature-data-extraction
description: 文本锚定+视觉增强+硬校验混合提参：PyMuPDF文本层锚定元数据与校验基准，仅对图表页做多模态视觉精读，三级硬校验消除幻觉，输出带溯源的结构化JSON。
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

