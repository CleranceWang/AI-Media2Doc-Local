# media2doc-local ASR service —— faster-whisper + CUDA
#
# ────────────────────────────────────────────────────────────────────────────
# 选型依据（不是凭印象，是查出来的）
# ────────────────────────────────────────────────────────────────────────────
# · ctranslate2 4.8.2 提供 cp310/cp311/cp312/cp313 的 manylinux x86_64 wheel
#   ⇒ **Python 版本不是约束**，3.11/3.12 都能用。真正的约束是 CUDA 运行时版本。
# · ctranslate2 的 CUDA 支持锚定 CUDA 12.x + cuDNN 9；
#   所以基础镜像选 CUDA 12.x 的 runtime 变体，而**不是** 11.x。
# · faster-whisper 1.2.1 是纯 Python 包（py3-none-any），不挑版本。
#
# ⚠️ 未验证：本 Dockerfile **尚未构建过**（Docker Desktop 当前未运行）。
#    首次构建时若 torch/ctranslate2 的 CUDA 版本要求对不上，
#    最可能的报错是 "libcudnn.so.9: cannot open shared object file" 或
#    运行期 "CUDA driver version is insufficient"。
#    届时先跑 `docker run --rm --gpus all <image> python -c "import ctranslate2"`
#    确认运行期能加载。
# ────────────────────────────────────────────────────────────────────────────

# CUDA 12.x runtime（不是 devel —— 我们不编译任何 CUDA 代码，只要运行时库，
# runtime 变体能省下好几个 GB）
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# ── 系统依赖 ────────────────────────────────────────────────────────────────
# ffmpeg 必须在容器内自带：宿主机 PATH 上没有（实测确认），不能依赖它。
# python3.11 来自 Ubuntu 22.04 的 deadsnakes 或系统源 —— 22.04 自带 3.10，
# 而 3.10 也在 ctranslate2 的支持列表里（cp310），所以直接用系统 python3 即可，
# 不引入第三方 apt 源，减少构建失败面。
RUN set -ex && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-dev \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖 ─────────────────────────────────────────────────────────────
# 版本全部锁死：ASR 这条链路的 wheel 兼容性已知是敏感点（CUDA ↔ ctranslate2），
# 用浮动版本会在某次重建时突然装到不兼容的组合。
RUN set -ex && \
    python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir \
        "fastapi==0.115.6" \
        "uvicorn[standard]==0.34.0" \
        "pydantic==2.10.4" \
        "boto3==1.40.69" \
        "faster-whisper==1.2.1" \
        "ctranslate2==4.8.2"

WORKDIR /app
COPY app.py ./

# 模型**不**打进镜像 —— compose 里以只读卷挂载（2.94 GB，进镜像会让体积失控，
# 且模型更新要重建镜像）。这里只声明挂载点。
VOLUME ["/models"]

EXPOSE 8000

# 单 worker：模型是进程内单例，多 worker 会每个进程各加载一份模型、各占一份显存。
# 并发靠 app.py 里的后台线程 + 前端轮询，不需要多进程。
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
