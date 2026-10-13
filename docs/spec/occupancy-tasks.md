# 占道堆积/异常占用检测 — 任务清单

按序执行；每步完成后进行语法校验。

1. [x] `src/occupancy/` 引擎模块：`__init__.py`、`baseline_manager.py`、`diff_analyzer.py`、`occupancy_state.py`
2. [x] 单测：`tests/test_occupancy_diff.py`、`tests/test_occupancy_state.py`（合成帧验证：面积占比、归一化、多边形掩膜、全局突变、状态机全迁移路径、分类型区域筛选）
3. [x] `src/pipeline/occupancy_pipeline.py` 管线主循环
4. [x] `src/pipeline/pipeline_factory.py` 注册两类型
5. [x] `src/tasks/task_executor.py`：CameraWorker occupancy 分支 + init_shared_components 跳过检测器池 + start() 跳过检测器等待
6. [x] `src/events/tracked_object_processor.py`：dict 消息分支 + `_on_occupancy_message`（alert/recovery/global_change）
7. [x] `src/tasks/task_api.py`：zones 校验扩展 + capture-baseline + baseline-status
8. [x] 前端 `templates/tasks.html`（检测类型选项、区域弹窗基线拍摄/参数区/状态徽标）、`templates/events.html`（类型筛选）
9. [x] 全量验证：py_compile + pytest 引擎单测 + node --check
