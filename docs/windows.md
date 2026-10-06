# Windows 原生首测指南

目标环境：Windows 10/11 x64、NVIDIA RTX 40 系或更新显卡、最新兼容驱动、Python 3.12 x64。首测包自带网页，不需要 WSL2、Docker 或 Node.js。Linux CPU、网页流程和 GitHub Actions Windows CPU 检查已经通过；原生 GPU 安装及训练需要在目标电脑完成首次验收。

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

0.0.2 起，安装窗口会显示步骤和日志路径，失败后等待按键；完整安装日志保存到 `runtime/diagnostics/install-*.log`。如果使用 0.0.1 遇到双击闪退、路径被当成命令或乱码，升级到 0.0.2，或在解压目录的终端直接运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install-windows.ps1"`。

0.0.1 的分离引擎约束误写为 `onnxruntime-gpu==1.20.1`，会导致 `ResolutionImpossible`。将 `scripts/separation-constraints.txt` 这一行改为 `onnxruntime-gpu==1.20.2` 后重新运行安装脚本，保留已经安装的环境。

如果 0.0.1 的安装器提示 Python 已安装但没有可升级版本，随后以 `-1978335189` 退出，可刷新当前 PowerShell 的 PATH，直接调用已安装的 Python：

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") + ";" + $env:Path
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" .\scripts\install.py --engines
```

上面的路径适用于默认的当前用户安装位置。0.0.2 安装器将自动检测可用的 Python 3.12 x64 路径，重新检查已安装工具。

任务失败查看“任务中心”的具体错误和引擎日志。显存不足降低批大小，分离可降低片段窗口/重叠；持续不稳定时先完成诊断。首测包不含训练素材和第三方权重。

## 0.0.1：RMVPE 阶段退出 143，日志为空

Windows 虚拟环境的 `python.exe` 可能经转发进程启动真正的 Python 解释器。0.0.1 的监督进程直接比较父进程 PID，会将这个正常转发误判为 worker 退出，在 RMVPE 脚本运行前返回 143。修正后使用真实 worker 的进程句柄检查存活，并继续用 Win32 Job 管理子进程；日志新增启动、停止和退出码记录。

先在启动工作台的窗口按 Ctrl+C，等待工作台退出。然后在原来的程序目录运行下面的 PowerShell 命令，仅替换两个引擎源码文件，并保留备份。无需重新安装依赖，数据集和基础模型可继续使用。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $enginePatchBase = 'https://raw.githubusercontent.com/JerryZhang-qaq/ai-voice-changing/17dd3d5f325fbacb9d3dc2b04280364a7d4e81b4/packages/engines/src/voice_workbench_engines'
    $enginePatchDir = '.\packages\engines\src\voice_workbench_engines'
    $enginePatchFiles = @('engine_launcher.py', 'process.py')
    foreach ($enginePatchFile in $enginePatchFiles) {
        Invoke-WebRequest -UseBasicParsing -Uri "$enginePatchBase/$enginePatchFile" -OutFile "$enginePatchDir\$enginePatchFile.new"
    }
    foreach ($enginePatchFile in $enginePatchFiles) {
        if (-not (Test-Path "$enginePatchDir\$enginePatchFile.backup143")) {
            Copy-Item "$enginePatchDir\$enginePatchFile" "$enginePatchDir\$enginePatchFile.backup143"
        }
        Move-Item "$enginePatchDir\$enginePatchFile.new" "$enginePatchDir\$enginePatchFile" -Force
    }
}
.\.venv\Scripts\python.exe .\scripts\launch.py
```

重新打开 RVC 页，选择原来已审查的数据集，选择“从预训练权重开始”，再次提交训练。旧的失败任务保留作为记录，新任务会使用修正后的启动逻辑。实际 GPU 训练仍需在目标电脑验证。

## 0.0.1：训练入口循环导入，退出码 1

如果 RMVPE 与 HuBERT 已成功，但训练日志报 `ImportError: cannot import name 'utils' from partially initialized module 'train'`，原因是按文件路径执行 `train/train.py` 遮蔽了同名包。适配器已改用 `python -m train.train`。引擎子进程同时统一使用 UTF-8 和及时输出，解决中文日志乱码。

先用 Ctrl+C 停止工作台，在原程序目录执行以下覆盖命令。该修复适用于已应用上面进程监督修复的安装，保留现有数据集与基础模型，并备份要替换的两个文件。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $enginePatchBase = 'https://raw.githubusercontent.com/JerryZhang-qaq/ai-voice-changing/2cb4a49f7eee57fb80073a0b659ad32cd4371ed6/packages/engines/src/voice_workbench_engines'
    $enginePatchDir = '.\packages\engines\src\voice_workbench_engines'
    $enginePatchFiles = @('rvc.py', 'process.py')
    foreach ($enginePatchFile in $enginePatchFiles) {
        Invoke-WebRequest -UseBasicParsing -Uri "$enginePatchBase/$enginePatchFile" -OutFile "$enginePatchDir\$enginePatchFile.new"
    }
    foreach ($enginePatchFile in $enginePatchFiles) {
        if (-not (Test-Path "$enginePatchDir\$enginePatchFile.backup-import")) {
            Copy-Item "$enginePatchDir\$enginePatchFile" "$enginePatchDir\$enginePatchFile.backup-import"
        }
        Move-Item "$enginePatchDir\$enginePatchFile.new" "$enginePatchDir\$enginePatchFile" -Force
    }
}
```

执行无报错后运行 `.\.venv\Scripts\python.exe .\scripts\launch.py`，选择原来已审查的数据集，并从预训练权重重新提交训练。基础模型和依赖环境继续复用。

## 0.0.1：训练已运行，但没有导出最终模型

如果日志记录了真实轮次和 G/D 检查点保存，但推理权重导出报 `RuntimeError: Parent directory assets/weights does not exist`，在程序目录执行以下命令即可创建缺少的目录，工作台可保持运行：

```powershell
New-Item -ItemType Directory -Path '.\runtime\engines\rvc\assets\weights' -Force
```

然后进入 RVC 页并刷新，选择与失败任务相同的数据集版本和采样率，在“继续训练检查点”选择该任务的训练工作目录。新的总轮数应大于原任务的目标轮数，保存间隔设为 1。例如原任务目标为 200 轮，可设为 201 轮，保留原批大小；后台会复制现有检查点和特征，并从检查点恢复训练，随后导出模型和构建索引。保留该任务的检查点缓存直至恢复完成。

开发源码已在训练前自动创建这个输出目录，并提前报告目录创建失败；修正版 Release 仍待用户完成首测后发布。成功任务会登记模型与对应索引，在 RVC 页刷新后可直接选择，无需再手动导入；可以先转换一小段未参与训练的干声，试听后再测试完整翻唱。

## 两份真人测试数据集

安装完成后，可以双击 `Prepare-Test-Datasets-Windows.cmd`，或运行 `.venv\Scripts\python.exe scripts/benchmark-dataset.py --import-workbench`，自动获取固定版本的中文 Opencpop 和日文 NIT-SONG070-F001 样本，分别登记到工作台。它们分别使用同一语料歌手，彼此不混合；为了保持测试证据清晰，初始版本均待复核。数据保存在本地，中文处理后音频不随发行包分发。完整训练需要补充更长且音域/唱法覆盖充分的素材。
