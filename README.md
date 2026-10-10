<h1 align="center">
  <p><img src="docs/images/readme_header.jpg" alt="logo"></p>
</h1>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/Docker-Support-blue?logo=docker" alt="Docker Support">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20CUDA%2012.4-76B900?logo=nvidia" alt="CUDA">
</p>

<p align="center">
  <b>AI-Media2Doc-Local</b> —— 把视频/音频一键转成图文文档，<b>全程跑在你自己机器上</b>。
</p>

---

## 这是什么

这是 [`hanshuaikang/AI-Media2Doc`](https://github.com/hanshuaikang/AI-Media2Doc) v0.8.0 的**本地化改造版**。

上游把语音识别和文件存储托管在字节跳动的云服务上（火山引擎 AUC + TOS）。这个版本把它们**全部搬回本机**：

| 能力 | 上游 | 本版本 |
|---|---|---|
| **语音识别** | 火山引擎 AUC（云端） | **faster-whisper-large-v3**（本机 GPU） |
| **文件存储** | 火山引擎 TOS（云端） | **RustFS**（本机容器，S3 兼容） |
| **大模型** | 火山方舟 | **任意 OpenAI 兼容端点**（默认 DeepSeek） |

**音频和视频不会离开你的机器。** 唯一的出网请求是调用大模型生成文档 —— 想彻底断网的话，换成本地 Ollama 即可。

---

## 它和上游的差别（一句话版）

- ✅ **ASR 在本机 GPU 上跑** —— 不需要联网、不需要 API key、没有时长限制
- ✅ **文件存本机** —— 音视频与转写结果都在你的硬盘上
- ✅ **转写结果可复用** —— 同一个视频再次处理时跳到 AI 总结，不用重新过 GPU
- ✅ **桶自动清理** —— 按对象寿命回收，不会无限膨胀
- ➕ **调试面板** —— 右下角悬浮球，一键复制请求日志
- ⚠️ **需要 NVIDIA GPU** —— 这是唯一的硬件门槛

完整改动清单见 [`docs/改动清单-本地化改造.md`](docs/改动清单-本地化改造.md)。

---

## 硬件要求

| 项 | 要求 |
|---|---|
| **GPU** | NVIDIA，显存 **6 GB 以上可用**（large-v3 用 float16 约占 3-4 GB） |
| **驱动** | 需支持 **CUDA 12.4** |
| **磁盘** | 约 10 GB（模型 3 GB + 镜像若干 + 你的音视频） |
| **内存** | 建议 16 GB |
| **系统** | Windows 10/11（Docker Desktop + WSL2）或 Linux |

> 没有 NVIDIA GPU？ASR 容器会起不来。可以考虑把 `config/docker-compose.yaml` 里 `asr` 服务的 `deploy.resources` 段去掉，改用 CPU 推理 —— 但速度会慢一个数量级，本 README 未覆盖这条路。

---

## 快速开始

### 前置：装好 Docker Desktop

Windows 用户注意两点：
1. **启用 WSL2 后端**（Settings → General）
2. **确认 GPU 可用**：装好最新 NVIDIA 驱动后，执行 `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` 应该能打印出显卡信息

### 第 1 步：下载模型

需要一个 **faster-whisper-large-v3** 模型（约 2.94 GB）。选一条路：

**路线 A：HuggingFace**（有国际网络）

```powershell
pip install -U "huggingface_hub[cli]"
huggingface-cli download Systran/faster-whisper-large-v3 --local-dir D:\models\faster-whisper-large-v3
```

**路线 B：ModelScope**（国内更快）

```powershell
pip install -U modelscope
modelscope download --model AI-ModelScope/faster-whisper-large-v3 --local_dir D:\models\faster-whisper-large-v3
```

> **为什么是 Systran 这个名字**：`Systran/faster-whisper-*` 是 faster-whisper 作者维护的**格式转换版**，已经把 OpenAI 原始权重转成了 CTranslate2 格式。
> **不要**下 OpenAI 的原始 `openai/whisper-large-v3` —— 那是 PyTorch 格式，faster-whisper 读不了。

下载完检查目录里有这些文件：

```
model.bin          ← 约 2.94 GB，必需
config.json
tokenizer.json
vocabulary.json
```

### 第 2 步：配置

复制模板到仓库**外面**（这样凭据永远不进仓库）：

```powershell
Copy-Item config\.env.example ..\..\.secrets\media2doc.env
notepad ..\..\.secrets\media2doc.env
```

至少要改 **4 项**：

```ini
WHISPER_MODEL_HOST=D:\models\faster-whisper-large-v3    # 第 1 步下的目录

RUSTFS_ACCESS_KEY=<20 位大写字母+数字>                   # 自己生成，见下
RUSTFS_SECRET_KEY=<48 位随机串>

LLM_API_KEY=sk-xxxxxxxx                                  # 你的大模型 key
```

生成 S3 凭据（PowerShell 直接跑）：

```powershell
-join ((65..90)+(48..57) | Get-Random -Count 20 | ForEach-Object {[char]$_})
-join ((1..48) | ForEach-Object {[char]((65..90)+(97..122)+(48..57) | Get-Random)})
```

> ⚠️ **Access Key 里不能有斜杠 `/`** —— SigV4 会把 AK 嵌进签名串，含斜杠会解析错。
> ⚠️ RustFS 出厂默认是 `rustfsadmin`/`rustfsadmin`，**是公开的**，必须换掉。

`LLM_API_KEY` 用哪家的都行。默认配置指向 DeepSeek：

```ini
LLM_BASE_URL=https://api.deepseek.com/v1      # 注意要带 /v1
MODEL_ID=deepseek-chat
LLM_API_KEY=sk-...
```

### 第 3 步：环境自检

```powershell
python scripts\doctor.py
```

它会检查 8 项：Docker / GPU / 配置 / 凭据 / compose / 模型文件 / 运行状态 / 桶与 CORS。

**在起服务之前，前 6 项应该都是绿的。** 有红的先解决。

### 第 4 步：启动

```powershell
m2d.bat up
```

没有 bat（Linux 或不用脚本），等价命令是：

```bash
docker compose --env-file ../../.secrets/media2doc.env -f config/docker-compose.yaml up -d --build
```

首次启动会构建镜像（约 5-10 分钟）并拉 RustFS 镜像。

### 第 5 步：初始化桶（**必做**）

```powershell
python scripts\init_rustfs.py
```

这一步做两件事：**建桶** + **配置 CORS**。

> ⚠️ **不跑这一步，上传会失败**，而且症状很有迷惑性：浏览器显示「提取音频时网络错误」，但后端日志完全正常（因为请求根本没到后端）。原因是 RustFS 默认不返回 CORS 头，浏览器把跨源的 PUT 请求拦掉了。

> ⚠️ CORS 配置存在数据卷里。**换新卷或清库后必须重跑本脚本。**

### 第 6 步：打开

浏览器访问 **http://localhost:5173**

---

## 日常使用

### 用 `m2d.bat`（Windows 推荐）

双击就行，菜单式：

```
[1] 启动全部服务          [6] 查看当前配置
[2] 重建并重启            [7] 填 API key
[3] 停止全部服务          [8] 初始化桶（建桶 + CORS）
[4] 查看日志              [9] 环境自检
[5] 查看状态
```

**它会自动找到你的凭据文件** —— 不用每次传 `--env-file`。

### 手动命令

```powershell
# 启动 / 停止
docker compose --env-file ..\..\.secrets\media2doc.env -f config\docker-compose.yaml up -d
docker compose --env-file ..\..\.secrets\media2doc.env -f config\docker-compose.yaml down

# 看日志
docker compose --env-file ..\..\.secrets\media2doc.env -f config\docker-compose.yaml logs -f backend

# 改了 .env 后让配置生效（必须重建，环境变量是创建容器时注入的）
docker compose --env-file ..\..\.secrets\media2doc.env -f config\docker-compose.yaml up -d --force-recreate backend asr
```

> ⚠️ **`docker compose` 必须在 `config\` 目录下跑**，或者用 `-f config\docker-compose.yaml` 指定。
> 仓库**根目录**也有一份 `docker-compose.yaml`，那是**上游的原版**（连着火山云），**不要用**。跑错了会得到一堆和本 README 对不上的报错。

---

## 服务与端口

| 服务 | 端口 | 说明 |
|---|---|---|
| **frontend** | `5173` | Web 界面，你访问这个 |
| **backend** | `8080` | API，签发上传 URL、转发 ASR、调用大模型 |
| **asr** | `127.0.0.1:8001` | 本地语音识别（**只绑本机**） |
| **rustfs** | `127.0.0.1:9000` | S3 API（**只绑本机**） |
| | `127.0.0.1:9001` | RustFS 控制台（**只绑本机**） |

asr 和 rustfs **只监听 127.0.0.1**，不对局域网暴露。

---

## 数据流（理解这个能省很多排查时间）

```
① 你在浏览器选一个视频
      ↓
② 浏览器用 ffmpeg.wasm 抽出音轨（这一步在你电脑的浏览器里，不用装 ffmpeg）
      ↓
③ 浏览器向后端要一个上传地址 → 后端签发 presigned URL
      ↓
④ 浏览器直接 PUT 到 RustFS（★ 跨源，所以必须配 CORS）
      ↓
⑤ 后端让 ASR 服务转写 → ASR 从 RustFS 取音频 → GPU 跑 whisper
      ↓
⑥ 转写结果写回 RustFS（transcripts/<md5>.json，供下次复用）
      ↓
⑦ 后端把转写文本 + 你的提示词发给大模型 → 生成 Markdown
      ↓
⑧ 浏览器展示，可导出
```

### 三个最容易踩的坑

**1. 「提取音频时网络错误」= CORS 没配**

不是后端坏了。跑 `python scripts\init_rustfs.py` 即可。

⚠️ **用 curl / boto3 测 PUT 会返回 200，让你误判「上传正常」** —— 命令行工具不做 CORS 检查。必须用浏览器，或用带 `Origin` 头的请求测 OPTIONS 预检。

**2. 改了 `.env` 但没生效**

环境变量是**创建容器时**注入的。改 `.env` 后必须重建：

```powershell
docker compose ... up -d --force-recreate backend asr
```

**3. 时间戳差 1000 倍**

转写结果的 `start_time`/`end_time` 单位是**毫秒**，不是秒。这是前端契约，改动时别搞错。

---

## 转写缓存与清理

### 缓存怎么用

同一个视频再次处理时，系统按 **MD5** 找缓存：

```
① 查 RustFS 桶          ← 跨机器有效
     ↓ 没找到
② 查浏览器 IndexedDB     ← 本机历史记录
     ↓ 没找到
③ 完整跑一遍（上传 → ASR → 大模型）
```

命中 ① 或 ② 时**跳过 ASR**，直接进大模型环节。

缓存内容是**带毫秒时间戳的分段数组**（不是纯文本），因为下游要生成 `[00:23-00:28]` 这样的分段。

### 自动清理

桶不会无限膨胀。`RETENTION_DAYS`（默认 `1`）控制产物寿命：

| 值 | 行为 |
|---|---|
| `1` | 默认。超过 1 天没被碰过的产物自动删除 |
| `0` | **关闭清理**，桶只增不减 |
| 调大 | 省空间，但重跑老视频要重新过 GPU |

> 判定依据是 **S3 侧的最后修改时间**。改宿主机文件的 mtime **不会**影响它。

---

## 调试面板

出问题时点右下角的 **🐞 悬浮球**，会展开一个面板，采集四类信息：

| 类型 | 采集什么 |
|---|---|
| **网络请求** | `fetch` + `XMLHttpRequest`（★ **CORS 失败只在这层看得见**） |
| **控制台** | `console.log / info / warn / error` |
| **异常** | `window.onerror` + 未处理的 Promise 拒绝 |

面板支持按类型筛选、**一键复制全部**、下载为 txt。

当 XHR 失败且目标是 S3 端点时，面板会直接给出诊断提示（比如「桶的 CORS 未配置，跑 `init_rustfs.py`」），不用自己猜。

---

## 常见问题

**Q：能不能不用 GPU？**

可以改成 CPU 推理，但 large-v3 在 CPU 上转写一小时视频可能要几十分钟甚至更久。改法：编辑 `config/docker-compose.yaml`，删掉 `asr` 服务的 `deploy.resources` 那一段，并把模型计算类型改成 `int8`。本 README 没有实测这条路。

**Q：要装 ffmpeg 吗？**

**不用。** 抽音轨用浏览器里的 ffmpeg.wasm；ASR 容器内自带 ffmpeg（给 whisper 解码用）。

**Q：能不能完全离线？**

只差大模型这一步。把 `LLM_BASE_URL` 指向本机 Ollama 即可：

```ini
LLM_BASE_URL=http://host.docker.internal:11434/v1
MODEL_ID=qwen2.5:7b
LLM_API_KEY=ollama        # Ollama 不校验，随便填
```

但要给 `backend` 服务加一行才能访问宿主机：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**Q：为什么桶不能去掉，直接把文件传给后端？**

上游的设计是**浏览器直传对象存储**（presigned PUT），后端不经手文件字节。要去掉桶就得改前端上传逻辑，改动面比现在大得多。现在桶的角色很单纯：**给浏览器一个上传落点**。

**Q：显存不够怎么办？**

默认用的是 `float16`，large-v3 约占 3-4 GB。如果还是不够：
- 改用 `faster-whisper-large-v3-turbo`（更快更小）
- 或改 `int8` 计算类型（精度略降）

改 `asr/app.py` 里 `WhisperModel(...)` 的参数。

**Q：支持多个人同时用吗？**

单机跑没问题，但 **ASR 的任务表存在进程内存里**，只支持单 worker。要水平扩展得把任务表换成 Redis。

---

## 目录结构

```
AI-Media2Doc-Local/
├── m2d.bat                      Windows 一键菜单
├── backend/                     后端（FastAPI）
│   └── routers/audio.py         ★ 改造过：转发到本地 ASR
├── frontend/                    前端（Vue 3 + Vite）
│   └── src/
│       ├── apis/transcriptService.ts   ★ 新增：转写缓存 API
│       ├── utils/debugLog.js           ★ 新增：调试采集
│       └── components/DebugPanel.vue   ★ 新增：调试面板
├── asr/                         ★ 新增：本地 ASR 服务
│   ├── app.py                   FastAPI + faster-whisper
│   └── Dockerfile               CUDA 12.4 基础镜像
├── config/
│   ├── docker-compose.yaml      ★ 本项目用的编排（四服务）
│   └── .env.example             ★ 配置模板
├── scripts/
│   ├── doctor.py                环境自检
│   ├── init_rustfs.py           建桶 + 配 CORS
│   ├── set_env.py               安全写入 env
│   ├── env_info.py              显示当前配置（自动打码）
│   ├── check_llm.py             验证 LLM key
│   └── verify_rustfs_s3.py      验证 S3 能力
├── docs/
│   ├── 改动清单-本地化改造.md    ★ 相对上游改了什么
│   └── internal/                开发过程记录（可忽略）
└── docker-compose.yaml          ⚠️ 上游原版，别用
```

---

## 许可证

本项目沿用上游的 **MIT License**，见 [`LICENSE`](LICENSE)。

上游项目：[hanshuaikang/AI-Media2Doc](https://github.com/hanshuaikang/AI-Media2Doc) © 2025

本仓库是它的修改版，改动内容已在 [`docs/改动清单-本地化改造.md`](docs/改动清单-本地化改造.md) 中逐项列出。

使用的第三方组件：
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) —— MIT
- [CTranslate2](https://github.com/OpenNMT/CTranslate2) —— MIT
- [RustFS](https://rustfs.com/) —— Apache-2.0
---

## 免责声明

本项目仅供个人学习与研究使用。转录内容与生成的文档由 AI 自动产生，可能存在错误，请自行核实。

请遵守你所在地区的法律法规，尊重音视频内容的版权。