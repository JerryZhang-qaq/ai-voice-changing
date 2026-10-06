# 声作坊 · AI 翻唱工作台

自托管的一站式 RVC 翻唱程序，目标为 Windows 10/11 原生运行和 NVIDIA RTX 40 系及更新显卡。源码 GPL-3.0-only，非商业开源项目；第三方权重按其各自许可使用。

0.0.1 为早期测试版，已具备素材上传、模型资源下载校验、人声提取、复杂和声筛查、去混响/降噪、歌唱切片、清洗损伤检查、重复检测、波形与边界编辑、数据集审查/导出、RVC 训练/继续训练、音色转换、一键翻唱、混音和缓存清理。版本说明见 [0.0.1 早期测试版](docs/releases/0.0.1.md)。

已验证 CPU 数据处理与网页完整操作，真实 RoFormer 模型执行记录见 [测试报告](docs/benchmarks/README.md)。Windows 安装入口、原生进程管理和 RVC GPU 调用已实现；当前云机器没有 NVIDIA GPU，Windows 实机安装和真实 RVC 训练仍待目标电脑验收。自动筛查属于 alpha 规则，不保证检出所有污染，不能以规则通过代替听感结论。

解压首测包后运行 `Install-Windows.cmd`，安装完成后运行 `Start-Windows.cmd`。首次启动在“引擎与基础模型”页下载所需资源。完整步骤见 [Windows 使用说明](docs/windows.md)。首测 ZIP 自带构建后的网页，不需要 Node.js 或 WSL2。

- [数据集处理逻辑](docs/dataset-pipeline.md)
- [清洗与重复检测边界](docs/quality-baseline.md)
- [引擎和权重](docs/engines.md)
- [工程架构](docs/architecture.md)
- [开发与 Docker](docs/development.md)
- [验证状态与后续工作](docs/progress.md)
- [第三方说明](THIRD_PARTY.md)

Linux 开发：Python 3.12、Node 22.12+ / 24、Git、FFmpeg/FFprobe，执行 `bash scripts/install.sh` 和 `bash scripts/start.sh`。测试执行 `.venv/bin/python -m pytest -q`。

程序默认只监听本机。原始素材、接受的数据、音色模型和成品受到缓存清理保护；处理中间件、未接受切片、分离音轨、特征、检查点与下载临时文件均纳入清理管理。权重、用户音频、虚拟环境和运行目录不进入源码仓库或程序 ZIP。
