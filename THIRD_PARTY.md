# 第三方来源

本项目自有代码使用 GPL-3.0-only，完整条款见 LICENSE。第三方软件保留各自许可证；本文件不替代上游许可证。当前未随源码附带分离或音色模型权重。

| 核心 | 来源 | 使用方式 |
| --- | --- | --- |
| RVC | https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI | 固定 commit 的独立依赖 checkout，以脚本接口调用；上游 MIT |
| audio-separator | https://github.com/nomadkaraoke/python-audio-separator | 独立 Python 环境，桥接其公开 API；上游 MIT |
| UVR 模型生态 | https://github.com/Anjok07/ultimatevocalremovergui | 分离权重候选的来源之一，不复制其 GUI |
| MSST 模型生态 | https://github.com/ZFTurbo/Music-Source-Separation-Training | RoFormer 等候选的训练/推理生态参考 |
| FFmpeg | https://ffmpeg.org | 外部程序调用；实际发行许可由所用编译版本决定 |

HuBERT/ContentVec、RMVPE、预训练 G/D 和各分离权重需要逐项登记模型来源、版本与许可；不能从训练或推理代码的 MIT 许可推断任意权重可再分发。未完成确认的权重不进入发行包。

前端与 API 等依赖由 package-lock.json、requirements.lock 登记。发行镜像和依赖副本时保留相应声明，并满足所用版本的许可证义务。

测试语料来源：中文 Opencpop（专业女歌手真人录音，官方许可正文 CC BY-NC-ND 4.0，仅本地非商业测试，不分发处理后音频）；日文 NIT-SONG070-F001（Nagoya Institute of Technology / HTS Working Group，CC BY 3.0，声明见 docs/benchmarks/NIT-SONG070-COPYING.txt）。文件从固定 Git revision 的公开样本获取并登记 SHA-256，来源见 docs/benchmarks/sources.json。程序包不含这些音频或训练模型。

随构建网页包含的 React、React DOM 和 Scheduler 的 MIT 许可原文见 `docs/licenses/web-dependencies.txt`。
