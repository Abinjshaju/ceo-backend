import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Archive, ArrowUpRight, BriefcaseBusiness, Check, ChevronDown, ChevronLeft, CircleHelp, Compass, FolderKanban, LogOut, MoreHorizontal, Paperclip, Plus, RefreshCw, Search, Settings, Target, Trash2, TrendingUp, User, X } from 'lucide-react';
import { api, checkUpload, downloadDocument, getToken, setToken, setUnauthorizedHandler, uploadDocument, SUPPORTED_UPLOADS } from './api.js';
import { AssessmentArtifact } from './artifacts.jsx';
import { AIChatWidget, AttachmentCard } from './assistant.jsx';
import { ReportCatalogDialog } from './document-picker.jsx';
import { AvatarStudio, readAvatarSettings, saveAvatarSettings } from './avatar.jsx';
import { FIELD_LABELS } from './content.jsx';

const STAGES = [['early', 'Early stage'], ['growth', 'Growth'], ['established', 'Established'], ['enterprise', 'Enterprise']];
const COLORS = ['violet', 'blue', 'orange', 'green', 'pink', 'cyan'];
const DOCUMENT_STATUS = { uploading: 'Uploading', processing: 'Processing…', ready: 'Ready', failed: 'Failed', listed: 'Listed, not analyzed' };

function colorFor(id) {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return COLORS[hash % COLORS.length];
}

// Saved profiles keep lists; older values may be a single comma-separated string.
function toList(value) {
  if (Array.isArray(value)) return value;
  return value ? String(value).split(/,\s*/).filter(Boolean) : [];
}

function listText(value) {
  return toList(value).join(', ');
}

function readStored(key, fallback) {
  try { return JSON.parse(window.localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

function MetricCard({ icon: Icon, label, value, note, color }) {
  return <article className="metric-card"><div className="metric-top"><span className={`metric-icon ${color}`}><Icon size={18}/></span></div><p>{label}</p><strong>{value}</strong><span className="metric-note">{note}</span></article>;
}

function ProjectRowMenu({ project, onArchive, onDelete }) {
  const [position, setPosition] = useState(null);
  const trigger = useRef(null);
  const list = useRef(null);
  const open = Boolean(position);
  const close = () => setPosition(null);
  const toggle = (event) => {
    event.stopPropagation();
    if (open) { close(); return; }
    const rect = trigger.current.getBoundingClientRect();
    setPosition({ top: rect.bottom + 6, right: window.innerWidth - rect.right });
  };
  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event) => { if (!trigger.current?.contains(event.target) && !list.current?.contains(event.target)) close(); };
    document.addEventListener('mousedown', closeOnOutside);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);
  return <div className="row-menu" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
    <button ref={trigger} type="button" className={`row-menu-button ${open ? 'open' : ''}`} aria-label={`Actions for ${project.name}`} aria-haspopup="menu" aria-expanded={open} onClick={toggle}><MoreHorizontal size={16}/></button>
    {open && createPortal(<div ref={list} className="row-menu-list" role="menu" style={{ top: position.top, right: position.right }} onClick={(event) => event.stopPropagation()}>
      {project.status !== 'draft' && <button type="button" role="menuitem" onClick={() => { close(); onArchive(project); }}><Archive size={14}/> {project.status === 'archived' ? 'Unarchive' : 'Archive'}</button>}
      <button type="button" role="menuitem" className="danger" onClick={() => { close(); onDelete(project); }}><Trash2 size={14}/> Delete</button>
    </div>, document.body)}
  </div>;
}

function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel, error }) {
  return <div className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <div className="new-project-dialog" role="alertdialog" aria-label={title}>
      <div className="dialog-heading"><div><span>CONFIRM</span><h2>{title}</h2></div><button type="button" className="dialog-close" onClick={onCancel} aria-label="Close"><X size={18}/></button></div>
      <p className="dialog-intro">{message}</p>
      {error && <p className="dialog-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button type="button" className="cancel-button" onClick={onCancel}>Cancel</button><button type="button" className="danger-button" onClick={onConfirm}>{confirmLabel}</button></div>
    </div>
  </div>;
}

function statusLabel(project) {
  if (project.status === 'draft') return 'Intake in progress';
  if (project.status === 'archived') return 'Archived';
  if (project.assessment_status === 'outdated') return 'Brief out of date';
  if (project.assessment_status === 'none') return 'Brief pending';
  return 'Active';
}

function ProjectTable({ projects, compact = false, onSelect, actions = false, onArchive, onDelete }) {
  const sorted = [...projects].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  return <div className={`project-table ${compact ? 'compact' : ''}`}>
    <div className="project-table-head"><span>Rank</span><span>Project</span><span>Score</span><span>Feasibility</span><span>Expected impact</span><span>Status</span><span/></div>
    {sorted.map((project, index) => <div className="project-row clickable" role="button" tabIndex={0} onClick={() => onSelect?.(project)} onKeyDown={(event) => { if (event.key === 'Enter') onSelect?.(project); }} key={project.id}>
      <span className="rank-cell">{String(index + 1).padStart(2, '0')}</span>
      <span className="project-name-cell"><i className={`project-avatar ${colorFor(project.id)}`}>{project.name.slice(0, 1).toUpperCase()}</i><span><b>{project.name}</b><small>{[project.owner, project.target_date].filter(Boolean).join(' · ') || 'Owner and date to confirm'}</small></span></span>
      <span className="score-cell"><b>{project.score ?? '—'}</b><span className="score-track"><i style={{ width: `${project.score || 0}%` }}/></span></span>
      <span><i className={`feasibility-dot ${project.feasibility?.toLowerCase() || 'none'}`}/>{project.feasibility || 'Not assessed'}</span>
      <span className="impact-cell" title={project.expected_impact || ''}>{project.expected_impact || '—'}</span>
      <span><i className={`status-dot ${project.status}`}/>{statusLabel(project)}{project.open_questions > 0 && <small className="row-questions"> · {project.open_questions} open</small>}</span>
      <span className="row-actions">{actions && <ProjectRowMenu project={project} onArchive={onArchive} onDelete={onDelete}/>}</span>
    </div>)}
    {!sorted.length && <div className="no-projects">No projects yet. Select New to add your first project.</div>}
  </div>;
}

// Multi-select chips with a custom entry. Selected custom values appear as removable chips.
function ChoiceField({ label, options, selected, onChange }) {
  const [custom, setCustom] = useState('');
  const toggle = (option) => onChange(selected.includes(option) ? selected.filter((item) => item !== option) : [...selected, option]);
  const addCustom = () => {
    const value = custom.trim();
    if (!value) return;
    if (!selected.some((item) => item.toLowerCase() === value.toLowerCase())) onChange([...selected, value]);
    setCustom('');
  };
  const customValues = selected.filter((item) => !options.includes(item));
  return <fieldset className="choice-field">
    <legend>{label}</legend>
    <div className="choice-chips">
      {options.map((option) => <button type="button" key={option} className={`choice-chip ${selected.includes(option) ? 'selected' : ''}`} aria-pressed={selected.includes(option)} onClick={() => toggle(option)}>{selected.includes(option) && <Check size={12}/>}{option}</button>)}
      {customValues.map((value) => <button type="button" key={value} className="choice-chip selected custom" aria-label={`Remove ${value}`} onClick={() => toggle(value)}>{value}<X size={12}/></button>)}
    </div>
    <div className="choice-custom">
      <input value={custom} onChange={(event) => setCustom(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addCustom(); } }} placeholder="Add your own"/>
      <button type="button" onClick={addCustom} disabled={!custom.trim()}>Add</button>
    </div>
  </fieldset>;
}

function CompanySetupDialog({ workspaceId, company, onClose, onSaved }) {
  const fields = company?.fields || {};
  const [revenueModels, setRevenueModels] = useState(() => toList(fields.revenue_model).length ? toList(fields.revenue_model) : ['Subscription']);
  const [priorities, setPriorities] = useState(() => toList(fields.priorities).length ? toList(fields.priorities) : ['Increase revenue']);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const value = (name) => (fields[name] && fields[name] !== 'unknown' ? fields[name] : '');
  const submit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const next = {
      name: data.get('company').trim(), employee_count: data.get('employeeCount').trim(), products_services: data.get('products').trim(),
      main_customers: data.get('customers').trim(), industry: data.get('industry').trim(), stage: data.get('stage'),
      revenue_model: revenueModels.join(', '), markets: data.get('markets').trim(), priorities,
    };
    setBusy(true);
    setError('');
    try {
      const saved = await api(`/workspaces/${workspaceId}/company`, { method: 'PATCH', body: { expected_version: company?.version ?? 0, fields: Object.fromEntries(Object.entries(next).filter(([, item]) => item && item.length)), complete: true } });
      onSaved(saved);
      onClose();
    } catch (failure) {
      const missing = failure.details?.missing;
      setError(missing?.length ? `Please fill in: ${missing.map((name) => name.replace(/_/g, ' ')).join(', ')}.` : failure.status === 409 ? 'The profile changed in another window. Close and reopen this form.' : failure.message);
    } finally {
      setBusy(false);
    }
  };
  return <div className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><form className="new-project-dialog" onSubmit={submit}>
    <div className="dialog-heading"><div><span>COMPANY PROFILE</span><h2>Company context</h2></div><button type="button" className="dialog-close" onClick={onClose} aria-label="Close"><X size={18}/></button></div>
    <p className="dialog-intro">This context helps the assistant ask better project questions and assess fit against your goals.</p>
    <label>Company name<input name="company" placeholder="Your company" defaultValue={value('name')} autoFocus required/></label>
    <div className="form-two"><label>Employees<input name="employeeCount" placeholder="e.g. 250" defaultValue={value('employee_count')}/></label><label>Markets / geographies<input name="markets" placeholder="e.g. North America" defaultValue={value('markets')}/></label></div>
    <div className="form-two"><label>Products and services<input name="products" placeholder="What the company sells" defaultValue={value('products_services')}/></label><label>Main customers<input name="customers" placeholder="Who buys from you" defaultValue={value('main_customers')}/></label></div>
    <div className="form-two"><label>Industry<input name="industry" placeholder="e.g. Financial services" defaultValue={value('industry')}/></label><label>Company stage<select name="stage" defaultValue={value('stage') || 'growth'}>{STAGES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
    <ChoiceField label="Revenue model" options={['Subscription', 'Usage based', 'Transaction fees', 'Licensing', 'Professional services', 'Marketplace / commission', 'Hybrid']} selected={revenueModels} onChange={setRevenueModels}/>
    <ChoiceField label="Most important priorities for the next 12 months" options={['Increase revenue', 'Reduce operating costs', 'Improve margins', 'Improve retention', 'Launch a new product', 'Enter a new market', 'Automate operations']} selected={priorities} onChange={setPriorities}/>
    {error && <p className="dialog-error" role="alert">{error}</p>}
    <div className="dialog-actions"><button type="button" className="cancel-button" onClick={onClose}>Cancel</button><button type="submit" className="primary-button" disabled={busy || !revenueModels.length || !priorities.length}>{busy ? 'Saving…' : 'Save company context'} <ArrowUpRight size={15}/></button></div>
  </form></div>;
}

const DICTATION_LANGUAGES = [['en-US', 'English (US)'], ['en-GB', 'English (UK)'], ['en-IN', 'English (India)'], ['es-ES', 'Spanish'], ['fr-FR', 'French'], ['de-DE', 'German']];

function SettingsDialog({ language, onLanguage, avatarSettings, onAvatarSettings, workspaceId, company, projects, onClose }) {
  const [health, setHealth] = useState(null);
  const [exporting, setExporting] = useState(false);
  useEffect(() => { api('/health').then(setHealth).catch(() => setHealth({ status: 'error' })); }, []);
  const languages = DICTATION_LANGUAGES.some(([code]) => code === language) ? DICTATION_LANGUAGES : [...DICTATION_LANGUAGES, [language, language]];
  const exportWorkspace = async () => {
    setExporting(true);
    try {
      const details = await Promise.all(projects.map((project) => api(`/projects/${project.id}`)));
      const blob = new Blob([JSON.stringify({ workspace_id: workspaceId, company, projects: details }, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'ceo-workspace.json';
      link.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };
  return <div className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="new-project-dialog settings-dialog" role="dialog" aria-label="Settings">
      <div className="dialog-heading"><div><span>WORKSPACE</span><h2>Settings</h2></div><button type="button" className="dialog-close" onClick={onClose} aria-label="Close"><X size={18}/></button></div>
      <section className="settings-section">
        <h3>Assistant</h3>
        <div className="settings-row"><span><b>Dictation language</b><small>Used by the microphone when you dictate a message.</small></span>
          <select value={language} onChange={(event) => onLanguage(event.target.value)} aria-label="Dictation language">{languages.map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></div>
        <div className="settings-row"><span><b>Model</b><small>Configured on the server. Model keys never reach the browser.</small></span>
          <span className={`verify-status ${health?.model_configured ? 'ok' : health ? 'error' : ''}`}>{!health ? 'Checking…' : health.model_configured ? `${health.model} · connected` : 'Not configured on the server'}</span></div>
      </section>
      <section className="settings-section">
        <h3>Assistant avatar</h3>
        <p className="settings-note">Its expressions follow the conversation. Appearance changes apply to the assistant and are saved in this browser.</p>
        <AvatarStudio settings={avatarSettings} onChange={onAvatarSettings}/>
      </section>
      <section className="settings-section">
        <h3>Data</h3>
        <div className="settings-row"><span><b>Export workspace</b><small>Download your company profile and projects, with facts, decisions and questions, as JSON.</small></span><button type="button" className="cancel-button" onClick={exportWorkspace} disabled={exporting}>{exporting ? 'Exporting…' : 'Export'}</button></div>
      </section>
      <div className="dialog-actions"><button type="button" className="primary-button" onClick={onClose}>Done</button></div>
    </div>
  </div>;
}

function FactsCard({ detail }) {
  const facts = Object.entries(detail.facts || {});
  return <article className="detail-card">
    <div className="detail-card-head"><div><span className="card-eyebrow">PROJECT RECORD</span><h2>Facts</h2></div><small className="card-meta">Version {detail.version}</small></div>
    {facts.length ? <dl className="fact-list">{facts.map(([name, fact]) => <div key={name}>
      <dt>{FIELD_LABELS[name] || name}</dt>
      <dd><span>{fact.value === 'unknown' ? 'Unknown' : fact.value}{fact.unit ? ` ${fact.unit}` : ''}</span><i className={`fact-status ${fact.status}`} title={`Source: ${fact.source?.kind || 'unknown'}`}>{fact.status}</i></dd>
    </div>)}</dl> : <p className="detail-empty">No facts recorded yet.</p>}
    {detail.missing_fields?.length > 0 && <p className="detail-hint">Still needed: {detail.missing_fields.map((name) => FIELD_LABELS[name] || name).join(', ')}.</p>}
  </article>;
}

function ProjectDetail({ projectId, version, onChanged }) {
  const [detail, setDetail] = useState(null);
  const [notice, setNotice] = useState('');
  const [briefBusy, setBriefBusy] = useState(false);
  const [uploading, setUploading] = useState([]);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const fileInput = useRef(null);
  const load = useCallback(() => api(`/projects/${projectId}`).then(setDetail).catch((failure) => setNotice(failure.message)), [projectId]);
  useEffect(() => { load(); }, [load, version]);
  const processing = detail?.documents?.some((doc) => doc.status === 'uploading' || doc.status === 'processing' || doc.latest_version?.status === 'processing');
  useEffect(() => {
    if (!processing) return undefined;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [processing, load]);
  if (!detail) return <section className="project-detail"><p className="detail-empty">{notice || 'Loading project…'}</p></section>;

  const refreshBrief = async () => {
    setBriefBusy(true);
    setNotice('');
    try {
      await api(`/projects/${projectId}/assessments`, { method: 'POST' });
      await load();
      onChanged();
    } catch (failure) {
      setNotice(`The brief could not be updated: ${failure.message}`);
    }
    setBriefBusy(false);
  };
  const addFiles = async (list) => {
    const files = Array.from(list || []);
    const problems = files.map(checkUpload).filter(Boolean);
    if (problems.length) setNotice(problems.join(' '));
    const valid = files.filter((file) => !checkUpload(file));
    setUploading(valid.map((file) => file.name));
    for (const file of valid) {
      try { await uploadDocument(`/projects/${projectId}`, file); } catch (failure) { setNotice(`${file.name}: ${failure.message}`); }
    }
    setUploading([]);
    load();
  };
  const removeDocument = async (doc) => {
    try {
      await api(`/projects/${projectId}/documents/${doc.id}`, { method: 'DELETE' });
      setPendingDelete(null);
      load();
      onChanged();
    } catch (failure) {
      setPendingDelete({ ...doc, error: failure.message });
    }
  };
  const brief = detail.latest_assessment;
  const scope = (doc) => (doc.scope === 'company' ? `/workspaces/${detail.workspace_id || ''}` : `/projects/${projectId}`);

  return <section className="project-detail">
    {catalogOpen && <ReportCatalogDialog projectId={projectId} onClose={() => setCatalogOpen(false)} onAdded={() => { load(); onChanged(); }}/>}
    <header className="detail-head">
      <div className="detail-title"><p className="eyebrow">{detail.status === 'draft' ? 'PROJECT · INTAKE IN PROGRESS' : detail.status === 'archived' ? 'PROJECT · ARCHIVED' : 'PROJECT'}</p><h1>{detail.name}</h1></div>
      <span/>
    </header>
    <div className="detail-grid">
      <div className="detail-main">
        {brief ? <AssessmentArtifact brief={brief.content} outdated={brief.outdated}/> : <article className="detail-card"><span className="card-eyebrow">DECISION BRIEF</span><p className="detail-empty">{detail.status === 'draft' ? 'The brief is written when the intake is finished.' : 'The decision brief is being prepared. It appears here in a moment.'}</p></article>}
        {detail.status === 'active' && <div className="detail-actions">
          <button type="button" className="cancel-button" onClick={refreshBrief} disabled={briefBusy}><RefreshCw size={13}/> {briefBusy ? 'Writing the brief… about 20 seconds' : brief?.outdated ? 'Refresh the out-of-date brief' : 'Refresh brief'}</button>
        </div>}
        {notice && <p className="detail-notice" role="status">{notice}</p>}
        <FactsCard detail={detail}/>
        {detail.summary && <article className="detail-card"><span className="card-eyebrow">CONVERSATION SUMMARY</span><div className="brief-text">{detail.summary.replace(/[#*]/g, '')}</div></article>}
      </div>
      <div className="detail-side">
        <article className="detail-card">
          <div className="detail-card-head">
            <div><span className="card-eyebrow">FILES</span><h2>{detail.documents.length ? `${detail.documents.length} document${detail.documents.length > 1 ? 's' : ''}` : 'Documents'}</h2></div>
            <div className="files-actions">
              <button type="button" className="text-link" onClick={() => setCatalogOpen(true)}><BriefcaseBusiness size={13}/> Market research</button>
              <button type="button" className="text-link" onClick={() => fileInput.current?.click()}><Paperclip size={13}/> Add files</button>
            </div>
          </div>
          <input ref={fileInput} className="hidden-file-input" type="file" accept={SUPPORTED_UPLOADS.join(',')} multiple onChange={(event) => { const list = event.target.files; addFiles(list); event.target.value = ''; }}/>
          {detail.documents.length || uploading.length ? <div className="attachment-grid">
            {detail.documents.map((doc) => <div className="document-row" key={doc.id}>
              <AttachmentCard file={{ name: doc.filename }} status={`${doc.scope === 'company' ? 'Company · ' : ''}${doc.status === 'failed' ? `Failed: ${doc.latest_version?.error || 'unreadable'}` : DOCUMENT_STATUS[doc.status] || doc.status}${doc.page_count ? ` · ${doc.page_count} pages` : ''}`} onOpen={doc.current_version ? () => downloadDocument(scope(doc), doc.id).catch((failure) => setNotice(failure.message)) : undefined}/>
              {doc.scope === 'project' && <button type="button" className="document-delete" onClick={() => setPendingDelete(doc)} aria-label={`Delete ${doc.filename}`}><Trash2 size={13}/></button>}
            </div>)}
            {uploading.map((name) => <AttachmentCard key={name} file={{ name }} status="Uploading…"/>)}
          </div> : <p className="detail-empty">No documents yet. Add PDFs, Word, Excel, CSV or text files; the assistant searches them and cites pages.</p>}
        </article>
        <article className="detail-card">
          <span className="card-eyebrow">DECISIONS</span>
          {detail.decisions.length ? <ul className="record-list">{detail.decisions.map((decision) => <li key={decision.id}><b>{decision.description}</b>{decision.rationale && <small>{decision.rationale}</small>}{decision.status === 'proposed' && <i className="fact-status proposed">proposed</i>}</li>)}</ul> : <p className="detail-empty">Decisions you make in the assistant are recorded here.</p>}
        </article>
        <article className="detail-card">
          <span className="card-eyebrow">OPEN QUESTIONS</span>
          {detail.open_questions.length ? <ul className="record-list">{detail.open_questions.map((question) => <li key={question.id}><CircleHelp size={13}/><b>{question.description}</b></li>)}</ul> : <p className="detail-empty">Nothing open right now.</p>}
        </article>
      </div>
    </div>
    {pendingDelete && <ConfirmDialog title="Delete document?" message={`“${pendingDelete.filename}” and its searchable content will be removed. Facts that relied on it will be flagged for review.`} confirmLabel="Delete" error={pendingDelete.error} onConfirm={() => removeDocument(pendingDelete)} onCancel={() => setPendingDelete(null)}/>}
  </section>;
}

function Dashboard({ me, onLogout }) {
  const workspace = me.workspaces[0];
  const workspaceId = workspace.id;
  const [company, setCompany] = useState(null);
  const [projects, setProjects] = useState([]);
  const [archived, setArchived] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [version, setVersion] = useState(0);
  const [section, setSection] = useState('Explore');
  const [selectedProjectId, setSelectedProjectId] = useState(null);
  const [mode, setMode] = useState(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [projectView, setProjectView] = useState('active');
  const [pendingDelete, setPendingDelete] = useState(null);
  const [query, setQuery] = useState('');
  const [language, setLanguage] = useState(() => readStored('ceo-workspace-language', navigator.language || 'en-US'));
  useEffect(() => { try { window.localStorage.setItem('ceo-workspace-language', JSON.stringify(language)); } catch { /* ignore */ } }, [language]);
  const [avatarSettings, setAvatarSettings] = useState(readAvatarSettings);
  useEffect(() => saveAvatarSettings(avatarSettings), [avatarSettings]);

  const refresh = useCallback(async () => {
    try {
      const [profile, list, archivedList] = await Promise.all([
        api(`/workspaces/${workspaceId}/company`),
        api(`/workspaces/${workspaceId}/projects?limit=100`),
        api(`/workspaces/${workspaceId}/projects?status=archived&limit=100`),
      ]);
      setCompany(profile);
      setProjects(list.projects);
      setArchived(archivedList.projects);
      setVersion((current) => current + 1);
      setLoadError('');
      return { profile, projects: list.projects };
    } catch (failure) {
      setLoadError(failure.message);
      return null;
    } finally {
      setLoaded(true);
    }
  }, [workspaceId]);

  // First load decides where the assistant starts: company setup, a first project, or nothing.
  useEffect(() => {
    refresh().then((data) => {
      if (!data) return;
      if (data.profile.status !== 'complete') { setMode({ kind: 'company' }); setAssistantOpen(true); }
      else if (!data.projects.some((project) => project.status === 'active')) { setMode({ kind: 'intake', requestId: Date.now() }); setAssistantOpen(true); }
      else setMode({ kind: 'idle' });
    });
  }, [refresh]);

  const allProjects = [...projects, ...archived];
  const selectedProject = allProjects.find((project) => project.id === selectedProjectId) || null;
  const activeProjects = projects.filter((project) => project.status === 'active');
  const setupComplete = company?.status === 'complete' && activeProjects.length > 0;
  const target = useMemo(() => {
    if (!mode) return null;
    if (mode.kind !== 'project') return mode;
    const project = allProjects.find((item) => item.id === mode.projectId);
    return { ...mode, projectName: project?.name || 'this project' };
  }, [mode, projects, archived]);

  const selectProject = (project) => {
    setSelectedProjectId(project.id);
    setMode(project.status === 'draft' ? { kind: 'intake', requestId: Date.now() } : { kind: 'project', projectId: project.id });
  };
  const goBack = () => {
    if (selectedProject) { setSelectedProjectId(null); if (company?.status === 'complete') setMode({ kind: 'idle' }); }
    else if (section !== 'Explore') setSection('Explore');
  };
  const startProject = () => { setSelectedProjectId(null); setMode({ kind: 'intake', requestId: Date.now() }); setAssistantOpen(true); };
  const toggleArchive = async (project) => {
    try {
      await api(`/projects/${project.id}`, { method: 'PATCH', body: { expected_version: project.version, status: project.status === 'archived' ? 'active' : 'archived' } });
    } catch (failure) {
      setLoadError(failure.message);
    }
    refresh();
  };
  const deleteProject = async (project) => {
    try {
      await api(`/projects/${project.id}`, { method: 'DELETE' });
    } catch (failure) {
      setPendingDelete({ ...project, error: failure.message });
      return;
    }
    if (selectedProjectId === project.id) { setSelectedProjectId(null); setMode({ kind: 'idle' }); }
    setPendingDelete(null);
    refresh();
  };

  const scored = activeProjects.filter((project) => project.score != null);
  const average = scored.length ? Math.round(scored.reduce((sum, project) => sum + project.score, 0) / scored.length) : null;
  const top = [...scored].sort((a, b) => b.score - a.score)[0];
  const openQuestions = activeProjects.reduce((sum, project) => sum + (project.open_questions || 0), 0);
  const matchesQuery = (project) => `${project.name} ${project.owner || ''}`.toLowerCase().includes(query.toLowerCase());
  const visibleProjects = (projectView === 'archived' ? archived : projects).filter(matchesQuery);
  const exploreProjects = projects.filter(matchesQuery);
  const fields = company?.fields || {};
  const navItems = [{ label: 'Explore', icon: Compass }, { label: 'Projects', icon: FolderKanban }];

  if (!loaded) return <main className="auth-page"><p className="detail-empty">Loading your workspace…</p></main>;

  return <div className="workspace-shell">
    <aside className="sidebar">
      <a className="workspace-brand" href="#explore" onClick={(event) => { event.preventDefault(); setSection('Explore'); setSelectedProjectId(null); }}><span className="brand-symbol"><i/><i/><i/><i/></span><span>Workspace<small>PROJECT PORTFOLIO</small></span></a>
      <div className="sidebar-label">WORKSPACE</div>
      <nav className="sidebar-nav" aria-label="Workspace navigation">
        {navItems.map(({ label, icon: Icon }) => <button key={label} className={section === label && !selectedProject ? 'selected' : ''} onClick={() => { setSection(label); setSelectedProjectId(null); }}><Icon size={18}/>{label}</button>)}
        <button className="new-nav" onClick={startProject} disabled={company?.status !== 'complete'} title={company?.status !== 'complete' ? 'Finish the company profile first' : undefined}><span><Plus size={16}/></span>New</button>
      </nav>
      <div className={`sidebar-bottom ${profileOpen ? 'open' : ''}`}>
        <div className="profile-accordion">
          <button type="button" className="sidebar-profile" aria-expanded={profileOpen} aria-controls="profile-menu" onClick={() => setProfileOpen((open) => !open)}>
            <span>{fields.name?.slice(0, 2).toUpperCase() || 'WS'}</span>
            <div><b>{fields.name || workspace.name}</b><small>{company?.status === 'complete' ? fields.industry : 'Setup in progress'}</small></div>
            <ChevronDown size={16} className="profile-chevron"/>
          </button>
          <div className="profile-menu" id="profile-menu">
            <div className="profile-menu-inner">
              <small className="profile-email">{me.user.email}</small>
              <button type="button" onClick={() => setShowOnboarding(true)}><User size={15}/> Edit profile</button>
              <button type="button" onClick={() => setShowSettings(true)}><Settings size={15}/> Settings</button>
              <button type="button" className="logout-button" onClick={onLogout}><LogOut size={15}/> Log out</button>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <main className="dashboard-main">
      <header className="dashboard-topbar"><nav className="breadcrumbs" aria-label="Breadcrumb"><button type="button" className="crumb-back" onClick={goBack} disabled={!selectedProject && section === 'Explore'} aria-label="Go back"><ChevronLeft size={15}/></button><span>Workspace</span><span className="crumb-sep">/</span>{selectedProject ? <><button type="button" className="crumb-link" onClick={goBack}>{section}</button><span className="crumb-sep">/</span><b>{selectedProject.name}</b></> : <b>{section}</b>}</nav></header>
      <div className="dashboard-content">
        {loadError && <p className="dialog-error" role="alert">{loadError}</p>}
        {selectedProject ? <ProjectDetail key={selectedProject.id} projectId={selectedProject.id} version={version} onChanged={refresh}/> : (<>
        <div className="page-heading"><div><p className="eyebrow">CEO DECISION WORKSPACE</p><h1>{section === 'Projects' ? 'Projects' : 'Explore'}</h1><p className="heading-subtitle">Connect company strategy to project feasibility, financial impact, and returns.</p></div></div>

        <section className={`onboarding-strip ${company?.status === 'complete' ? 'complete' : ''}`}>
          <span className="onboarding-icon"><BriefcaseBusiness size={17}/></span>
          <div className="onboarding-copy"><strong>{company?.status === 'complete' ? `${fields.name} · Company context saved` : 'Start with your company context'}</strong><span>{company?.status === 'complete' ? `${fields.industry} · ${fields.revenue_model} · Priorities: ${listText(fields.priorities)}` : 'Tell the assistant about your business, revenue model, and top priorities before assessing projects.'}</span></div>
          <span className="onboarding-progress"><i/></span>
          <button className="onboarding-action" onClick={() => (company?.status === 'complete' ? setShowOnboarding(true) : (setMode({ kind: 'company' }), setAssistantOpen(true)))}>{company?.status === 'complete' ? 'Edit profile' : 'Continue setup'} <ArrowUpRight size={13}/></button>
        </section>

        {section === 'Explore' && (
          <section className="metrics-grid" aria-label="Portfolio metrics">
            <MetricCard icon={FolderKanban} label="Active projects" value={activeProjects.length} note={projects.length > activeProjects.length ? `${projects.length - activeProjects.length} in intake` : 'Across your workspace'} color="violet"/>
            <MetricCard icon={Target} label="Top project score" value={top ? `${top.score}/100` : '—'} note={top?.name || 'Scores appear once briefs are written'} color="blue"/>
            <MetricCard icon={TrendingUp} label="Average project score" value={average != null ? `${average}/100` : '—'} note={scored.length ? `Across ${scored.length} scored project${scored.length > 1 ? 's' : ''}` : 'No scored projects yet'} color="orange"/>
            <MetricCard icon={CircleHelp} label="Open questions" value={openQuestions} note="Unresolved across active projects" color="green"/>
          </section>
        )}

        <section className="projects-section">
          <div className="section-heading"><div><h2>{section === 'Projects' ? 'All projects' : 'Top-ranked projects'}</h2><p>{section === 'Projects' ? `${projects.length} current · ${archived.length} archived` : 'Your strongest opportunities, ranked by score'}</p></div><div className="section-actions">{section === 'Projects' && <div className="segmented" role="tablist" aria-label="Project status"><button type="button" role="tab" aria-selected={projectView === 'active'} className={projectView === 'active' ? 'active' : ''} onClick={() => setProjectView('active')}>Current <span>{projects.length}</span></button><button type="button" role="tab" aria-selected={projectView === 'archived'} className={projectView === 'archived' ? 'active' : ''} onClick={() => setProjectView('archived')}>Archived <span>{archived.length}</span></button></div>}<label className="search-box"><Search size={16}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search projects"/></label>{section === 'Explore' && <button className="text-link" onClick={() => setSection('Projects')}>View all <ArrowUpRight size={14}/></button>}</div></div>
          <ProjectTable projects={section === 'Projects' ? visibleProjects : exploreProjects.slice(0, 5)} compact={section === 'Explore'} onSelect={selectProject} actions={section === 'Projects'} onArchive={toggleArchive} onDelete={setPendingDelete}/>
        </section>
        </>)}
        <footer className="dashboard-footer"><span>Workspace overview</span><span>Scores are indicative estimates from each project’s decision brief</span></footer>
      </div>
    </main>
    {pendingDelete && <ConfirmDialog title="Delete project?" message={`“${pendingDelete.name}” and its documents, conversations and history will be permanently deleted.`} confirmLabel="Delete" error={pendingDelete.error} onConfirm={() => deleteProject(pendingDelete)} onCancel={() => setPendingDelete(null)}/>}
    {showSettings && <SettingsDialog language={language} onLanguage={setLanguage} avatarSettings={avatarSettings} onAvatarSettings={setAvatarSettings} workspaceId={workspaceId} company={company} projects={allProjects} onClose={() => setShowSettings(false)}/>}
    {showOnboarding && <CompanySetupDialog workspaceId={workspaceId} company={company} onClose={() => setShowOnboarding(false)} onSaved={() => refresh().then((data) => { if (data && !data.projects.some((p) => p.status === 'active')) { setMode({ kind: 'intake', requestId: Date.now() }); setAssistantOpen(true); } })}/>}
    <AIChatWidget open={assistantOpen} setOpen={setAssistantOpen} canClose={setupComplete} onLogout={onLogout} target={target} workspaceId={workspaceId} projects={projects} language={language} avatarSettings={avatarSettings}
      onChanged={refresh}
      onCompanyComplete={() => { refresh(); setMode({ kind: 'intake', requestId: Date.now() }); }}
      onProjectCreated={(projectId) => { refresh().then(() => { setSelectedProjectId(projectId); setSection('Projects'); setMode({ kind: 'project', projectId }); }); }}
      onStartProject={startProject}
      onSelectProject={(projectId) => { const project = projects.find((item) => item.id === projectId); if (project) selectProject(project); }}/>
  </div>;
}

function SignInPage({ onSignedIn }) {
  const [mode, setMode] = useState('signin');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState(null);
  useEffect(() => { api('/auth/config').then(setConfig).catch(() => setConfig({ password_sign_in: true })); }, []);
  const creating = mode === 'create';
  const submit = async (event) => {
    event.preventDefault();
    if (!email.includes('@') || password.length < 8) { setError('Enter a valid email and a password of at least 8 characters.'); return; }
    setBusy(true);
    setError('');
    try {
      const result = creating
        ? await api('/auth/signup', { method: 'POST', body: { email, password, name } })
        : await api('/auth/login', { method: 'POST', body: { email, password } });
      setToken(result.token);
      onSignedIn();
    } catch (failure) {
      setError(failure.message || 'Something went wrong. Try again.');
    } finally {
      setBusy(false);
    }
  };
  if (config && !config.password_sign_in) return <main className="auth-page"><div className="auth-card"><h1>Sign-in is managed by your organization</h1><p className="auth-intro">This server uses single sign-on. Connect the frontend to your identity provider to sign in.</p></div></main>;
  return <main className="auth-page">
    <form className="auth-card" onSubmit={submit}>
      <div className="auth-brand"><span className="brand-symbol"><i/><i/><i/><i/></span><div><b>Workspace</b><small>PROJECT PORTFOLIO</small></div></div>
      <h1>{creating ? 'Create your account' : 'Sign in to your workspace'}</h1>
      <p className="auth-intro">{creating ? 'Your company profile, projects and documents are private to your workspace.' : 'Welcome back. Enter your details to continue.'}</p>
      <div className="auth-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={!creating} className={!creating ? 'active' : ''} onClick={() => { setMode('signin'); setError(''); }}>Sign in</button>
        <button type="button" role="tab" aria-selected={creating} className={creating ? 'active' : ''} onClick={() => { setMode('create'); setError(''); }}>Create account</button>
      </div>
      {creating && <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Your name" autoComplete="name"/></label>}
      <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" autoComplete="email" required/></label>
      <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 8 characters" autoComplete={creating ? 'new-password' : 'current-password'} required/></label>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <button type="submit" className="primary-button auth-submit" disabled={busy}>{busy ? 'Please wait…' : creating ? 'Create account' : 'Sign in'}</button>
    </form>
  </main>;
}

export default function App() {
  const [me, setMe] = useState(null);
  const [checking, setChecking] = useState(() => Boolean(getToken()));
  const signOut = useCallback(() => { setToken(null); setMe(null); setChecking(false); }, []);
  const loadMe = useCallback(() => {
    setChecking(true);
    api('/me').then(setMe).catch(() => signOut()).finally(() => setChecking(false));
  }, [signOut]);
  useEffect(() => { setUnauthorizedHandler(signOut); if (getToken()) loadMe(); }, [loadMe, signOut]);
  if (checking) return <main className="auth-page"><p className="detail-empty">Loading…</p></main>;
  if (!me) return <SignInPage onSignedIn={loadMe}/>;
  return <Dashboard key={me.user.id} me={me} onLogout={signOut}/>;
}
