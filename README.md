# 📑 LitExtract — AI 文献数据提参助手

基于 [OpenClaw](https://github.com/nicholasgriffintn/openclaw) 框架的科学文献结构化参数提取智能体。采用 **文本锚定 + 视觉精读 + 硬校验** 混合架构，支持从 PDF 论文中按用户自定义键值结构精确提取数据，输出带溯源的结构化 JSON。

## 🎯 核心能力

| 能力 | 说明 |
|------|------|
| **PDF 文本锚定** | PyMuPDF 提取文本层，自动识别标题/DOI/作者/关键词作为不可幻觉锚点 |
| **选择性视觉精读** | 仅对有图表的页面做多模态视觉精读（Qwen3.6-plus），跳过参考文献和晶体学数据页 |
| **约束驱动提参** | 用户定义键值结构 + 筛选条件，模型按约束精确提取 |
| **三级硬校验** | Level 1 元数据一致性 → Level 2 实体存在性 → Level 3 数值回溯，杜绝幻觉数据 |
| **溯源标记** | 每个提取值标注来源页码和表格编号，质量分 reliable / needs_review / suspicious |
| **多文献对比** | 支持多篇论文并行提取，输出带 paper_id 的统一 JSON |

## ⚡ 性能参考

以 **84 页 Angew. Chem. 论文**（含 74 页 SI）为基准：

| 指标 | 数值 |
|------|------|
| **总耗时（并行优化）** | **~5-7 分钟**（vs 串行 24 分钟） |
| **视觉精读页数** | ~17 页（并行处理，~1 分钟完成） |
| **API 成本** | ~¥0.50-0.80 |
| **提取记录数** | 7 条（7 种 PFAS 污染物） |
| **数据溯源率** | 100%（每个值标注页码） |
| **幻觉记录** | 0（三级硬校验通过） |

## 📦 一键部署

### 前置要求

- **Node.js** >= 20.x
- **Python** >= 3.9（用于 PyMuPDF 和 pdf2image）
- **阿里百炼 DashScope API Key**（[免费申请](https://help.aliyun.com/zh/model-studio/get-api-key)）
- **poppler-utils**（Linux 需要 `sudo apt install poppler-utils`，macOS 用 `brew install poppler`）

### 部署步骤

```bash
# 1. 克隆仓库
git clone https://github.com/Water-Quality-Risk-Control-Engineering/paper-param-extractor.git
cd paper-param-extractor

# 2. 运行一键部署脚本
chmod +x scripts/setup.sh
bash scripts/setup.sh
```

部署脚本会自动完成：
- 安装 OpenClaw CLI (`npm install -g openclaw`)
- 配置阿里百炼 API Key（交互式输入）
- 安装 Python 依赖（PyMuPDF、pdf2image）
- 启动 OpenClaw Gateway（端口 18789）

### 手动配置

如果一键部署不成功，手动操作：

```bash
# 安装 OpenClaw
npm install -g openclaw

# 安装 Python 依赖
pip install PyMuPDF pdf2image

# 编辑 openclaw.json，将 YOUR_DASHSCOPE_API_KEY 替换为你的百炼 API Key
# 获取 Key: https://bailian.console.aliyun.com/

# 启动服务
openclaw gateway --force

# 验证
openclaw status
# 应显示: Gateway running on http://127.0.0.1:18789
```

## 📖 使用教程

### 场景 1：Web UI 对话式提参（推荐新手）

1. 打开浏览器访问 `http://127.0.0.1:18789`
2. 在对话框输入：

```
帮我从 ~/papers/Andersson2026.pdf 中提取所有 PFAS 吸附去除数据：

提取字段：
- pollutant_name: PFAS污染物名称
- material_type: 吸附剂类型
- target_pollutant: 目标污染物（含分子式、分子量）
- host_guest_stoichiometry: 主客体化学计量比
- removal_rate_percent: 去除率(%)
- adsorption_capacity_mg_g: 吸附容量(mg/g)
- binding_thermodynamics: 结合热力学参数
- adsorption_mechanism: 吸附机理
- water_quality_tested: 测试水质条件

约束：每种PFAS一条记录，包含水质信息。
```

3. LitExtract 会自动执行流水线，~5-7 分钟后输出结构化 JSON

### 场景 2：命令行对话

```bash
openclaw agent --message "从 ~/papers/Zhang2024.pdf 提取 MOF 的 BET 比表面积、孔径分布和吸附容量数据，输出 JSON"
```

### 场景 3：终端 UI（TUI）

```bash
openclaw tui
```

进入交互界面后，像聊天一样提出提取需求。

### 场景 4：多文献对比提参

```
从这三篇论文中提取 MOF 材料性能对比数据：
1. ~/papers/Li2025.pdf
2. ~/papers/Wang2024.pdf
3. ~/papers/Zhang2026.pdf

提取字段：
- material_name: 材料名称
- BET_surface_area: 比表面积(m²/g)
- pore_volume: 总孔容(cm³/g)
- CO2_uptake: CO₂吸附量(mmol/g)
- adsorption_enthalpy: 吸附焓(kJ/mol)
```

每篇论文独立执行完整校验流水线，最终合并为带 `paper_id` 的统一表格。

## 🔧 提取字段约束语法

用户可以自定义提取结构。以下是字段定义示例：

```yaml
提取字段：
- pollutant_name: PFAS污染物名称（字符串）
- material_type: 吸附剂类型代码（字符串）
- specific_surface_area_m2_g: BET比表面积 m²/g（数值）
- pore_diameter_A: 介孔孔径 Å（数值）
- target_pollutant: 目标污染物含分子式（字符串）
- host_guest_stoichiometry: 主客体化学计量比（字符串，如 1:4）
- adsorption_performance:
    removal_rate_percent: 去除率%（数值）
    adsorption_capacity_mg_g: 吸附容量 mg/g（数值）
    kinetics: 动力学描述（字符串）
    regeneration: 再生性能描述（字符串）
- binding_thermodynamics:
    log_K: 结合常数（数值）
    delta_H_kJ_mol: 焓变 kJ/mol（数值）
    delta_S: 熵变描述（字符串）
- water_quality_tested: 水质条件（含离子浓度、pH等）

约束条件：
- 每种污染物一条记录
- 优先采用表格数据
- 数值保留原文单位
- 文献中未给出的字段设为 null
```

## 📊 输出格式

提取结果为一个标准 JSON，核心结构：

```json
{
  "extraction_meta": {
    "source": "论文标题（从文本层硬提取，不可幻觉）",
    "doi": "10.1002/anie.202526027",
    "authors": "作者列表",
    "total_records": 7,
    "pipeline": "text-anchored + visual-enhanced + hard-validated"
  },
  "field_definitions": { /* 用户定义的字段说明 */ },
  "data": [
    {
      "pollutant_name": "PFBA",
      "BET_surface_area": 403,
      "removal_rate_percent": 98,
      "_source": {
        "pollutant_name": "Page 2, Table 1",
        "BET_surface_area": "Page 7, Section 2.3",
        "removal_rate_percent": "Page 65, Table S12"
      },
      "_quality": {
        "BET_surface_area": "reliable",
        "removal_rate_percent": "reliable"
      }
    }
  ],
  "validation_report": {
    "level_1_metadata": "PASS",
    "level_2_entities_removed": 0,
    "level_3_values_reliable": 15
  }
}
```

## 🏗️ 流水线架构

```
PDF 论文
  │
  ├─ Stage 0: PyMuPDF 文本锚定 (< 1s, 零 API 成本)
  │   ├─ 元数据硬提取 → 标题/DOI/作者/关键词
  │   ├─ 智能分页 → data_page / text_page / skip_page
  │   └─ 校验基准：全文文本层
  │
  ├─ Stage 1: 选择性视觉精读（仅 data_page）
  │   ├─ pdf2image → 高清截图 (300 DPI)
  │   └─ Qwen3.6-plus 多模态 → 图表 Markdown 转录
  │
  ├─ Stage 2: 合并 + 约束提参
  │   ├─ 文本层(text_page) + 视觉层(data_page) 合并
  │   ├─ 注入元数据锚点到 Prompt
  │   └─ Qwen3.6-plus 文本模式 → 结构化 JSON
  │
  └─ Stage 3: 三级硬校验（纯本地 Python）
      ├─ Level 1: 元数据一致性（标题/DOI/作者 vs 锚点）
      ├─ Level 2: 实体存在性（材料/污染物在原文中出现频次）
      └─ Level 3: 数值回溯（关键数值在原文文本中可查）
          ↓
     输出最终 JSON（带溯源标记 + 校验报告）
```

## ❓ 常见问题

<details>
<summary><b>如何获取阿里百炼 API Key？</b></summary>

1. 访问 [阿里云百炼控制台](https://bailian.console.aliyun.com/)
2. 开通"模型服务" → 获取 API Key
3. 新用户有免费配额（100 万 tokens）
4. 将 Key 填入 `openclaw.json` 的 `models.providers.bailian.apiKey` 字段
5. 或运行 `bash scripts/setup.sh` 交互式输入
</details>

<details>
<summary><b>提取一篇论文需要多长时间？</b></summary>

- 典型 20-40 页论文：~3-5 分钟
- 含详细 SI 的长论文（80+ 页）：~5-7 分钟
- 通过并行预处理器，Stage 1 视觉精读从 12 分钟压缩到 ~1 分钟
</details>

<details>
<summary><b>提取结果的质量如何？</b></summary>

三级硬校验确保：
- 元数据（标题/DOI/作者）100% 与原文一致
- 数据实体（材料/污染物）在原文中存在
- 数值可在原文中回溯验证
- 幻觉数据自动标记或删除
</details>

<details>
<summary><b>支持哪些语言的论文？</b></summary>

中英文均可。Qwen3.6-plus 对中英文混合文档有良好的多模态理解能力。
</details>

<details>
<summary><b>如果 PDF 是扫描版怎么办？</b></summary>

如果 PyMuPDF 文本层为空，系统自动回退到全页视觉精读模式，并在 extraction_notes 中标注精度风险。建议优先使用带文本层的原生 PDF。
</details>

<details>
<summary><b>可以提取补充材料（SI）中的数据吗？</b></summary>

可以。SI 中的表格数据（如去除率表、BET 数据表）可以通过 PyMuPDF 文本层直接提取。SI 中的图表（如 N₂ 吸附等温线、XRD 谱图）在视觉精读范围内。
</details>

## 📁 项目结构

```
lit-extract/
├── openclaw.json              # OpenClaw 主配置
├── README.md                  # 本文件
├── .gitignore
├── scripts/
│   └── setup.sh               # 一键部署脚本
├── workspace/
│   ├── IDENTITY.md            # 文献提参助手 角色定义
│   ├── SOUL.md                # 行为准则
│   ├── AGENTS.md              # 工作空间配置
│   ├── TOOLS.md               # 工具链说明
│   ├── USER.md                # 用户档案
│   ├── HEARTBEAT.md           # 心跳任务
│   └── skills/
│       └── literature-data-extraction/
│           └── SKILL.md       # ★ 文献提参技能定义（472 行协议）
└── agents/
    └── lit-extract/
        └── agent/
            ├── agent.json     # Agent 模型配置
            └── models.json    # 模型参数（API Key 已脱敏）
```

## 🔗 相关资源

- [OpenClaw 文档](https://github.com/nicholasgriffintn/openclaw)
- [阿里百炼 DashScope](https://bailian.console.aliyun.com/)
- [PyMuPDF 文档](https://pymupdf.readthedocs.io/)
- [示例提取结果（Andersson 2026, Angew. Chem.）](https://github.com/Water-Quality-Risk-Control-Engineering/paper-param-extractor/blob/main/examples/extraction_result_andersson_2026.json)

---

**作者**: [Water Quality Risk Control Engineering](https://github.com/Water-Quality-Risk-Control-Engineering)
**维护者**: Axl1Huang
