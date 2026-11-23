import { useEffect, useState } from 'react';
import { estimateTask, taskBudget } from '../api';
import type { AgentOptions, BudgetOptions, BudgetUsage, CostEstimate, RepairResult } from '../types';

export const defaultAgentOptions: AgentOptions = {
  parallel_workers: true,
  repair_options: { enabled: false, max_rounds: 2, validation: 'python_compile' },
  budget: { max_cost_usd: null, max_tokens: 100000, output_tokens_per_call: 2048, input_price_per_1m: null, output_price_per_1m: null },
};

const money = (amount: number | null) => amount === null ? '单价缺失，无法估算' : `$${amount.toFixed(6)} USD`;

function BudgetFields({ value, onChange, disabled }: { value: BudgetOptions; onChange: (value: BudgetOptions) => void; disabled: boolean }) {
  const field = (key: keyof BudgetOptions, label: string, min: number, step: string, placeholder: string) => <label>{label}
    <input aria-label={label} type="number" min={min} step={step} value={value[key] ?? ''} placeholder={placeholder} disabled={disabled}
      onChange={event => onChange({ ...value, [key]: event.target.value === '' ? null : Number(event.target.value) })} />
  </label>;
  return <>
    <div className="form-row">{field('max_cost_usd', '费用上限（USD）', 0, '0.01', '不限制金额')}{field('max_tokens', 'Token 上限', 1, '1', '不限制 token')}</div>
    <div className="form-row">{field('input_price_per_1m', '输入单价（USD / 百万 token）', 0, '0.01', '使用模型配置')}{field('output_price_per_1m', '输出单价（USD / 百万 token）', 0, '0.01', '使用模型配置')}</div>
    <p className="field-help">填写的单价适用于本任务所有模型；留空则分别读取模型配置。价格未知时，金额预算会暂停付费调用。显式填写 0 表示免费。</p>
  </>;
}

export function AgentExecutionControls({ value, onChange, goal, maxFiles, disabled }: {
  value: AgentOptions; onChange: (value: AgentOptions) => void; goal: string; maxFiles: number; disabled: boolean;
}) {
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setEstimate(null); setError(''); setLoading(true);
    const timer = setTimeout(() => {
      estimateTask({ ...value, goal, max_files: maxFiles }, controller.signal).then(setEstimate).catch(exc => {
        if (!controller.signal.aborted) setError(String(exc.message));
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 400);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [value, goal, maxFiles]);
  return <><details className="advanced-settings agent-execution-controls">
    <summary>协作与费用<span>{value.parallel_workers ? '并行' : '串行'} · {value.repair_options.enabled ? '自动修复已开启' : '只分析'}</span></summary>
    <label className="toggle-row"><input type="checkbox" checked={value.parallel_workers} disabled={disabled} onChange={event => onChange({ ...value, parallel_workers: event.target.checked })} />并行执行独立 Agent</label>
    <label className="toggle-row"><input type="checkbox" checked={value.repair_options.enabled} disabled={disabled} onChange={event => onChange({ ...value, repair_options: { ...value.repair_options, enabled: event.target.checked } })} />在项目副本中自动修复并复查</label>
    {value.repair_options.enabled && <>
      <div className="form-row"><label>最多修复轮次<input type="number" min={1} max={3} value={value.repair_options.max_rounds} disabled={disabled} onChange={event => onChange({ ...value, repair_options: { ...value.repair_options, max_rounds: Number(event.target.value) } })} /></label>
        <label>验证方式<select value={value.repair_options.validation} disabled={disabled} onChange={event => onChange({ ...value, repair_options: { ...value.repair_options, validation: event.target.value as 'python_compile' | 'pytest' } })}><option value="python_compile">Python 语法检查</option><option value="pytest">运行项目 pytest 测试</option></select></label></div>
      <p className="field-help">修复后自动重新规划和复查，审核后可查看差异并应用。语法检查不验证业务行为；pytest 会在本机执行项目测试代码，最长 60 秒，不是安全沙箱。</p>
    </>}
    <BudgetFields value={value.budget} onChange={budget => onChange({ ...value, budget })} disabled={disabled} />
  </details>
    <p className="agent-cost-estimate" role="status">{loading ? '正在估算…' : estimate ? `运行前预估：${money(estimate.estimated_cost_usd)} · 约 ${estimate.estimated_tokens.toLocaleString()} token` : '暂无预估'}</p>
    {estimate && <p className="field-help">{estimate.basis}</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </>;
}

export function AgentExecutionResult({ taskId, running, status, usage: initialUsage, repair, executionError, onBudgetResume, onApply }: {
  taskId: string; running: boolean; status: string; usage?: BudgetUsage; repair?: RepairResult; executionError?: string;
  onBudgetResume: (budget: BudgetOptions) => Promise<void>; onApply: () => Promise<void>;
}) {
  const [usage, setUsage] = useState(initialUsage);
  const [budget, setBudget] = useState(initialUsage?.options ?? defaultAgentOptions.budget);
  const [error, setError] = useState('');
  useEffect(() => { setUsage(initialUsage); setBudget(initialUsage?.options ?? defaultAgentOptions.budget); setError(''); }, [taskId, initialUsage]);
  useEffect(() => {
    if (!taskId || (!running && !initialUsage)) return;
    let active = true;
    const refresh = () => taskBudget(taskId).then(value => { if (active) setUsage(value); }).catch(() => undefined);
    refresh();
    const timer = running ? setInterval(refresh, 1500) : undefined;
    return () => { active = false; if (timer) clearInterval(timer); };
  }, [taskId, running, initialUsage]);
  if (!usage && !repair && status !== 'budget_paused') return null;
  const act = async (run: () => Promise<void>) => { setError(''); try { await run(); } catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); } };
  return <div className="agent-execution-result">
    {usage && <><h3>任务用量</h3><dl className="state-summary"><dt>累计估算费用</dt><dd>{money(usage.estimated_cost_usd)}</dd><dt>已报告用量</dt><dd>{usage.reported_tokens.toLocaleString()} token</dd><dt>计入预算</dt><dd>{usage.accounted_tokens.toLocaleString()} token</dd><dt>在途 / 待确认</dt><dd>{usage.reserved_calls} / {usage.uncertain_calls} 次</dd></dl><p className="field-help">费用按配置单价估算，含保守预留，不是供应商账单。预算不足时停止发起新调用。</p></>}
    {status === 'budget_paused' && <div className="review-box"><h3>预算不足，任务已暂停</h3><p>{executionError}</p><BudgetFields value={budget} onChange={setBudget} disabled={running} /><button type="button" className="secondary" disabled={running} onClick={() => act(() => onBudgetResume(budget))}>保存预算并继续</button></div>}
    {repair && <><h3>候选修复 · 第 {repair.round} 轮</h3><p>{repair.applied ? '验证通过，已应用' : ({ validated: '验证通过，待应用', validation_failed: '验证未通过', no_changes: '未生成有效修改', invalid_patch: '修复格式校验失败', no_editable_evidence: '没有可编辑的文件证据' } as Record<string, string>)[repair.status] ?? repair.status}</p><p>{repair.reason}</p>
      {repair.validation && <details><summary>验证输出（{repair.validation.kind}）</summary><pre>{repair.validation.output}</pre></details>}
      {repair.diff && <details className="repair-diff"><summary>查看修复差异 · {repair.changed_files?.length ?? 0} 个文件</summary><pre>{repair.diff}</pre></details>}
      {repair.status === 'validated' && <button type="button" className="secondary" disabled={running || status !== 'completed' || repair.applied} onClick={() => act(onApply)}>{repair.applied ? '已应用到原项目' : status === 'completed' ? '应用修复到原项目' : '完成审核后可应用'}</button>}
    </>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </div>;
}
