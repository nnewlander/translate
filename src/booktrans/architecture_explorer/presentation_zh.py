from __future__ import annotations

from typing import Any

from .generator import ExplorerInputError


_EVIDENCE_GUIDES = {
    "worker_script_binding": {
        "group": "supervisor",
        "name": "绑定 Worker 启动脚本",
        "explanation": "Supervisor 把 Python 包装脚本的仓库内路径保存为确定的启动目标。这里证明的是脚本指向关系，不代表脚本已经运行。",
        "supports": "支持“Supervisor 知道应启动哪个 Worker 包装入口”。",
    },
    "production_argument_vector": {
        "group": "supervisor",
        "name": "组装生产启动参数",
        "explanation": "Supervisor 组装传给子进程的参数，其中明确选择 production 模式、携带生产写入授权开关，并绑定当前批次与 Checkpoint。",
        "supports": "支持“启动请求携带了已识别的生产模式、授权、批次和 Checkpoint 参数”。",
    },
    "process_boundary_definition": {
        "group": "supervisor",
        "name": "定义独立进程启动边界",
        "explanation": "该 PowerShell 函数构造子进程启动信息、调用 Start，并等待子进程退出。静态源码只能证明这条启动路径存在。",
        "supports": "支持“Supervisor 具备创建并管理独立 Worker 进程的代码边界”。",
    },
    "process_boundary_invocation": {
        "group": "supervisor",
        "name": "调用 Worker 进程启动函数",
        "explanation": "Supervisor 把 Python 解释器、前面组装的参数和工作目录交给启动函数。",
        "supports": "支持“生产参数向独立 Worker 进程启动边界传递”。",
    },
    "worker_wrapper_import": {
        "group": "wrapper",
        "name": "导入 Worker 主入口",
        "explanation": "轻量 Python 包装脚本从生产 Worker 模块导入准确的 main 函数。",
        "supports": "支持“Supervisor 指向的包装脚本连接到生产 Worker 主入口”。",
    },
    "worker_wrapper_main_call": {
        "group": "wrapper",
        "name": "在脚本入口调用 Worker main",
        "explanation": "包装脚本仅在作为主程序运行时调用导入的 main，并把退出结果交回操作系统。",
        "supports": "支持“独立启动包装脚本会进入 Worker main”。",
    },
    "worker_mode_contract": {
        "group": "worker",
        "name": "限定 Worker 运行模式",
        "explanation": "Worker 参数解析器只接受 canary 或 production 两种模式，默认值是 canary。",
        "supports": "支持“production 是 Worker 明确定义的模式，而不是文件名推测”。",
    },
    "worker_authorization_contract": {
        "group": "worker",
        "name": "声明生产写入授权参数",
        "explanation": "Worker 参数解析器声明了生产 Checkpoint 写入授权开关。这里仅证明参数契约存在。",
        "supports": "支持“Supervisor 传入的授权开关能被 Worker 正式解析”。",
    },
    "worker_argument_parse": {
        "group": "worker",
        "name": "解析启动参数",
        "explanation": "Worker main 使用统一参数解析器读取传入参数，后续守卫和生产入口都使用这份解析结果。",
        "supports": "支持“启动参数进入 Worker main 的控制流程”。",
    },
    "worker_canary_exit_guard": {
        "group": "worker",
        "name": "先隔离 Canary 分支",
        "explanation": "当模式是 canary 时，Worker 执行 Canary 路径并在此返回，不继续进入后面的生产守卫序列。",
        "supports": "支持“生产入口位于 Canary 分支退出之后”。",
    },
    "worker_id_guard": {
        "group": "worker",
        "name": "要求 Worker 标识",
        "explanation": "进入生产路径前，Worker 检查 worker-id；缺失时立即停止。",
        "supports": "支持“生产调用受 worker-id 必填条件约束”。",
    },
    "worker_batch_checkpoint_guard": {
        "group": "worker",
        "name": "校验批次与 Checkpoint 成对绑定",
        "explanation": "Worker 要求 batch-label 与 checkpoint 同时提供或同时缺失，不接受只提供其中一个的启动绑定。",
        "supports": "支持“生产调用前存在批次/Checkpoint 配对守卫”。",
    },
    "worker_production_branch": {
        "group": "worker",
        "name": "调用生产 Checkpoint 入口",
        "explanation": "通过前述分支和守卫后，Worker main 把解析后的参数传给 run_production_checkpoint。",
        "supports": "支持“这条静态调用链最终可到达生产 Checkpoint 函数调用”。",
    },
    "auxiliary_launch_boundary_test": {
        "group": "auxiliary",
        "name": "辅助测试：子进程启动边界",
        "explanation": "测试检查受控场景中的子进程退出和创建诊断，只作为启动边界的支持性上下文。",
        "supports": "辅助支持启动边界测试覆盖；不独立证明生产调用发生。",
    },
    "auxiliary_wrapper_bootstrap_test": {
        "group": "auxiliary",
        "name": "辅助测试：包装入口引导",
        "explanation": "测试覆盖包装入口导入并到达预检的行为，只用于补充理解入口连接。",
        "supports": "辅助支持包装入口测试覆盖；不独立证明生产调用发生。",
    },
    "auxiliary_worker_binding_test": {
        "group": "auxiliary",
        "name": "辅助测试：Worker 包绑定边界",
        "explanation": "测试确认生产 Worker 包的既定绑定边界，只作为支持性上下文。",
        "supports": "辅助支持 Worker 绑定约束；不独立证明生产调用发生。",
    },
}

_GROUPS = [
    {
        "id": "supervisor",
        "title": "1. Supervisor：准备并启动独立进程",
        "description": "从确定 Worker 脚本，到组装生产参数，再到定义和调用进程启动边界。",
        "kinds": [
            "worker_script_binding", "production_argument_vector",
            "process_boundary_definition", "process_boundary_invocation",
        ],
    },
    {
        "id": "wrapper",
        "title": "2. Python 包装入口：连接到 Worker main",
        "description": "包装脚本导入准确的生产 Worker 主入口，并在独立执行时调用它。",
        "kinds": ["worker_wrapper_import", "worker_wrapper_main_call"],
    },
    {
        "id": "worker",
        "title": "3. Worker：解析、守卫并进入生产调用",
        "description": "Worker 识别生产模式和授权参数，解析输入，隔离 Canary，并经过必要守卫后调用生产 Checkpoint 入口。",
        "kinds": [
            "worker_mode_contract", "worker_authorization_contract", "worker_argument_parse",
            "worker_canary_exit_guard", "worker_id_guard", "worker_batch_checkpoint_guard",
            "worker_production_branch",
        ],
    },
]


def build_relationship_presentation_zh(
    modules: list[dict[str, Any]],
    relationship: dict[str, Any],
    candidate_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build Chinese guidance strictly keyed to the already-generated evidence facts."""

    module_names = {item["id"]: item["name"] for item in modules}
    production = relationship["evidence"]
    production_by_kind = {item["evidence_kind"]: item for item in production}
    expected_kinds = [kind for group in _GROUPS for kind in group["kinds"]]
    if set(production_by_kind) != set(expected_kinds) or len(production_by_kind) != len(production):
        raise ExplorerInputError("T3 Chinese presentation does not exactly cover the production evidence chain")
    auxiliary = [item for item in candidate_evidence if item.get("source_role") == "auxiliary_test"]
    if any(item.get("evidence_kind") not in _EVIDENCE_GUIDES for item in auxiliary):
        raise ExplorerInputError("T3 Chinese presentation is missing an auxiliary evidence guide")

    evidence_items = {
        item["evidence_id"]: {**_EVIDENCE_GUIDES[item["evidence_kind"]]}
        for item in [*production, *auxiliary]
    }
    groups = []
    for group in _GROUPS:
        groups.append(
            {
                "id": group["id"],
                "title": group["title"],
                "description": group["description"],
                "evidence_ids": [production_by_kind[kind]["evidence_id"] for kind in group["kinds"]],
            }
        )
    return {
        "source_module_name": module_names[relationship["source_module"]],
        "target_module_name": module_names[relationship["target_module"]],
        "relationship_name": "进程启动",
        "summary": "Supervisor 在满足指定条件时，可以启动独立的 Python Worker 进程，并传递生产参数。",
        "status_explanations": {
            "current-reviewed": "当前已审核：Owner 已批准这份固定源码快照中的关系，且当前相关生产源码与审核依据精确一致。",
            "STATIC_CONDITIONAL": "静态条件可达：从源码结构可以确认这条调用路径在满足列出的模式、参数和守卫条件时可到达；这不是一次实际运行记录。",
            "NOT_COLLECTED": "真实运行状态未采集：Explorer 没有读取运行日志或监控数据，因此不知道某次启动是否真实发生或是否成功。",
        },
        "limits": [
            "静态源码证据不能证明某一次 Worker 进程已经实际启动成功。",
            "静态源码证据不能证明翻译任务已经成功完成。",
            "静态源码证据不能证明运行时凭据、外部服务或其他前置条件可用。",
        ],
        "evidence_groups": groups,
        "evidence_items": evidence_items,
        "static_conditions": [
            "Supervisor 明确选择 production 模式。",
            "Supervisor 传入生产 Checkpoint 写入授权开关。",
            "Supervisor 从当前重建状态绑定批次和 Checkpoint。",
            "Python 包装脚本导入准确的 Worker main，并只在主程序入口调用它。",
            "Worker main 通过统一解析器读取启动参数。",
            "Worker 的 Canary 分支在生产守卫序列之前返回。",
            "Worker 依次要求 worker-id 和成对的批次/Checkpoint，之后才调用生产 Checkpoint 入口。",
        ],
        "retained_unknowns": [
            "无法从静态扫描证明 Supervisor 在某次实际运行中走到了这条启动路径。",
            "无法从静态扫描证明运行时前置条件、凭据或外部服务可用。",
            "除源码中直接观察到的名称、字面值和绑定外，参数的业务含义未经过本次审核。",
        ],
        "auxiliary_evidence_ids": [item["evidence_id"] for item in auxiliary],
    }


__all__ = ["build_relationship_presentation_zh"]
