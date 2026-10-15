import { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, Download, FileText, MessageSquare, Paperclip, ScrollText, Shapes, X } from 'lucide-react';
import { api } from './api.js';
import { FIELD_LABELS } from './content.jsx';
import { artifactLabel, factRows, reportMarkdown } from './report-export.js';

// Report export: pick the projects and the contents, preview the report, and download it.
// The report is Markdown for now; the PDF layout is not decided yet.

const CONTENT = [
  { key: 'information', label: 'Project information', icon: FileText, hint: 'Captured facts and figures' },
  { key: 'brief', label: 'Assessment', icon: ScrollText, hint: 'Summary, risks and next step' },
  { key: 'chats', label: 'Chats', icon: MessageSquare, hint: 'Conversation summary' },
  { key: 'documents', label: 'Documents', icon: Paperclip, hint: 'Files attached to the project' },
  { key: 'artifacts', label: 'Artifacts', icon: Shapes, hint: 'Tables, charts, timelines and scenarios' },
];

const plural = (count, word, pluralWord = `${word}s`) => `${count} ${count === 1 ? word : pluralWord}`;

const cleanSummary = (text) => (text ? text.replace(/[#*]/g, '').trim() : null);

export function ReportExportDialog({ projects, title, workspaceId, onClose }) {
  const [loaded, setLoaded] = useState(null);
  const [company, setCompany] = useState('');
  const [loadError, setLoadError] = useState('');
  const [selectedProjects, setSelectedProjects] = useState(() => projects.map((project) => project.id));
  const [include, setInclude] = useState({ information: true, brief: true, chats: true, documents: true, artifacts: true });
  const [selectedArtifacts, setSelectedArtifacts] = useState(null);

  useEffect(() => {
    if (!workspaceId) return;
    api(`/workspaces/${workspaceId}/company`).then((profile) => setCompany(profile.fields?.name || '')).catch(() => setCompany(''));
  }, [workspaceId]);

  // Keyed on the IDs, so a parent re-render with the same projects keeps the current selection.
  const projectKey = projects.map((project) => project.id).join(',');
  useEffect(() => {
    let cancelled = false;
    Promise.all(projects.map((project) => Promise.all([
      api(`/projects/${project.id}`),
      api(`/projects/${project.id}/artifacts`),
    ]).then(([detail, artifacts]) => ({ ...project, detail, artifacts: artifacts.artifacts })).catch(() => null)))
      .then((results) => {
        if (cancelled) return;
        const ok = results.filter(Boolean);
        setLoaded(ok);
        setSelectedProjects(ok.map((item) => item.id));
        setSelectedArtifacts(new Set(ok.flatMap((item) => item.artifacts.map((artifact) => artifact.artifact_id))));
        if (ok.length < projects.length) setLoadError(`${projects.length - ok.length} project(s) could not be loaded and are left out.`);
      })
      .catch((failure) => { if (!cancelled) setLoadError(failure.message); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectKey]);

  const chosen = useMemo(() => (loaded || []).filter((item) => selectedProjects.includes(item.id)), [loaded, selectedProjects]);
  const sections = useMemo(() => chosen.map((item) => ({
    id: item.id,
    name: item.detail.name,
    status: item.detail.status,
    facts: include.information ? factRows(item.detail.facts, FIELD_LABELS) : null,
    brief: include.brief ? item.detail.latest_assessment?.content || null : null,
    chats: include.chats ? cleanSummary(item.detail.summary) : null,
    documents: include.documents ? item.detail.documents.map((doc) => ({ name: doc.filename, status: doc.status })) : null,
    artifacts: include.artifacts ? item.artifacts.filter((artifact) => selectedArtifacts?.has(artifact.artifact_id)) : null,
  })), [chosen, include, selectedArtifacts]);

  const anyContent = Object.values(include).some(Boolean);
  const canExport = sections.length > 0 && anyContent;
  const markdown = useMemo(() => (canExport ? reportMarkdown(sections, { title, company, date: new Date().toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) }) : ''), [canExport, sections, title, company]);

  const toggleProject = (id) => setSelectedProjects((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  const toggleContent = (key) => setInclude((current) => ({ ...current, [key]: !current[key] }));
  const toggleArtifact = (id) => setSelectedArtifacts((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const download = () => {
    const blob = new Blob([markdown], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${title.replace(/[^\w-]+/g, '-').toLowerCase() || 'report'}-${new Date().toISOString().slice(0, 10)}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const counts = (item) => ({
    facts: Object.values(item.detail.facts || {}).filter((fact) => fact?.value).length,
    documents: item.detail.documents.length,
    artifacts: item.artifacts.length,
  });

  return <div className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="new-project-dialog report-builder" role="dialog" aria-modal="true" aria-label="Export report">
      <header className="report-builder-head">
        <div><span>EXPORT REPORT</span><h2>{title}</h2></div>
        <button type="button" className="dialog-close" onClick={onClose} aria-label="Close"><X size={18}/></button>
      </header>

      {!loaded && !loadError && <p className="detail-empty" role="status">Loading project data…</p>}
      {loadError && <p className="dialog-error" role="status">{loadError}</p>}

      {loaded && <div className="report-builder-body">
        <aside className="report-builder-side">
          <section aria-label="Contents">
            <h3 className="report-builder-label">Include</h3>
            <div className="report-tiles">
              {CONTENT.map((item) => {
                const Icon = item.icon;
                const on = include[item.key];
                return <button type="button" key={item.key} className={`report-tile ${on ? 'on' : ''}`} aria-pressed={on} onClick={() => toggleContent(item.key)}>
                  <span className="report-tile-icon"><Icon size={15}/></span>
                  <span className="report-tile-text"><b>{item.label}</b><small>{item.hint}</small></span>
                  <span className="report-tile-check">{on && <Check size={12}/>}</span>
                </button>;
              })}
            </div>
          </section>

          {projects.length > 1 && <section aria-label="Projects">
            <h3 className="report-builder-label">Projects <span>{selectedProjects.length} of {loaded.length}</span></h3>
            <ul className="report-pick-list">
              {loaded.map((item) => <li key={item.id}>
                <label className="report-pick">
                  <input type="checkbox" checked={selectedProjects.includes(item.id)} onChange={() => toggleProject(item.id)}/>
                  <span><b>{item.detail.name}</b><small>{item.detail.status} · {plural(counts(item).facts, 'fact')} · {plural(counts(item).documents, 'document')} · {plural(counts(item).artifacts, 'artifact')}</small></span>
                </label>
              </li>)}
            </ul>
          </section>}

          {include.artifacts && <section aria-label="Artifacts">
            <h3 className="report-builder-label">Artifacts <span>{plural(sections.reduce((sum, section) => sum + (section.artifacts?.length || 0), 0), 'selected', 'selected')}</span></h3>
            {chosen.every((item) => item.artifacts.length === 0)
              ? <p className="report-empty">No artifacts have been generated yet.</p>
              : <ul className="report-pick-list">
                {chosen.flatMap((item) => item.artifacts.map((artifact) => <li key={artifact.artifact_id}>
                  <label className="report-pick">
                    <input type="checkbox" checked={selectedArtifacts?.has(artifact.artifact_id)} onChange={() => toggleArtifact(artifact.artifact_id)}/>
                    <span><b>{artifact.title}</b><small>{artifactLabel(artifact.type)}{projects.length > 1 ? ` · ${item.detail.name}` : ''}</small></span>
                  </label>
                </li>))}
              </ul>}
          </section>}
        </aside>

        <section className="report-builder-preview" aria-label="Report preview">
          <div className="report-preview-bar"><span>Preview</span><small>{canExport ? 'Markdown' : 'Nothing selected'}</small></div>
          {canExport
            ? <div className="report-paper"><ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown></div>
            : <p className="report-empty">{anyContent ? 'Choose at least one project.' : 'Choose at least one kind of content.'}</p>}
        </section>
      </div>}

      <footer className="report-builder-foot">
        <small>The report is written for outside readers and leaves out internal scores. The PDF layout is not decided yet.</small>
        <button type="button" className="report-add" disabled={!canExport} onClick={download}><Download size={13}/> Download report</button>
      </footer>
    </div>
  </div>;
}
