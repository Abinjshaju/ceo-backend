// Builds the project report: one Markdown document that the export dialog previews and downloads.
// Pure functions only, so the preview and the downloaded file always show the same content.

export const ARTIFACT_LABELS = {
  decision_brief: 'Decision brief',
  chart: 'Chart',
  table: 'Table',
  timeline: 'Timeline',
  scenario: 'Scenario',
  form: 'Form',
  evidence_panel: 'Evidence',
};

const TIMELINE_STATUS = { done: 'Done', in_progress: 'In progress', planned: 'Planned', at_risk: 'At risk' };

export function artifactLabel(type) {
  return ARTIFACT_LABELS[type] || type;
}

export function formatValue(value) {
  if (value === null || value === undefined || value === '') return 'Not recorded';
  if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (Array.isArray(value)) return value.length ? value.map(formatValue).join(', ') : 'Not recorded';
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${formatValue(item)}`).join('; ');
  return String(value);
}

// Table cells must not break the Markdown table.
const cell = (value) => formatValue(value).replace(/\|/g, '\\|').replace(/\n/g, ' ');

function markdownTable(columns, rows) {
  const header = `| ${columns.map(cell).join(' | ')} |`;
  const divider = `| ${columns.map(() => '---').join(' | ')} |`;
  const body = rows.map((row) => `| ${row.map(cell).join(' | ')} |`);
  return [header, divider, ...body].join('\n');
}

function bulletList(items) {
  return items.map((item) => `- ${formatValue(item)}`).join('\n');
}

// The body of one artifact, as Markdown. Each type reads the fields its renderer in the app uses.
export function artifactBody(artifact) {
  const data = artifact.data || {};
  switch (artifact.type) {
    case 'table':
      return data.columns && data.rows ? markdownTable(data.columns, data.rows) : 'No rows.';
    case 'chart': {
      const series = data.series || [];
      if (!series.length) return 'No values.';
      const xs = [...new Set(series.flatMap((item) => item.points.map((point) => point.x)))];
      const columns = [data.x_label || 'Category', ...series.map((item) => (data.unit ? `${item.name} (${data.unit})` : item.name))];
      const rows = xs.map((x) => [x, ...series.map((item) => item.points.find((point) => point.x === x)?.y ?? null)]);
      return markdownTable(columns, rows);
    }
    case 'timeline':
      return (data.items || []).map((item) => {
        const parts = [item.label, item.date, TIMELINE_STATUS[item.status]].filter(Boolean);
        const deps = item.depends_on?.length ? ` (depends on ${item.depends_on.join(', ')})` : '';
        return `- ${parts.join(' · ')}${deps}`;
      }).join('\n') || 'No items.';
    case 'scenario': {
      const outputs = Object.entries(data.outputs || {}).map(([key, value]) => ({ key: key.replace(/_/g, ' '), value }));
      const inputs = Object.entries(data.inputs || {}).map(([key, value]) => ({ key: key.replace(/_/g, ' '), value }));
      const lines = [];
      if (inputs.length) lines.push('**Inputs**', '', markdownTable(['Input', 'Value'], inputs.map((item) => [item.key, item.value])), '');
      if (outputs.length) lines.push('**Results**', '', markdownTable(['Result', 'Value'], outputs.map((item) => [item.key, item.value])), '');
      if (data.formula) lines.push(`Formula: \`${data.formula}\``, '');
      if (data.assumptions?.length) lines.push('**Assumptions**', '', bulletList(data.assumptions));
      return lines.join('\n').trim() || 'No results.';
    }
    case 'evidence_panel':
      return `${data.claim || 'No claim recorded.'}\n\nSupported by ${data.refs?.length || 0} cited source${data.refs?.length === 1 ? '' : 's'}.`;
    case 'form':
      return data.fields?.length ? bulletList(data.fields.map((field) => field.label)) : 'No fields.';
    default:
      return formatValue(data);
  }
}

const titleCase = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1).replace(/_/g, ' ') : '');

// Documents that are only market listings are not sources for the report, so they are not shown.
const DOC_STATUS = { ready: 'Analyzed', processing: 'Processing', uploading: 'Uploading', failed: 'Failed', listed: null };

// The assessment without any score: summary, impact figures, risks and next step.
function assessmentBlock(brief) {
  const lines = [
    brief.summary || 'No summary recorded.',
    '',
    bulletList([
      `Investment: ${formatValue(brief.investment)}`,
      `Expected impact: ${formatValue(brief.expected_impact)}`,
      `Growth: ${formatValue(brief.growth)}`,
      `Feasibility: ${formatValue(brief.feasibility)}`,
      `Target completion: ${formatValue(brief.target_date)}`,
    ]),
  ];
  for (const [title, items] of [['Key risks', brief.key_risks], ['Open questions', brief.open_questions], ['Assumptions', brief.assumptions]]) {
    if (items?.length) lines.push('', `**${title}**`, '', bulletList(items));
  }
  if (brief.recommended_next_step) lines.push('', `**Recommended next step:** ${brief.recommended_next_step}`);
  return lines.join('\n');
}

// Executive summary line for one project: the assessment summary, else the conversation summary.
function headline(section) {
  return section.brief?.summary || section.chats || 'No summary recorded yet.';
}

// One numbered chapter per project. Each part is included only when its content was selected (not null).
// `exhibit` is a counter shared across the report so exhibit numbers run through the whole document.
function chapter(section, number, exhibit) {
  const parts = [`## ${number}. ${section.name}`, '', `*Status: ${titleCase(section.status)}*`];
  if (section.facts) {
    parts.push('', `### ${number}.1 Project overview`, '');
    parts.push(section.facts.length
      ? ['| Field | Value |', '| --- | --- |', ...section.facts.map((fact) => `| ${cell(fact.label)} | ${cell(fact.value)} |`)].join('\n')
      : 'No project details have been recorded yet.');
  }
  if (section.brief) parts.push('', `### ${number}.2 Assessment`, '', assessmentBlock(section.brief));
  if (section.chats !== null) parts.push('', `### ${number}.3 Discussion`, '', section.chats || 'No discussion summary has been recorded yet.');
  if (section.documents) {
    const sources = section.documents.filter((doc) => DOC_STATUS[doc.status] !== null);
    parts.push('', `### ${number}.4 Supporting documents`, '');
    parts.push(sources.length
      ? bulletList(sources.map((doc) => `${doc.name} (${DOC_STATUS[doc.status] || titleCase(doc.status)})`))
      : 'No supporting documents have been uploaded.');
  }
  if (section.artifacts) {
    parts.push('', `### ${number}.5 Exhibits`);
    if (!section.artifacts.length) parts.push('', 'No exhibits have been prepared.');
    for (const artifact of section.artifacts) {
      exhibit.count += 1;
      parts.push('', `**Exhibit ${exhibit.count}: ${artifact.title}**`, '', artifactBody(artifact), '', `*Source: ${section.name} project records.*`);
    }
  }
  return parts.join('\n');
}

// The report for other readers: cover details, contents, executive summary, one chapter per project,
// and a note on how the figures were prepared.
export function reportMarkdown(sections, { title, company, date }) {
  const exhibit = { count: 0 };
  const chapters = sections.map((section, index) => chapter(section, index + 1, exhibit));
  const lines = [
    `# ${title}`,
    '',
    company ? `Prepared by ${company} · ${date}` : `Prepared ${date}`,
    '',
    '## Contents',
    '',
    ...sections.map((section, index) => `${index + 1}. ${section.name}`),
    '',
    '## Executive summary',
    '',
    ...sections.map((section) => `- **${section.name}:** ${headline(section)}`),
    '',
    chapters.join('\n\n'),
    '',
    '## Methodology and sources',
    '',
    bulletList([
      'Figures and descriptions are taken from project records in the workspace as of the preparation date.',
      'Assessments and discussion summaries are generated from project information and conversations, and reflect the company\'s own inputs.',
      'Scenario results are calculated from the stated inputs, formula and assumptions.',
      'Figures have not been independently audited. Treat assessments as indicative.',
    ]),
    '',
  ];
  return lines.join('\n');
}

// Facts as the report shows them: only captured values, labelled with the app's field names.
export function factRows(facts, labels) {
  return Object.entries(facts || {})
    .filter(([, fact]) => fact?.value)
    .map(([field, fact]) => ({ label: labels[field] || field, value: fact.value }));
}
