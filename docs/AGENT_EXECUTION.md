# Agent 执行能力与边界

本轮把协作计划、人工审核和检查点接到实际执行路径，并替换了内置 `workflow.run` 的模拟输出。适用于多智能体协作、项目入门和架构治理；PR 审查继续使用其独立流程。

## 已修复的执行缺口

| 原行为 | 现在的行为 |
| --- | --- |
| Planner 输出文字，图仍固定执行全部 Worker | 严格校验 JSON 计划，按指定顺序执行选中的 Agent。 |
| 人工审核仅记录 pending，Reporter 继续执行 | LangGraph interrupt 持久化暂停；通过后才生成报告，拒绝则停止。 |
| Supervisor 文字判断难以约束风险 | 确定性风险门控决定审核要求，模型意见不能取消规则要求。 |
| 协作失败后缺少持久化续跑 | 协作、入门和治理场景按保存的图类型和输入恢复。 |
| workflow.run 拼接节点文字 | 使用真实编译器执行节点、边和条件分支，传播失败。 |
| 未知 Agent 返回成功文本 | 不支持的 Agent 类型明确报错。 |
| 待审核内容容易被当作最终结果 | final_report 为空，另提供标注的 draft_report；禁止导出草稿。 |

## 可执行计划

```json
{
  "steps": [
    {"agent": "project_analyzer", "objective": "分析项目结构"},
    {"agent": "code_reviewer", "objective": "检查代码风险"}
  ]
}
```

允许 `project_analyzer`、`code_reviewer`、`rag_processor`，共 1–3 步，不允许重复或额外字段。自定义目标可选择子集；入门和治理场景要求三类证据齐全。模型未配置、输出非法或业务能力覆盖不足时，使用确定性计划。`planning.source`、`trace_id`、`fallback_reason` 记录来源。

步骤目标用于说明意图；各 Worker 仍按自己的项目扫描能力执行，不支持由步骤目标动态生成任意工具调用。可选择串行或并行；并行只用于三个相互独立的项目分析 Worker，Supervisor 在它们汇合后运行。成功分支的检查点会保留，另一个分支失败后恢复不会重跑成功分支。

## 风险门控和报告

- high / critical 问题、评分低于 70、治理建议要求确认，均触发审核；关闭人工审核开关也不能取消这些规则。
- 用户开启人工审核时始终暂停。
- 未运行代码审查时风险为 unknown，报告写明未执行；缺少检查不等于项目安全。
- 证据保留 evidence_id、文件、行号、严重程度、类别和问题摘要，兼容扫描器的 path / file 字段。
- 报告始终附加执行范围与规范证据，优先列出最高风险的 30 项；完整扫描结果保存在检查点。
- report_generation 记录报告来源、调用追踪和 fallback 状态。未配置模型时可完成规则扫描和模板报告，但不能视为验证了模型推理质量。
- 协作 RAG 只生成切片，**不自动入库**；可视工作流的 RAG 加工节点和知识管理接口仍可显式入库。

## 审核与恢复

1. 在工作台选择“多智能体”，或调用 `POST /api/v1/tasks/collaborate`。旧的 `/api/v1/agents/collaborate` 也创建持久化任务并返回 task_id、status。
2. waiting_review 时读取详情、事件和草稿，Reporter 尚未执行。
3. `POST /api/v1/tasks/{task_id}/approve` 从暂停处继续；reject 结束为 cancelled；revise 记录意见并保持等待。请求体可带 `{"comment":"审核意见"}`。
4. revise 不会自动重规划或重跑；“深入分析”可建立后续分析任务。
5. 失败且存在检查点时，任务详情和历史页显示“从检查点继续”，调用 `POST /api/v1/tasks/{task_id}/recover`。恢复使用原始输入；先解决路径不可用等故障，再恢复。

协作任务重复同一终态审核决定是幂等的，矛盾决定返回 409；recover 不能绕过等待中的审核。执行租约阻止同一任务同时恢复。入门和治理恢复后继续生成业务产物。

普通恢复不会重跑已完成的图节点；进程在节点执行中崩溃时，该节点可能重新执行。重启不会自动调度遗留任务，需在租约过期后调用恢复接口。旧版没有检查点的协作任务无法补造执行状态。工具副作用不确定时沿用操作日志和人工核对流程，不承诺外部调用恰好执行一次。

## 内置 workflow.run

接收 nodes、edges、project_path、max_files，使用真实编译器执行。最多 20 个节点，扫描上限范围 1–5000。空节点默认执行 Planner 和 Reporter。

内联 Skill 不提供持久化人工暂停，因此执行前拒绝 human_review 或 confirm_before_run 节点；需要审核时使用任务工作台的持久化工作流。递归调用 workflow.run 会被拦截，失败、取消和等待审核不能伪装成成功。

## 验证

```powershell
.venv/Scripts/python.exe scripts/verify_iteration.py -q
.venv/Scripts/python.exe -m ruff check app tests --select E9,F63,F7,F82 --no-cache
npm --prefix web test
npm --prefix web run build
```

新增 tests/test_collaboration_execution.py 和 tests/test_inline_workflow_execution.py，覆盖计划路由、真实扫描证据、规则门控、审核幂等、拒绝不生成报告、失败恢复不重跑前序节点、业务产物，以及真实内联执行和递归限制。

2026-11-23：后端 63 项通过、2 项可选 PostgreSQL 集成测试跳过；前端 9 项通过，生产构建成功。另用临时项目和禁用模型的隔离真实服务完成 13 项浏览器验收：审核通过、拒绝、故障恢复、草稿导出限制，以及 1440 / 768 / 390 像素布局。未替换接口响应。

协作 Benchmark 等待审核时仅评价 review_gate，不自动批准，也不计入正式报告质量平均值；结果提供 evaluation_target、report_evaluated 和汇总计数。

## 自动重规划、修复与预算

第二轮已实现有轮次上限的候选修复闭环、Worker 并行执行，以及持久化的任务费用与 token 预算。工作台“多智能体”、项目入门和架构治理可使用这些选项；可视工作流和 PR 独立流程暂未接入这些新选项。

修复默认关闭。开启后会强制包含 Code Reviewer，根据文件证据生成候选修改，在副本中验证，再根据验证反馈重新规划和复查，最多 3 轮。最终需要人工审核，通过后可查看差异并点击应用；不会自动覆盖原项目。详情见 [自主执行与费用预算](AGENT_AUTONOMY.md)。

费用区分运行前粗估、供应商报告 token、在途预留和用量不确定的调用。预算不足时保存检查点并进入 budget_paused，提高预算后可继续。费用是按配置单价计算的估算，不是供应商账单；未知单价不等于免费。

## 后续边界

- 修复验证目前支持 Python 语法检查和显式选择的 pytest；其他语言的构建与测试预设尚未接入。
- 自动重规划以代码问题和验证反馈为输入，尚不是任意工具任务的开放式自主循环。
- 协作切片自动入库。
- 真实模型端到端质量评测及多模型对比结论；本轮验证的是离线行为和输出契约。

目前产品定位仍是项目理解与工程治理工作台，上述能力没有用模拟成功代替。
