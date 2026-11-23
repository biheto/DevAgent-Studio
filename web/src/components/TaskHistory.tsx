import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, RefreshCw, Search } from 'lucide-react';
import { downloadTaskReport, searchTasks } from '../api';
import { TaskPage } from '../types';
import { statusLabel } from './WorkbenchShell';

export function ReportDownload({taskId, available}: {taskId: string; available: boolean}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [taskId]);
  async function download() {
    setBusy(true);
    setError('');
    try { await downloadTaskReport(taskId); }
    catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); }
    finally { setBusy(false); }
  }
  return <div className="report-download">
    <button type="button" className="secondary" onClick={download} disabled={!taskId || !available || busy}>
      <Download size={16} />{busy ? '正在导出…' : '导出 Markdown'}
    </button>
    {error && <span className="error-text" role="alert">{error}</span>}
  </div>;
}

export function TaskHistory({selectedTaskId, onOpen, disabled}: {
  selectedTaskId: string; onOpen: (taskId: string) => Promise<void>; disabled: boolean;
}) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState<TaskPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [opening, setOpening] = useState('');
  const limit = 20;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    const timer = setTimeout(async () => {
      try {
        const result = await searchTasks({q: query.trim(), status, limit, offset}, controller.signal);
        if (!controller.signal.aborted) setPage(result);
      } catch (exc) {
        if (!controller.signal.aborted) setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, status, offset, refresh, disabled]);

  async function open(taskId: string) {
    setOpening(taskId);
    setError('');
    try { await onOpen(taskId); }
    catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); }
    finally { setOpening(''); }
  }

  function refreshHistory() {
    setRefresh(value => value + 1);
    if (selectedTaskId && !disabled) void open(selectedTaskId);
  }

  const statuses = ['created', 'running', 'waiting_review', 'completed', 'failed', 'partial_success', 'cancelled', 'rejected'];
  return <div className="task-history">
    <div className="history-filters">
      <label className="history-search"><span className="sr-only">搜索历史任务</span><Search size={16} />
        <input type="search" placeholder="搜索目标、路径或任务 ID" maxLength={200} value={query}
          onChange={event => {setQuery(event.target.value); setOffset(0);}} />
      </label>
      <div className="history-filter-row">
        <select aria-label="筛选任务状态" value={status} onChange={event => {setStatus(event.target.value); setOffset(0);}}>
          <option value="">全部状态</option>
          {statuses.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}
        </select>
        <button type="button" className="icon-button" aria-label="刷新历史任务" disabled={loading || Boolean(opening)}
          onClick={refreshHistory}><RefreshCw size={16} /></button>
      </div>
    </div>
    {disabled && <p className="field-help">当前任务正在执行，完成后可切换历史任务。</p>}
    {error && <div role="alert"><p className="error-text">{error}</p><button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}>重试加载</button></div>}
    <div className="history-results" aria-busy={loading}>
      {loading ? <div className="history-loading" role="status" aria-label="正在加载历史任务">
        {[0, 1, 2].map(item => <div className="history-skeleton" key={item} />)}
      </div> : !error && page?.tasks.length === 0 ? <div className="history-empty">
        <p>{query || status ? '没有符合条件的任务' : '还没有任务记录'}</p>
        <p className="field-help">{query || status ? '尝试其他关键词，或清除筛选条件。' : '在任务工作台运行一次分析，执行记录会保存在这里。'}</p>
        {(query || status) && <button type="button" className="secondary" onClick={() => {setQuery(''); setStatus(''); setOffset(0);}}>清除筛选</button>}
      </div> : !error && <div className="task-list">
        {page?.tasks.map(task => <button type="button" key={task.task_id} aria-current={selectedTaskId === task.task_id ? 'true' : undefined}
          className={selectedTaskId === task.task_id ? 'active' : ''} disabled={disabled || Boolean(opening)} onClick={() => open(task.task_id)}>
          <span>{task.goal}</span>
          <small>{opening === task.task_id ? '正在读取…' : statusLabel(task.status)} · {task.created_at}</small>
          {task.project_path && <small className="history-path" title={task.project_path}>{task.project_path}</small>}
        </button>)}
      </div>}
    </div>
    {!loading && !error && page && <div className="history-pagination">
      <span role="status">共 {page.total} 条{page.total > 0 ? ` · ${offset + 1}–${offset + page.tasks.length}` : ''}</span>
      {(page.total > limit || offset > 0) && <div>
        <button type="button" className="icon-button" aria-label="上一页任务" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}><ChevronLeft size={16} /></button>
        <button type="button" className="icon-button" aria-label="下一页任务" disabled={offset + limit >= page.total} onClick={() => setOffset(offset + limit)}><ChevronRight size={16} /></button>
      </div>}
    </div>}
  </div>;
}
