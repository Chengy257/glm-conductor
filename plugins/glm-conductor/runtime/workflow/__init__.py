"""GLM Conductor v2.4 原生 Workflow 编译器包（Phase 1 工作包 P1-D，单元 U4）。

职责分置：
    persona   文本工人 persona 唯一权威来源（TEXT_WORKER_PERSONA）
              + 单节点 ask 指令构造（build_ask）；
    compiler  静态节点 DAG → 宿主 dynamic-workflow facade TypeScript 源
              （compile_workflow；确定性：同输入两次编译字节全等；
              ownership 冲突即抛）；
    adapter   薄 run-id 关联层（record_run / load_run；
              .glm-conductor/workflow-runs/ 下单一 JSON；零子代理状态
              镜像）。

本包只生成与登记，绝不启动任何 Workflow——执行是主会话的事。
公共面经此重导出（包可独立 import，sys.path 需含 plugins/glm-conductor）：

    from runtime.workflow import compile_workflow, record_run, load_run
"""

from runtime.workflow.persona import TEXT_WORKER_PERSONA, build_ask
from runtime.workflow.compiler import (
    NODE_RESULT_FIELDS, STATUS_UNION, WorkflowCompileError, compile_workflow,
)
from runtime.workflow.adapter import WorkflowRunError, load_run, record_run

__all__ = [
    "TEXT_WORKER_PERSONA",
    "build_ask",
    "WorkflowCompileError",
    "compile_workflow",
    "NODE_RESULT_FIELDS",
    "STATUS_UNION",
    "WorkflowRunError",
    "record_run",
    "load_run",
]
