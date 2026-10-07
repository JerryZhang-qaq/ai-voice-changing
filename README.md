# 声作坊 · AI 翻唱工作台

自托管的一站式 RVC 翻唱程序，目标为 Windows 10/11 原生运行和 NVIDIA RTX 40 系及更新显卡。源码 GPL-3.0-only，非商业开源项目；第三方权重按其各自许可使用。

0.0.4 为早期测试版，提供按歌手整夹导入、自动清洗切片、Y / N 审核、RVC 训练及可视化、独立转换素材、伴奏与和声下载、三轨混音及手动缓存清理。本版增加均衡 / 保真 / 快速分离模式、批量模型复用、整批与当前模型两级进度及实时日志。版本说明见 [0.0.4 早期测试版](docs/releases/0.0.4.md)。

所有歌曲先提取人声，再切片、逐片人工审查。复杂和声只提示相关片段，不能据此弃用整首；可选和声辅助检查默认关闭。已有旧版整曲弃用数据集可重新准备，完整干声缓存优先复用。

已有安装：关闭旧工作台，完整解压新 ZIP，运行新包中的 `Update-Windows.cmd`，选择**现在实际使用的安装目录**；更新后从安装日志指出的原目录运行 `Start-Windows.cmd`。0.0.4 保留所有现有素材、数据集、模型、缓存、基础权重和依赖环境，匹配固定版本的依赖跳过安装。

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
