# 多模型智能编排网关 - Docker 镜像（多阶段：前端 build + Python 运行时）

# ── Stage 1: 前端构建（Vite + React + Ant Design）────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /app
# npm 走国内镜像源
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build            # -> /app/dist

# ── Stage 2: Python 运行时 ───────────────────────────────────────────────────
# 基础镜像：阿里云 ACR（国内加速）
FROM crpi-27zlqugq2208c0pz.cn-hangzhou.personal.cr.aliyuncs.com/xiaofeiyang930112/python:3.12-slim

WORKDIR /app

# pip 走国内镜像源（清华）；BuildKit cache mount 缓存 pip 下载
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/

# 安装依赖（requirements 不常变，利用分层缓存）
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# 前端构建产物（Stage 1 产出），由 FastAPI SPA fallback serve
COPY --from=frontend /app/dist ./frontend/dist

# 后端源码
COPY src/ src/
COPY scripts/ scripts/
COPY config/ config/

# 环境变量（生产环境通过 docker-compose / 环境注入覆盖）
ENV HOST=0.0.0.0
ENV PORT=8000
ENV DEBUG=false

EXPOSE 8000

# 启动前先初始化（幂等 seed），再启动服务
CMD ["sh", "-c", "python scripts/init_db.py && python -m uvicorn src.main:app --host 0.0.0.0 --port 8000"]
