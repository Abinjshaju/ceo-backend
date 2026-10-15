import { useCallback, useEffect, useState } from 'react';
import { Check, Plus, X } from 'lucide-react';
import { api } from './api.js';

// Lets the user choose market research reports offered during project intake.
export function DocumentPicker({ picker, busy, onAdd }) {
  const [selected, setSelected] = useState([]);
  const toggle = (id) => setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));

  return (
    <div className="choice-card report-picker" aria-live="polite">
      <p className="choice-card-title">{picker.title}</p>
      <ul className="report-list">
        {picker.reports.map((report) => (
          <li key={report.id}>
            <label className="report-option">
              <input type="checkbox" checked={selected.includes(report.id)} onChange={() => toggle(report.id)} disabled={busy}/>
              <span className="report-text">
                <b>{report.title}</b>
                <small>{report.provider} · {report.year} · {report.actual ? 'Actual report, analyzed once added' : 'Sample listing, not analyzed'}</small>
              </span>
            </label>
          </li>
        ))}
      </ul>
      <button type="button" className="report-add" disabled={!selected.length || busy} onClick={() => onAdd(selected)}>
        Add selected{selected.length ? ` (${selected.length})` : ''}
      </button>
    </div>
  );
}

// Generated cover for reports without a cover image: provider, title and year on a tinted panel.
function GeneratedCover({ report }) {
  return <div className="report-cover generated" aria-hidden="true">
    <span className="report-cover-provider">{report.provider}</span>
    <b className="report-cover-label">Sample listing</b>
    <span className="report-cover-year">{report.year}</span>
  </div>;
}

function ReportCard({ report, busy, error, onAdd }) {
  return <article className={`report-card ${report.added ? 'added' : ''}`}>
    {report.cover ? <img className="report-cover" src={report.cover} alt={`Cover of ${report.title}`} loading="lazy"/> : <GeneratedCover report={report}/>}
    <div className="report-card-body">
      <div className="report-card-meta">
        <span>{report.provider}</span>
        <span>{report.year}</span>
        {report.actual ? <i className="report-kind actual">Actual report</i> : <i className="report-kind">Sample</i>}
      </div>
      <h3>{report.title}</h3>
      <p>{report.summary}</p>
      <div className="report-card-foot">
        <small>{report.pages ? `${report.pages} pages` : report.actual ? '' : 'Not analyzed'}</small>
        {report.added
          ? <span className="report-added"><Check size={13}/> In this project</span>
          : <button type="button" className="report-add" disabled={busy} onClick={() => onAdd(report.id)}><Plus size={13}/> {busy ? 'Adding…' : 'Add to project'}</button>}
      </div>
      {error && <p className="report-error" role="alert">{error}</p>}
    </div>
  </article>;
}

// The Frost & Sullivan catalog for a project. Adding a report imports it into the project's documents.
export function ReportCatalogDialog({ projectId, onClose, onAdded }) {
  const [reports, setReports] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState('');
  const [errors, setErrors] = useState({});

  const load = useCallback(() => api(`/projects/${projectId}/market-reports`).then((data) => setReports(data.reports)).catch((failure) => setError(failure.message)), [projectId]);
  useEffect(() => { load(); }, [load]);

  const add = async (reportId) => {
    setBusyId(reportId);
    setError('');
    setErrors({});
    try {
      const result = await api(`/projects/${projectId}/market-reports`, { method: 'POST', body: { report_ids: [reportId] } });
      setErrors(Object.fromEntries(result.skipped.map((item) => [item.id, item.reason])));
      onAdded();
      await load();
    } catch (failure) {
      setErrors({ [reportId]: failure.message });
    }
    setBusyId(null);
  };

  return <div className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="new-project-dialog report-catalog" role="dialog" aria-modal="true" aria-label="Market research catalog">
      <div className="dialog-heading">
        <div><span>MARKET RESEARCH</span><h2>Frost &amp; Sullivan catalog</h2></div>
        <button type="button" className="dialog-close" onClick={onClose} aria-label="Close"><X size={18}/></button>
      </div>
      <p className="dialog-intro">Add reports to this project. The actual report is analyzed by the assistant; sample listings are kept for reference only.</p>
      {reports === null && !error && <p className="detail-empty" role="status">Loading the catalog…</p>}
      {reports && <div className="report-grid">
        {reports.map((report) => <ReportCard key={report.id} report={report} busy={busyId === report.id} error={errors[report.id]} onAdd={add}/>)}
      </div>}
      {error && <p className="dialog-error" role="alert">{error}</p>}
    </div>
  </div>;
}
