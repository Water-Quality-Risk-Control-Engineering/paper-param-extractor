#!/usr/bin/env bash
# ============================================================
# LitExtract — 一键部署脚本
# ============================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║   📑  LitExtract — 文献智能提参助手       ║"
echo "║     一键部署脚本 v1.0                    ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ---- Check Node.js ----
if ! command -v node &> /dev/null; then
    echo -e "${YELLOW}[1/5] Node.js 未安装，正在安装...${NC}"
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt install -y nodejs
else
    echo -e "${GREEN}[1/5] Node.js 已安装: $(node --version)${NC}"
fi

# ---- Install OpenClaw ----
if ! command -v openclaw &> /dev/null; then
    echo -e "${YELLOW}[2/5] 安装 OpenClaw CLI...${NC}"
    npm install -g openclaw
else
    echo -e "${GREEN}[2/5] OpenClaw 已安装: $(openclaw --version 2>&1 | head -1)${NC}"
fi

# ---- Configure API Key ----
echo -e "${YELLOW}[3/5] 配置 API Key...${NC}"
if [ -f "openclaw.json" ]; then
    if grep -q "YOUR_DASHSCOPE_API_KEY" openclaw.json; then
        echo -ne "  请输入你的阿里百炼 DashScope API Key: "
        read -r API_KEY
        if [ -n "$API_KEY" ]; then
            # macOS compatibility
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s/YOUR_DASHSCOPE_API_KEY/$API_KEY/g" openclaw.json
            else
                sed -i "s/YOUR_DASHSCOPE_API_KEY/$API_KEY/g" openclaw.json
            fi
            echo -e "${GREEN}  API Key 已配置 ✅${NC}"
        else
            echo -e "${RED}  API Key 为空，请在 openclaw.json 中手动设置${NC}"
        fi
    else
        echo -e "${GREEN}  API Key 已配置 ✅${NC}"
    fi
else
    echo -e "${RED}  未找到 openclaw.json，请检查工作目录${NC}"
    exit 1
fi

# ---- Install Python dependencies (for PyMuPDF / pdf2image) ----
echo -e "${YELLOW}[4/5] 安装 Python 依赖...${NC}"
if command -v python3 &> /dev/null; then
    pip3 install PyMuPDF pdf2image 2>/dev/null || pip3 install PyMuPDF pdf2image --user 2>/dev/null || echo "  ⚠️ pip 安装失败，请手动安装: pip install PyMuPDF pdf2image"
    # Check poppler
    if ! ldconfig -p 2>/dev/null | grep -q libpoppler || ! command -v pdftoppm &> /dev/null; then
        echo "  ⚠️ pdf2image 需要 poppler-utils，尝试安装..."
        sudo apt install -y poppler-utils 2>/dev/null || echo "  请手动安装: sudo apt install poppler-utils"
    fi
else
    echo -e "${YELLOW}  ⚠️ Python3 未安装，视觉精读功能需要 Python${NC}"
fi

# ---- Start Gateway ----
echo -e "${YELLOW}[5/5] 启动 OpenClaw Gateway...${NC}"
openclaw gateway --force &
sleep 3

if curl -s http://127.0.0.1:18789/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Gateway 已启动: http://127.0.0.1:18789${NC}"
else
    echo -e "${RED}⚠️ Gateway 启动失败，请检查日志: cat /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log${NC}"
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN}  部署完成！📑 LitExtract 已就绪${NC}"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo ""
echo "  快速开始:"
echo "    openclaw tui                # 终端对话"
echo "    openclaw agent --message \"帮我从 PDF 提取文献参数\""
echo "    openclaw status             # 查看状态"
echo ""
echo "  Web UI: http://127.0.0.1:18789"
echo ""
