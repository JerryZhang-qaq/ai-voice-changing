# 声作坊 · AI 翻唱工作台

自托管的一站式 RVC 翻唱程序，目标为 Windows 10/11 原生运行和 NVIDIA RTX 40 系及更新显卡。源码 GPL-3.0-only，非商业开源项目；第三方权重按其各自许可使用。

0.0.3 为早期测试版，包含按歌手整夹导入、自动清洗切片、Y / N 审核、RVC 训练及可视化、独立转换素材、伴奏与和声下载、三轨混音，以及手动清理全部闲置缓存。界面采用明亮设计，每项任务在执行位置显示进度。版本说明见 [0.0.3 早期测试版](docs/releases/0.0.3.md)。

已有 0.0.1：关闭旧工作台，完整解压新 ZIP，运行新包中的 `Update-Windows.cmd`，选择原安装目录；更新后从原目录运行 `Start-Windows.cmd`。首次更新按要求清理旧音源、切片及数据集版本，保留已训练模型、索引、转换结果、成品、检查点、基础权重和依赖环境。重复更新不会清理新数据。

首次安装：运行 `Install-Windows.cmd`，选择解压目录；安装完成运行 `Start-Windows.cmd`，在“引擎与基础模型”页下载资源。首测 ZIP 自带网页，不需要 Node.js 或 WSL2。完整步骤见 [Windows 使用说明](docs/windows.md)。

真实 RoFormer 执行记录见 [测试报告](docs/benchmarks/README.md)。用户已在原生 Windows / RTX 5080 Laptop GPU 完成上一版训练；本版 CPU、界面与原生 Windows 检查不代替新 GPU 流程和听感验收。自动筛查仍需人工复核。

- [数据集处理逻辑](docs/dataset-pipeline.md)
- [清洗与重复检测边界](docs/quality-baseline.md)
- [引擎和权重](docs/engines.md)
- [工程架构](docs/architecture.md)
- [开发与 Docker](docs/development.md)
- [验证状态与后续工作](docs/progress.md)
- [第三方说明](THIRD_PARTY.md)

Linux 开发：Python 3.12、Node 22.12+ / 24、Git、FFmpeg/FFprobe，执行 `bash scripts/install.sh` 和 `bash scripts/start.sh`。测试执行 `.venv/bin/python -m pytest -q`。

程序默认只监听本机。普通缓存清理保护原始素材、接受的数据、音色模型和成品；手动永久删除允许用户主动选择这些文件，活动任务占用的文件仍受保护。处理中间件、未接受切片、分离音轨、特征、检查点与下载临时文件均纳入清理管理。权重、用户音频、虚拟环境和运行目录不进入源码仓库或程序 ZIP。
