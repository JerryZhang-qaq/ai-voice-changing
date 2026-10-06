# 开发与部署

## CPU 开发

Python 3.12、Node 22.12+ / 24、Git 和 FFmpeg/FFprobe：

```bash
cd /workspace/ai-voice-changing
bash scripts/install.sh
bash scripts/start.sh
.venv/bin/python -m pytest -q
```

前端使用 `cd apps/web && npm run dev`，Vite 将 `/api` 转发到 API。生产构建用 `npm run build`。API 与单 worker 共享 SQLite 运行目录，独占锁防止两个 worker 同时领取任务。没有 GPU 时可上传干声、使用全部人工复核模式（关闭和声筛查）、编辑与导出数据、混音、下载基础资源。

跨平台基础安装也可以执行 `python3 scripts/install.py`，运行 `.venv/bin/python scripts/launch.py --no-browser`。打包后的网页存在时不需要 Node。

## GPU 引擎

```bash
python3 scripts/install.py --engines --cuda cu128
```

安装固定 RVC commit 和 audio-separator 0.47.0，分别创建独立环境并检查实际 CUDA 运算。RVC 使用 NumPy 1.26；分离环境使用 NumPy 2.2，不能混装。仅安装调用所需的 RVC CLI 核心依赖，排除上游 Gradio、实时 GUI 和其他分离后端。

默认自动发现 `runtime/venvs/rvc`、`runtime/venvs/separation`、`runtime/engines/rvc`、`runtime/models/separation`。需要迁移路径时使用 `.env.example` 中的变量；启动前在进程环境设置，程序不会自动加载 `.env`。

网页的资源管理负责基础权重下载、固定大小与 SHA-256 校验、安装及完整性记录。RVC 模型仓库 revision 和所有分离配置已固定在 `packages/engines/src/voice_workbench_engines/resources.json`。公共下载不需要个人 token。手工放入权重后用资源页“仅校验已有文件”，校验通过才启用。

Windows 原生步骤见 `windows.md`。当前 CPU 云机不能验证 CUDA、40/50 系显存表现或 Windows 实机行为。`scripts/doctor.py` 和 `scripts/gpu-smoke.py` 用于目标电脑实际验收。

## 真人音频与浏览器检查

```bash
.venv/bin/python scripts/benchmark-dataset.py
```

脚本下载固定摘要的中文和日文真人录音，并在独立运行目录生成两份数据集和报告。测试材料来源和许可见 `benchmarks/sources.json`，不混入程序发行包。

真实模型 CPU 检查用独立分离解释器执行 `scripts/benchmark-engines.py`，需指定输入、权重和输出目录，并将 packages/audio、dataset、engines、storage 的 src 路径加入 PYTHONPATH。这是显式测试例外；产品的 GPU 就绪门槛保持启用。

`python scripts/smoke-browser.py --audio <真实WAV>` 验证正在运行的 API/worker 和七个页面，需要 Playwright 与 Chromium。当前云机使用已有 `/usr/bin/chromium`，不依赖被网络拦截的浏览器 CDN。

## Docker

保留 `deploy/Dockerfile`、`deploy/compose.yaml` 和 `scripts/smoke-docker.py`。此前 CPU 镜像和两个容器的联合流程通过；本次新增模块尚未重建镜像，GPU 容器仍待验收。

```bash
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm worker bash scripts/install-engines.sh cu128
docker compose -f deploy/compose.yaml up -d
```

API 与 worker 使用同一运行目录；只让 worker 使用 GPU。引擎环境需要在相应运行系统安装，不能复用 Windows 虚拟环境到 Linux 容器。默认仅本机监听；公开部署需要另配 HTTPS 与访问认证。首测版目标为本机使用，没有账号系统。

## 发行包

`python scripts/package-release.py` 收集源码、测试、文档、Windows 入口和已经构建的网页，生成首测 ZIP 及 SHA-256。排除 Git、运行数据、音频、第三方权重、虚拟环境、下载缓存及 node_modules。GitHub Actions 提供 Linux 和 Windows CPU 检查配置，只有发布代码并实际运行后才算 CI 验证。
