# 工程架构

## 产品范围

单机 GPU 服务器部署，浏览器访问；先支持个人和小团队。统一 UI，同时允许分离、数据准备、训练、转换和混音单独使用。第一版使用 RVC 音色模型体系；分离引擎和具体权重通过对比验证后选定。

## 源码目录

```text
apps/web/                       React + TypeScript 界面
services/api/src/               FastAPI：资源管理、任务提交、音频试听、任务状态
services/worker/src/            任务编排、取消、中断登记、继续训练、GPU 状态
packages/contracts/            API、任务事件、模型包及数据集清单的版本化协议
packages/storage/src/voice_workbench_storage/
  store.py                     产物清单、引用保护、缓存清理、工作目录、运行状态
packages/dataset/src/voice_workbench_dataset/
  pipeline.py                  来源诊断、轻处理、切片清单与缓存
  segmentation.py              能量/停顿约束切片基础算法
  quality.py                   清洗前后信号检查、保守回退与处理证据
  duplicates.py                增益/极性容忍的近重复复核候选
  curation.py                  重复关系及来源分组、训练/验证划分与 ZIP 导出
packages/audio/src/voice_workbench_audio/
  io.py                        FFmpeg 解码、声道选择、基本信号诊断
  mix.py                       对齐检查、流式混音与导出
packages/engines/src/voice_workbench_engines/
  registry.py                  固定引擎版本和模型候选
  separation.py                分离工作流适配器
  separation_bridge.py         独立环境内的公开 API 调用
  rvc.py                       训练格式、特征检查、训练、索引、转换
  model_probe.py               受限权重解析与索引维度检查
  process.py / engine_launcher.py  进程取消、超时及 worker 死亡监护
configs/dataset/               经过验证的歌唱数据处理预设
deploy/                        镜像、Compose、GPU 检测与存储挂载
tests/dataset/                 数据处理的单元及音频回归测试
tests/integration/             任务恢复与完整流程测试
tests/fixtures/                小型可再分发测试音频及标注
scripts/                       开发和评测入口
docs/                          设计、模型来源、测试报告
```

当前文件布局对应已实现的基础能力。歌手身份识别不在范围内，污染规则与感知质量仍待校准；contracts、数据处理预设和可再分发真人音频 fixture 目录仍待完善，不把空目录作为实现。各组件只在需要时添加依赖声明，不预先编造版本锁文件。

## 服务边界

API 不直接执行重型推理。任务写入 SQLite，由 worker 按持久化状态领取；初期单 worker、单 GPU 重型任务并行数为一。当前 CPU 分析也顺序执行，未来引入有上限的并发；多 worker 时再引入相应队列和数据库。GPU 能力由 worker 写入带心跳的状态，API 不以自身容器的 GPU 能力决定 worker 是否可用。

引擎通过进程协议调用，支持独立虚拟环境或容器，避免分离和 RVC 的 Python、PyTorch、CUDA 依赖冲突。基础 GPU 镜像按实测的显卡代际选择；不能假设 40 系与所有更新显卡使用同一套二进制。

任务事件包括阶段、已完成工作量、日志引用、产物和失败原因。取消在安全边界生效。训练恢复依赖检查点；其他步骤按已完成产物恢复。任务重启后先校验产物，不能仅凭文件存在认定成功。

## 数据与产物

运行目录独立挂载：

```text
runtime/
  catalog.sqlite3              产物角色、任务、引用保护与 worker 心跳
  artifacts/歌手/<实际歌手>/
    原始素材/                  训练音源：原曲名__内部ID.ext
    转换素材/                  转换音源：原曲名__内部ID.ext
    切片数据集/<歌手>-after__版本ID/   音频切片与准备清单
    切片数据集/<歌手>-ready__版本ID/   审查清单，引用已保护切片
    处理中间文件/              工作母版、分离音轨与训练检查点
  artifacts/<可读名字>__<ID>.<suffix>  模型、索引、ZIP 与成品
  processing-<job_uuid>-*/     临时代理和分析文件，任务结束自动删除
  engines/rvc/                 固定版本的外部引擎（可配置位置）
  venvs/                      外部引擎独立依赖（可配置位置）
  models/separation/           外部分离权重（可配置位置）
```

产物身份由输入内容摘要、引擎及权重摘要、处理器版本、参数决定。缓存命中需校验完整性。来源、模型和数据集的删除采用引用检查，避免删掉其他任务仍使用的文件。

时间位置使用解码后的源采样点整数及采样率登记；重采样或剪裁记录映射。模型内部补齐、偏移和裁剪需在适配器层显式恢复，保证混音对齐。

## UI

当前有训练数据集、分离、RVC 训练、转换翻唱、混音、基础资源、任务、缓存八个入口。训练和转换素材使用用途字段隔离，分别由歌手目录管理。数据集页支持清洗 A/B、和声弃用证据、波形与边界编辑、人工纠正和新版本。自动模式依据独唱声明与实验规则准入，全部人工复核模式仍可使用。身份识别不在范围内；独立音质验收仍待完成。

## 外部资源

候选来源：RVC-Project/Retrieval-based-Voice-Conversion-WebUI、IAHispano/Applio、nomadkaraoke/python-audio-separator、ZFTurbo/Music-Source-Separation-Training。接入前固定 commit、验证调用协议、记录依赖和许可证，并逐一确认权重分发条件。初期不复制整个上游 UI，不宣称已支持任意 RVC 文件。

## 0.0.3 进度与升级

`JobProgress` 在执行位置和任务中心读取同一 SQLite 状态，页面切换与刷新后恢复。上传读取 XHR 字节进度；下载读取真实文件字节；切片、导出与删除读取实际数量。RoFormer tqdm 与特征提取计数经增量 UTF-8 日志解析；固定 RVC 模块训练循环在每个 batch 后通过导入钩子发送观测 JSON，rank 0 每秒和轮次结束最多采样一次，保留全部原训练运算。显存与利用率来自 nvidia-smi，估时来自实际已完成工作量变化；无法观测时保持未知。

`upgrade.py` 校验 Release 文件清单，在原目录取得 WorkerLock 后原子替换代码文件，保护 runtime 和环境。旧数据清理使用持久化 cutoff、待完成标记和最终收据，崩溃后可继续，再次升级不会删除新数据。用户主动删除可突破普通保留标记，但活动任务持有的输入仍不可删除。
