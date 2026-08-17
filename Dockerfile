# 多模型智能编排网关 - Docker 镜像
# 基础镜像：阿里云 ACR（国内加速）
FROM crpi-27zlqugq2208c0pz.cn-hangzhou.personal.cr.aliyuncs.com/xiaofeiyang930112/python:3.12-slim

WORKDIR /app

# pip 走国内镜像源（清华）
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/

# 安装依赖
# - 先 COPY requirements.txt（不常变），改代码不会触发重装依赖 → 利用 Docker 分层缓存
# - BuildKit cache mount 缓存 pip 下载，跨构建复用，不进入最终镜像
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# 复制源码（放在依赖之后，源码变更不影响依赖层缓存）
COPY src/ src/
COPY web/ web/
COPY scripts/ scripts/
COPY config/ config/

# 环境变量（生产环境通过 docker-compose / 环境注入覆盖）
ENV HOST=0.0.0.0
ENV PORT=8000
ENV DEBUG=false

EXPOSE 8000

# 启动前先初始化（幂等 seed：默认管理员 + 供应商 + 模型），再启动服务
CMD ["sh", "-c", "python scripts/init_db.py && python -m uvicorn src.main:app --host 0.0.0.0 --port 8000"]
