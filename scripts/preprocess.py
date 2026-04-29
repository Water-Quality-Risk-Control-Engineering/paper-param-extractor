#!/usr/bin/env python3
"""
LitExtract 并行预处理器 — Stage 0 + 并行 Stage 1
将 17 次串行视觉 API 调用变为并发执行，12 分钟 → ~1 分钟

用法:
  python3 scripts/preprocess.py paper.pdf [--max-workers 17]

输出:
  paper_visual_cache.json  — Stage 0 锚点 + Stage 1 视觉精读缓存
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import fitz  # PyMuPDF
from openai import OpenAI

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

VISUAL_READING_PROMPT = """你是一个学术文献视觉读取专家。请仔细观察这一页PDF图片，将页面上所有可见信息转录为结构化Markdown。

重要约束：
- 只转录你在图片中实际看到的内容，不要添加任何你"知道"但图片中没有的信息
- 如果某个数值看不清楚，用 [unclear] 标记，不要猜测
- 不要从你的训练知识中补充任何数据

转录要求：
1. 正文文字完整转录，保留标题层级（#/##/###）
2. 表格转为Markdown表格，确保表头与数据行严格对齐
3. Figure/图表：描述图中内容，尽量读出具体数值
4. 图注（Caption）完整转录
5. 公式转为LaTeX格式
6. 脚注完整保留
7. 在每个内容块前标注类型标签：[TEXT]、[TABLE]、[FIGURE]、[CAPTION]、[EQUATION]、[FOOTNOTE]

输出纯Markdown，不要添加任何解释性文字。"""

# ═══════════════════════════════════════════════════════════
# Stage 0: Text anchoring
# ═══════════════════════════════════════════════════════════

SKIP_KEYWORDS = ["References", "Bibliography", "REFERENCES", "BIBLIOGRAPHY",
                 "Supporting Information", "Electronic Supplementary Material"]
CRYSTAL_KEYWORDS = ["CCDC number", "CCDC ", "R1 =", "wR2 =", "Crystal system",
                    "Crystal data", "space group", "Rint", "Goodness-of-fit",
                    "Flack parameter", "Residual density"]
NMR_SKIP_MARKERS = [r"δ\s*/\s*ppm", r"δ\(ppm\)", r"chemical shift.*\(ppm\)",
                    r"\d+\.\d+\s*\([dtsm]\)", r"Hz,\s*\d+H"]
META_NOISE = {"Angewandte Chemie", "International Edition", "Downloaded",
              "Wiley", "Online Library", "Terms", "Conditions"}


def is_skip_page(text: str) -> bool:
    stripped = text.strip()
    for kw in SKIP_KEYWORDS:
        if stripped.startswith(kw):
            return True
    crystal_score = sum(1 for kw in CRYSTAL_KEYWORDS if kw in text)
    if crystal_score >= 3:
        return True
    nmr_matches = sum(1 for pat in NMR_SKIP_MARKERS if re.search(pat, text, re.IGNORECASE))
    many_numbers = len(re.findall(r"\d+\.\d+", text)) > 15
    many_nmr_lines = len(re.findall(r"^\s*\d+\.\d+", text, re.MULTILINE)) > 5
    if (nmr_matches >= 2 and many_numbers) or many_nmr_lines:
        return True
    return False


def is_data_page(text: str, page_obj, page_num: int) -> bool:
    has_ft = bool(re.search(r"\b(?:Figure|Table|Fig\.|Tab\.|Scheme)\b[\s]*[S]?\d+",
                            text, re.IGNORECASE))
    number_count = len(re.findall(r"\b\d+\.?\d*\b", text))
    try:
        images = page_obj.get_images(full=True)
        total_img_size = sum(img[2] for img in images if len(img) > 2)
    except Exception:
        total_img_size = 0
    if page_num <= 10:
        return has_ft
    if total_img_size > 50000 and has_ft and number_count > 2:
        return True
    return False


def run_stage0(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    pages_text, page_types = [], []
    for i, page in enumerate(doc):
        text = page.get_text()
        pages_text.append({"page": i + 1, "text": text})
        if is_skip_page(text):
            page_types.append({"page": i + 1, "type": "skip_page"})
        elif is_data_page(text, page, i + 1):
            page_types.append({"page": i + 1, "type": "data_page"})
        else:
            page_types.append({"page": i + 1, "type": "text_page"})

    front_text = " ".join(p["text"] for p in pages_text[:3])
    doi_match = re.search(r"10\.\d{4,}/[^\s]+", front_text)
    anchor_doi = doi_match.group(0) if doi_match else None
    page1_lines = [l.strip() for l in pages_text[0]["text"].split("\n")
                   if l.strip() and len(l.strip()) > 10]
    anchor_title = max(page1_lines[:10], key=len) if page1_lines else ""
    kw_match = re.search(r"(?:Keywords|KEYWORDS)[:：]\s*(.+)", front_text, re.IGNORECASE)
    anchor_keywords = [k.strip() for k in kw_match.group(1).split(",")] if kw_match else []

    data_pages = [pt for pt in page_types if pt["type"] == "data_page"]
    text_count = sum(1 for pt in page_types if pt["type"] == "text_page")
    skip_count = sum(1 for pt in page_types if pt["type"] == "skip_page")
    print(f"Stage 0: {len(doc)} pages → {len(data_pages)} data, "
          f"{text_count} text, {skip_count} skip")
    print(f"  Title: {anchor_title[:80]}...")
    print(f"  DOI: {anchor_doi}")
    doc.close()
    return {
        "pages_text": pages_text,
        "page_types": page_types,
        "anchor_title": anchor_title,
        "anchor_doi": anchor_doi,
        "anchor_keywords": anchor_keywords,
        "data_page_nums": [dp["page"] for dp in data_pages],
        "total_pages": len(pages_text),
        "preprocess_date": date.today().isoformat(),
    }


# ═══════════════════════════════════════════════════════════
# Stage 1: Parallel visual reading
# ═══════════════════════════════════════════════════════════

def read_page_visual(client: OpenAI, pdf_path: str, page_num: int, dpi: int = 150) -> tuple:
    """Read a single page visually. Returns (page_num, markdown_text)."""
    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("png")
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    doc.close()

    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": VISUAL_READING_PROMPT},
        ],
    }]

    resp = client.chat.completions.create(
        model="qwen3.6-plus",
        messages=messages,
        max_tokens=16384,
    )
    return page_num, resp.choices[0].message.content or ""


def run_stage1_parallel(stage0: dict, pdf_path: str, api_key: str,
                        max_workers: int = 17) -> dict[int, str]:
    """Run all data page visual reads in parallel."""
    client = OpenAI(base_url=DASHSCOPE_BASE, api_key=api_key)
    page_nums = stage0["data_page_nums"]

    print(f"\nStage 1: {len(page_nums)} pages in parallel (max_workers={max_workers})...")
    t0 = time.time()
    results: dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(read_page_visual, client, pdf_path, pg): pg
            for pg in page_nums
        }
        for future in as_completed(futures):
            pg_num, md = future.result()
            results[pg_num] = md
            elapsed = time.time() - t0
            print(f"  Page {pg_num:>3} OK ({len(md)} chars) [{elapsed:.0f}s]")

    print(f"  Done in {time.time() - t0:.0f}s ({len(results)} pages)")
    return results


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LitExtract 并行预处理器 — Stage 0 + 并行 Stage 1"
    )
    parser.add_argument("pdf", help="PDF file path")
    parser.add_argument("--api-key", help="DashScope API key", default=None)
    parser.add_argument("--max-workers", type=int, default=17,
                        help="Max parallel workers (default: 17)")
    parser.add_argument("-o", "--output", help="Cache output path", default=None)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: Set DASHSCOPE_API_KEY env var or use --api-key")
        sys.exit(1)

    pdf_path = os.path.abspath(args.pdf)
    output_path = args.output or os.path.splitext(pdf_path)[0] + "_visual_cache.json"

    # Stage 0
    stage0 = run_stage0(pdf_path)

    # Stage 1 (parallel)
    visual_results = run_stage1_parallel(stage0, pdf_path, api_key, args.max_workers)

    # Combine and save
    cache = {
        "stage0": stage0,
        "visual_markdown": {str(k): v for k, v in visual_results.items()},
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"\nCache saved to: {output_path}")


if __name__ == "__main__":
    main()
