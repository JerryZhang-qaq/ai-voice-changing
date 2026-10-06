# 外部引擎与验证状态

## 人声、和声与去混响

使用独立环境中的 `audio-separator==0.47.0`（PyPI 发布日期 2026-08-27）。已依据官方 Python API 编写进程桥接，不以手工滤波或自行训练的分离模型替代。

初始模型候选来自其官方模型目录：

| 用途 | 候选 |
| --- | --- |
| 人声/伴奏 | MelBand RoFormer Kim FT3 · unwa |
| 主唱/和声 | MelBand RoFormer Karaoke · aufr33/viperx |
| 温和去混响 | MelBand RoFormer Less Aggressive · anvuew |
| 降噪 | MelBand RoFormer Denoise · aufr33 |

这些是候选，不是本项目实测音质排名。Karaoke 模型的主唱/和声能力依赖素材，必须先得到人声再调用，不能将歌曲分离中的伴奏直接当作和声。不同权重的声部标签可能不同；桥接要求预期输出严格对应，否则失败。

每次运行记录实际引擎版本、权重与配置的 SHA-256、输入来源及参数。摘要用于追溯，不能冒充权重发布者提供的完整性签名。资源管理从官方发布源下载并核对固定大小与 SHA-256，使用正常 TLS 验证，禁止关闭验证。权重再分发许可待逐项核实，不随源码发布。

## RVC

上游：<https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI>

核对版本：`81eed5e8f68b6bed1789f682fe78cdd324495afc`。该版本使用 Python 3.12，提供 `infer/cli.py` 及 `train/` 脚本，40 系与 50 系分别有 CUDA 11.8/12.8 的上游安装方案。

本项目不使用上游 `train/preprocess.py` 的默认强制再切片、逐段幅度处理和高通；已审查片段直接生成训练所需的 `0_gt_wavs` 和 `1_16k_wavs`，后续调用 RMVPE、HuBERT、训练和 FAISS 索引脚本。

## 当前验证情况

四种模型均已下载、核对配置与完整 SHA-256，并用 audio-separator 0.47.0 / PyTorch 2.7.1 CPU 在中日真人女声短片段执行。模型按固定配置读取真实声部标签，使用浮点 WAV 保存，不查询运行时远程模型目录。记录见 `benchmarks/engine-report.json`。这证明所选模型可以实际执行，不能证明已经完成听感验收。

当前云机没有 NVIDIA GPU，因此 RVC 真实训练/转换、CUDA 性能、Windows 实机安装和 40/50 系兼容性仍待目标电脑验证。基础资源位于固定 Hugging Face revision；云环境的实际下载 CDN 访问仍被网络规则阻止，补充规则仅保存为草稿。Windows 用户的本地网络需分别诊断。

资源清单见 `packages/engines/src/voice_workbench_engines/resources.json`。首次默认选择 RVC 分析模型、40k 底模、人声和和声两个分离模型，约 2.41 GB；全部可选资源约 4.67 GB。引擎环境和训练缓存需要额外空间。
