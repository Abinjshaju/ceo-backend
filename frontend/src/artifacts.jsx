import { lazy, Suspense, useState } from 'react';
import { BarChart3, Check } from 'lucide-react';
import { api } from './api.js';
import { FIELD_LABELS, citationLabel, MessageText, SourcesList } from './content.jsx';

const ChartArtifact = lazy(() => import('./charts.jsx'));
const TYPE_LABELS = { decision_brief: 'Decision brief', chart: 'Chart', table: 'Table', timeline: 'Timeline', scenario: 'Scenario', form: 'Form', evidence_panel: 'Evidence' };
const STATUS_LABELS = { done: 'Done', in_progress: 'In progress', planned: 'Planned', at_risk: 'At risk' };

export function toArtifact(component) {
  return { ...component, id: component.artifact_id };
}

export function briefArtifact(assessment, projectName) {
  return { id: `assessment-${assessment.id}`, type: 'decision_brief', title: `${projectName} · Decision brief`, data: assessment.content, outdated: assessment.outdated };
}

const number = (value) => (typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value);

export function AssessmentArtifact({ brief, outdated = false }) {
  if (!brief) return null;
  const show = (value) => value || 'Not recorded';
  return <div className="assessment-artifact">
    <div className="assessment-banner"><div className="assessment-score"><strong>{brief.score ?? '—'}</strong><span>{brief.score == null ? '' : '/100'}</span></div><div><small>INDICATIVE PROJECT SCORE</small><b>{brief.project_name}</b><span>{outdated ? 'Out of date: project facts changed since this brief' : 'Indicative assessment · validate assumptions with your leadership team'}</span></div><span className={`feasibility-badge ${outdated ? 'outdated' : ''}`}>{brief.feasibility} feasibility</span></div>
    <div className="assessment-metrics"><div><small>Investment</small><b>{show(brief.investment)}</b></div><div><small>Expected impact</small><b>{show(brief.expected_impact)}</b></div><div><small>Growth</small><b>{show(brief.growth)}</b></div><div><small>ROI</small><b>{show(brief.roi)}</b></div><div><small>Evidence confidence</small><b>{brief.confidence}</b></div></div>
    <div className="assessment-bottom">
      <div className="assessment-summary"><small>SUMMARY</small><b>{brief.summary}</b></div>
      <div><small>TARGET COMPLETION</small><b>{show(brief.target_date)}</b></div>
      {brief.key_risks?.length > 0 && <div><small>KEY RISKS</small><ul className="brief-list">{brief.key_risks.map((risk) => <li key={risk}>{risk}</li>)}</ul></div>}
      {brief.open_questions?.length > 0 && <div><small>OPEN QUESTIONS</small><ul className="brief-list">{brief.open_questions.map((question) => <li key={question}>{question}</li>)}</ul></div>}
      {brief.assumptions?.length > 0 && <div><small>ASSUMPTIONS</small><ul className="brief-list">{brief.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></div>}
      <div className="assessment-next"><small>RECOMMENDED NEXT STEP</small><b>{brief.recommended_next_step}</b></div>
    </div>
  </div>;
}

function TableArtifact({ data, citations = [], sourcePrefix }) {
  return <div className="artifact-table-wrap"><table className="artifact-table"><thead><tr>{data.columns.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
    <tbody>{data.rows.map((row, index) => <tr key={index}>{row.map((cell, i) => <td key={i}><MessageText text={cell === null ? '—' : String(number(cell))} citations={citations} showSources={false} sourcePrefix={sourcePrefix}/></td>)}</tr>)}</tbody></table></div>;
}

function TimelineArtifact({ data }) {
  return <ol className="timeline">{data.items.map((item, index) => <li key={`${item.label}-${index}`} className={item.status || ''}>
    <span className="timeline-dot"/><div><b>{item.label}</b><small>{[item.date, STATUS_LABELS[item.status]].filter(Boolean).join(' · ')}</small>
      {item.depends_on?.length > 0 && <small className="timeline-deps">Depends on {item.depends_on.join(', ')}</small>}</div>
  </li>)}</ol>;
}

function inputText(value) {
  return Array.isArray(value) ? value.join(', ') : value ?? '';
}

function parseInput(original, text) {
  if (Array.isArray(original)) return text.split(',').map((part) => Number(part.trim())).filter((value) => Number.isFinite(value));
  if (typeof original === 'boolean') return text;
  const value = Number(text);
  return Number.isFinite(value) ? value : original;
}

function ScenarioArtifact({ artifact, projectId, onProjectChanged }) {
  const data = artifact.data;
  const [inputs, setInputs] = useState(data.inputs || {});
  const [result, setResult] = useState(data);
  const [adopt, setAdopt] = useState(null);
  const [status, setStatus] = useState({ busy: false, message: '', error: false });
  const event = async (body, done) => {
    setStatus({ busy: true, message: '', error: false });
    try {
      const response = await api(`/projects/${projectId}/artifacts/${artifact.id}/events`, { method: 'POST', body });
      done(response);
    } catch (failure) {
      setStatus({ busy: false, message: failure.status === 409 ? 'The project changed after this scenario was made. Ask the assistant to recalculate it.' : failure.message, error: true });
    }
  };
  const recalc = () => event({ operation: 'explore', inputs }, (response) => { setResult({ ...result, ...response.result }); setStatus({ busy: false, message: 'Recalculated. Project facts are unchanged.', error: false }); });
  const save = () => event({ operation: 'save_scenario', inputs, name: artifact.title }, () => setStatus({ busy: false, message: 'Scenario saved for later comparison.', error: false }));
  const confirmAdopt = (formEvent) => {
    formEvent.preventDefault();
    event({ operation: 'adopt', changes: [{ field: adopt.field, value: adopt.value }], expected_version: artifact.project_version }, () => { setAdopt(null); setStatus({ busy: false, message: `${FIELD_LABELS[adopt.field]} updated and the decision recorded.`, error: false }); onProjectChanged?.(); });
  };
  return <div className="scenario">
    <div className="scenario-grid">
      <section><small>INPUTS{data.currency ? ` · ${data.currency}` : ''}</small>
        {Object.entries(inputs).map(([key, value]) => <label key={key}>{key.replace(/_/g, ' ')}
          {typeof value === 'boolean' ? <input type="checkbox" checked={value} onChange={(e) => setInputs({ ...inputs, [key]: e.target.checked })}/>
            : <input value={inputText(value)} onChange={(e) => setInputs({ ...inputs, [key]: parseInput(data.inputs[key], e.target.value) })} inputMode="decimal"/>}
        </label>)}
      </section>
      <section><small>RESULTS</small>{Object.entries(result.outputs || {}).map(([key, value]) => <div className="scenario-output" key={key}><span>{key.replace(/_/g, ' ')}</span><b>{number(value)}{key.includes('percent') ? '%' : ''}</b></div>)}</section>
    </div>
    <p className="scenario-formula">{result.formula}</p>
    {result.assumptions?.length > 0 && <ul className="brief-list">{result.assumptions.map((item) => <li key={item}>{item}</li>)}</ul>}
    {adopt ? <form className="scenario-adopt" onSubmit={confirmAdopt}>
      <select value={adopt.field} onChange={(e) => setAdopt({ ...adopt, field: e.target.value })}>{Object.entries(FIELD_LABELS).filter(([key]) => key !== 'name').map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
      <input value={adopt.value} onChange={(e) => setAdopt({ ...adopt, value: e.target.value })} placeholder="New value, with currency" required/>
      <button type="submit" className="primary-button" disabled={status.busy}>Adopt</button><button type="button" className="cancel-button" onClick={() => setAdopt(null)}>Cancel</button>
    </form> : <div className="scenario-actions">
      <button type="button" className="cancel-button" onClick={recalc} disabled={status.busy}>Recalculate</button>
      <button type="button" className="cancel-button" onClick={save} disabled={status.busy}>Save scenario</button>
      <button type="button" className="primary-button" onClick={() => setAdopt({ field: 'investment', value: data.inputs?.new_investment ? `${data.currency || ''} ${inputs.new_investment}`.trim() : '' })} disabled={status.busy}>Adopt as project fact…</button>
    </div>}
    {status.message && <p className={`scenario-status ${status.error ? 'error' : ''}`} role="status">{status.message}</p>}
  </div>;
}

function FormArtifact({ artifact, projectId, onProjectChanged }) {
  const [values, setValues] = useState(() => Object.fromEntries(artifact.data.fields.map((field) => [field.field, field.current_value || ''])));
  const [status, setStatus] = useState({ busy: false, message: '', error: false });
  const submit = async (event) => {
    event.preventDefault();
    const changes = Object.entries(values).filter(([, value]) => String(value).trim()).map(([field, value]) => ({ field, value: String(value).trim() }));
    if (!changes.length) return;
    setStatus({ busy: true, message: '', error: false });
    try {
      await api(`/projects/${projectId}/artifacts/${artifact.id}/events`, { method: 'POST', body: { operation: 'submit_form', changes, expected_version: artifact.project_version } });
      setStatus({ busy: false, message: 'Saved to the project.', error: false });
      onProjectChanged?.();
    } catch (failure) {
      setStatus({ busy: false, message: failure.status === 409 ? 'The project changed after this form was made. Ask the assistant for a fresh one.' : failure.message, error: true });
    }
  };
  return <form className="artifact-form" onSubmit={submit}>
    {artifact.data.fields.map((field) => <label key={field.field}>{field.label}<input type={field.input_type === 'number' ? 'text' : field.input_type} inputMode={field.input_type === 'number' ? 'decimal' : undefined} value={values[field.field]} onChange={(event) => setValues({ ...values, [field.field]: event.target.value })}/></label>)}
    <button type="submit" className="primary-button" disabled={status.busy}>{status.busy ? 'Saving…' : artifact.data.submit_label || 'Save'}</button>
    {status.message && <p className={`scenario-status ${status.error ? 'error' : ''}`} role="status">{status.message}</p>}
  </form>;
}

function EvidenceArtifact({ data, citations }) {
  return <div className="evidence"><p>{data.claim}</p><ul>{data.refs.map((ref) => <li key={ref}><span>{ref.split(':')[0]}</span>{citations?.get(ref) ? citationLabel(citations.get(ref)) : ref}</li>)}</ul></div>;
}

function ArtifactBody({ artifact, projectId, onProjectChanged, citations, sourcePrefix }) {
  switch (artifact.type) {
    case 'decision_brief': return <AssessmentArtifact brief={artifact.data} outdated={artifact.outdated}/>;
    case 'chart': return <div className="chart-card"><div className="chart-card-heading"><span>{artifact.data.unit || 'Values'}</span><span>{(artifact.data.series || []).map((item) => item.name).join(' · ')}</span></div><Suspense fallback={<p role="status">Loading chart…</p>}><ChartArtifact data={artifact.data}/></Suspense></div>;
    case 'table': return <TableArtifact data={artifact.data} citations={[...citations.values()]} sourcePrefix={sourcePrefix}/>;
    case 'timeline': return <TimelineArtifact data={artifact.data}/>;
    case 'scenario': return <ScenarioArtifact key={artifact.id} artifact={artifact} projectId={projectId} onProjectChanged={onProjectChanged}/>;
    case 'form': return <FormArtifact key={artifact.id} artifact={artifact} projectId={projectId} onProjectChanged={onProjectChanged}/>;
    case 'evidence_panel': return <EvidenceArtifact data={artifact.data} citations={citations}/>;
    default: return <p className="artifact-note">This view type is not supported yet.</p>;
  }
}

export function ArtifactsView({ artifacts, activeId, setActiveId, projectId, onProjectChanged, citations }) {
  const active = artifacts.find((artifact) => artifact.id === activeId) || artifacts[0];
  if (!active) return <div className="artifact-empty"><div className="empty-chart-icon"><BarChart3 size={28}/></div><h2>Your artifacts appear here</h2><p>Ask for a brief, a timeline, a comparison, or a scenario and it will open here.</p></div>;
  const resolved = new Map([...(citations || new Map()), ...(active.citations || []).map((item) => [item.ref, item])]);
  const sources = (active.source_refs || []).map((ref) => resolved.get(ref) || { ref });
  const sourcePrefix = `artifact-${active.id}`;
  return <div className="artifact-view">
    <header className="artifact-header"><div><span className="artifact-eyebrow">{(TYPE_LABELS[active.type] || 'View').toUpperCase()}</span><h2>{active.title}</h2></div><span className="chart-type">{active.outdated ? 'Out of date' : TYPE_LABELS[active.type] || active.type}</span></header>
    {artifacts.length > 1 && <nav className="artifact-list" aria-label="Artifacts">{artifacts.map((artifact) => <button className={artifact.id === active.id ? 'selected' : ''} key={artifact.id} onClick={() => setActiveId(artifact.id)} title={artifact.title}>{artifact.id === active.id && <Check size={10}/>} {TYPE_LABELS[artifact.type] || artifact.type}</button>)}</nav>}
    <div key={active.id} className="artifact-body"><ArtifactBody artifact={active} projectId={projectId} onProjectChanged={onProjectChanged} citations={new Map(sources.map((item) => [item.ref, item]))} sourcePrefix={sourcePrefix}/></div>
    <SourcesList key={`sources-${active.id}`} citations={sources} prefix={sourcePrefix}/>
  </div>;
}
