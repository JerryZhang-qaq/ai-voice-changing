# Windows 原生首测指南

目标环境：Windows 10/11 x64、NVIDIA RTX 40 系或更新显卡、最新兼容驱动、Python 3.12 x64。首测包自带网页，不需要 WSL2、Docker 或 Node.js。当前云机完成的是 Linux CPU 和网页验证，下面的原生 GPU 安装及训练需要在目标电脑完成首次验收。

## 安装与启动

1. 解压到本地 NTFS 磁盘，例如 `D:\VoiceWorkbench`。建议剩余空间至少 25 GB，以容纳两个引擎环境、基础模型、训练特征和检查点。
2. 双击 `Install-Windows.cmd`。缺少的 Python 3.12、Git、FFmpeg 会通过 Windows Package Manager 安装；没有 `winget` 时安装 Microsoft App Installer，或按下面的手动命令安装依赖。NVIDIA 驱动需要事先可用。
3. 安装器为 API、RVC、分离引擎分别创建独立环境，40 系默认 Torch 2.7.1 / CUDA 11.8，50 系及后续默认 CUDA 12.8；最后执行实际 CUDA 矩阵运算检查。CUDA 工具包无需另装。
4. 双击 `Start-Windows.cmd`。程序启动 API 与工作进程，并在本机打开浏览器；保持命令窗口运行，结束时按 Ctrl+C。
5. 在“引擎与基础模型”页下载默认所选资源：RVC 分析模型、40k 底模、人声提取、和声检查。其他采样率、去混响和降噪按需下载。每个文件必须通过固定 SHA-256 校验才会启用。

网络需要能访问 GitHub、PyPI、PyTorch 官方下载站、Hugging Face 及实际重定向 CDN。已有文件可选择“仅校验已有文件”。损坏文件不会被覆盖，页面任务会指出文件位置。安装过程的真实依赖清单保存在 `runtime/*-installed.lock`。

手动安装（已经装好 Python 3.12、Git、FFmpeg）：

```powershell
py -3.12 scripts/install.py --engines
.venv\Scripts\python.exe scripts/launch.py
```

指定 CUDA 方案可用 `py -3.12 scripts/install.py --engines --cuda cu128`。显卡架构和驱动必须支持选定的 PyTorch；仅检测到显卡名称不会标记就绪，实际 CUDA 运算也必须成功。

## 从素材到成曲

1. 每份训练素材必须来自同一歌手独唱。上传时选择干声或带伴奏歌曲，选择“自动准备”并确认独唱。歌曲先提取人声；复杂和声命中后整份弃用，不尝试以剥离后的主唱代替。
2. 自动规则接受无风险切片。待复核片段可以试听、看波形、调整边界，保存新数据集版本。去混响和降噪默认关闭，启用后会执行处理损伤检查；不确定结果进入复核。
3. 在 RVC 页选择已接受数据集，使用 40k 底模开始训练。8 GB 显存首测建议批大小 1–2；显存充足后再提高。训练轮数应以试听结果调整，不能把执行检查的两轮模型当成合格音色。
4. 完成后模型和检索索引会自动登记。选择歌曲、音色及对应索引，调整变调、检索比例、辅音保护、响度包络、人声/伴奏增益和导出格式，提交一键翻唱。
5. 缓存页可以按任务、所选或全部预览并清理。处理中引用不会删除；源音频、接受数据、最终模型/索引和成曲受保护。训练检查点属于中间缓存，想继续训练请先保留它。

## 诊断与 GPU 执行检查

`Diagnose-Windows.cmd` 会保存 `runtime/diagnostics/doctor.json`，包括系统、工具、PyTorch、显卡/显存、模型校验及引擎状态；不收集凭据或环境变量值。

完成资源下载、关闭工作台后运行 `GPU-Test-Windows.cmd`，实际执行 RMVPE、HuBERT、两轮 RVC 训练、FAISS、音色转换和混音。它使用生成的信号，只检查能否执行，不能测量音质。结果写入 `runtime/diagnostics/gpu-smoke.json`，每个引擎任务的日志和失败检查点仍可在工作台查看。

安装失败保留命令窗口日志；任务失败查看“任务中心”的具体错误和引擎日志。显存不足降低批大小，分离可降低片段窗口/重叠；持续不稳定时先完成诊断。首测包不含训练素材和第三方权重。

## 两份真人测试数据集

安装完成后，可以双击 `Prepare-Test-Datasets-Windows.cmd`，或运行 `.venv\Scripts\python.exe scripts/benchmark-dataset.py --import-workbench`，自动获取固定版本的中文 Opencpop 和日文 NIT-SONG070-F001 样本，分别登记到工作台。它们分别使用同一语料歌手，彼此不混合；为了保持测试证据清晰，初始版本均待复核。数据保存在本地，中文处理后音频不随发行包分发。完整训练需要补充更长且音域/唱法覆盖充分的素材。
