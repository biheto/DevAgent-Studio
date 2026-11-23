import { Activity, ArrowUpRight, BarChart3, Check, ChevronRight, Command, FileText, History, LayoutDashboard, Menu, MessageSquare, Puzzle, Search, Workflow, Wrench, X } from 'lucide-react';
import { ReactNode, useEffect, useRef, useState } from 'react';

export type ViewKey = 'run' | 'workflow' | 'reports' | 'chat' | 'history' | 'llm' | 'mcp' | 'skills' | 'marketplace' | 'benchmark';

const destinations = [
  { view: 'run', label: '任务工作台', icon: LayoutDashboard, group: '工作空间', hint: '配置目标，运行 Agent' },
  { view: 'workflow', label: '工作流编排', icon: Workflow, group: '工作空间', hint: '连接节点，构建执行流程' },
  { view: 'reports', label: '分析报告', icon: FileText, group: '工作空间', hint: '查看结论、建议与审核结果' },
  { view: 'chat', label: '对话与知识', icon: MessageSquare, group: '工作空间', hint: '继续追问，积累项目知识' },
  { view: 'history', label: '任务历史', icon: History, group: '工作空间', hint: '回看任务与执行记录' },
  { view: 'llm', label: '模型与用量', icon: BarChart3, group: '能力管理', hint: '管理提示词，追踪模型调用' },
  { view: 'mcp', label: '工具连接', icon: Wrench, group: '能力管理', hint: '管理 MCP 服务与工具' },
  { view: 'skills', label: '技能管理', icon: Puzzle, group: '能力管理', hint: '配置技能和执行权限' },
  { view: 'marketplace', label: '插件市场', icon: Command, group: '能力管理', hint: '发现和安装扩展能力' },
  { view: 'benchmark', label: '效果评测', icon: Activity, group: '能力管理', hint: '运行基准测试，比较质量' },
] as const;

export const statusLabel = (status: string) => ({ idle: '准备就绪', created: '已创建', running: '正在执行', completed: '已完成', failed: '执行失败', budget_paused: '预算暂停', partial_success: '部分成功', waiting_review: '等待审核', approved: '已通过', rejected: '已拒绝', cancelled: '已取消' }[status] || status);

function Brand() {
  return <div className="studio-brand"><img className="studio-mark" src="/assets/favicon.svg?v=devagent-d1" width={32} height={32} alt="" aria-hidden="true" /><span>DevAgent<span className="brand-suffix">Studio</span></span></div>;
}

export function WorkbenchShell({ activeView, onNavigate, status, projectPath, children }: {
  activeView: ViewKey; onNavigate: (view: ViewKey) => void; status: string; projectPath: string; children: ReactNode;
}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const searchDialog = useRef<HTMLDialogElement>(null);
  const menuDialog = useRef<HTMLDialogElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchOpener = useRef<HTMLElement | null>(null);
  const active = destinations.find(item => item.view === activeView)!;
  const results = destinations.filter(item => `${item.label} ${item.hint} ${item.view}`.toLowerCase().includes(query.toLowerCase()));
  const project = projectPath.replace(/\\/g, '/').replace(/\/$/, '').split('/').pop() || '未选择项目';

  const openSearch = () => {
    if (searchDialog.current?.open) return;
    searchOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setQuery(''); setSelected(0);
    if (!searchDialog.current?.open) searchDialog.current?.showModal();
    searchInput.current?.focus();
  };
  const navigate = (view: ViewKey) => {
    onNavigate(view);
    searchDialog.current?.close(); menuDialog.current?.close();
  };
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); openSearch();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);
  useEffect(() => {
    if (searchDialog.current?.open) document.querySelector('#command-results [aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [selected, query]);

  const navigation = <>
    <Brand />
    <div className="project-context"><span className="project-initial">{project.slice(0, 1).toUpperCase()}</span><span><strong title={projectPath}>{project}</strong><small>本地工作空间</small></span></div>
    <nav aria-label="主导航">
      {['工作空间', '能力管理'].map(group => <div className="nav-group" key={group}>
        <span className="nav-group-label">{group}</span>
        {destinations.filter(item => item.group === group).map(item => <button key={item.view} type="button" className={`nav-item ${activeView === item.view ? 'active' : ''}`} aria-current={activeView === item.view ? 'page' : undefined} onClick={() => navigate(item.view)}>
          <item.icon size={18} strokeWidth={1.7} /><span>{item.label}</span>{activeView === item.view && <span className="nav-current" />}
        </button>)}
      </div>)}
    </nav>
    <div className="sidebar-bottom"><span className="local-indicator" /><span>本地工作空间</span><span className="version">v0.1</span></div>
  </>;

  return <div className="app-shell">
    <a className="skip-link" href="#workspace-content">跳至工作区</a>
    <aside className="side-nav">{navigation}</aside>
    <main className="app-main">
      <header className="topbar">
        <div className="topbar-location">
          <button className="icon-button mobile-menu-toggle" aria-label="打开导航" onClick={() => menuDialog.current?.showModal()}><Menu size={20} /></button>
          <span className="breadcrumb-parent">{active.group}</span><ChevronRight size={14} className="breadcrumb-divider" /><h1>{active.label}</h1>
        </div>
        <div className="topbar-actions">
          <button className="search-trigger" onClick={openSearch} aria-label="搜索页面（Ctrl 或 Command 加 K）"><Search size={16} /><span>快速前往</span><kbd>⌘ K</kbd></button>
          <div className={`status-pill ${status}`} role="status"><span className="status-dot" />{statusLabel(status)}</div>
        </div>
      </header>
      <div className="workspace-content" id="workspace-content" tabIndex={-1}>{children}</div>
    </main>
    <dialog ref={menuDialog} className="navigation-dialog" aria-label="导航菜单" onClick={event => { if (event.target === event.currentTarget) menuDialog.current?.close(); }}>
      <button className="icon-button dialog-close" aria-label="关闭导航" onClick={() => menuDialog.current?.close()}><X size={20} /></button>
      <div className="mobile-navigation">{navigation}</div>
    </dialog>
    <dialog ref={searchDialog} className="command-dialog" aria-labelledby="command-title" onClose={() => searchOpener.current?.focus()} onClick={event => { if (event.target === event.currentTarget) searchDialog.current?.close(); }}>
      <div className="command-search"><Search size={20} /><label className="sr-only" id="command-title" htmlFor="command-input">搜索页面</label>
        <input ref={searchInput} id="command-input" value={query} placeholder="想去哪里？搜索功能或页面…" role="combobox" aria-expanded="true" aria-controls="command-results" aria-autocomplete="list" aria-activedescendant={results[selected] ? `command-${results[selected].view}` : undefined} onChange={event => { setQuery(event.target.value); setSelected(0); }} onKeyDown={event => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setSelected(current => Math.max(0, Math.min(results.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)))); }
          if (event.key === 'Enter' && results[selected]) { event.preventDefault(); navigate(results[selected].view); }
        }} />
        <button className="icon-button" aria-label="关闭搜索" onClick={() => searchDialog.current?.close()}><X size={18} /></button>
      </div>
      <div className="command-results" id="command-results" role="listbox" aria-label="页面">
        {results.map((item, index) => <button role="option" aria-selected={selected === index} id={`command-${item.view}`} className={selected === index ? 'selected' : ''} key={item.view} onClick={() => navigate(item.view)} onPointerMove={() => setSelected(index)}><item.icon size={18} /><span><strong>{item.label}</strong><small>{item.hint}</small></span>{item.view === activeView ? <Check size={16} /> : <ArrowUpRight size={16} />}</button>)}
        {!results.length && <p className="empty-text">没有找到相关页面，试试“工作流”或“技能”。</p>}
      </div>
      <div className="command-footer"><span>↑ ↓ 选择 · Enter 打开</span><span>Esc 关闭</span></div>
    </dialog>
  </div>;
}
