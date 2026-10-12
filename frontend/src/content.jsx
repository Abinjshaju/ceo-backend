import { useId } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Renders assistant text: a small Markdown subset plus numbered citation markers.
// Everything becomes React elements; model output is never inserted as HTML.

export const FIELD_LABELS = {
  name: 'Name', description: 'Description', objective: 'Company objective', investment: 'Investment',
  financial_impact: 'Financial impact', growth_outcomes: 'Growth outcomes', owner: 'Delivery owner',
  target_date: 'Target date', customer_evidence: 'Customer evidence', dependencies: 'Dependencies',
  risks: 'Major risks', delivery_status: 'Delivery status', team_capacity: 'Team capacity',
};

export function citationLabel(citation) {
  if (!citation) return '';
  if (citation.kind === 'document') {
    const where = [citation.page ? `p. ${citation.page}` : null, citation.sheet, citation.heading].filter(Boolean).join(' · ');
    return `${citation.filename}${where ? ` — ${where}` : ''}`;
  }
  if (citation.kind === 'fact') return `Project fact: ${FIELD_LABELS[citation.field] || citation.field}${citation.status && citation.status !== 'confirmed' ? ` (${citation.status})` : ''}`;
  if (citation.kind === 'message') return `Earlier message, ${new Date(citation.created_at).toLocaleDateString()}`;
  if (citation.kind === 'decision') return `Decision: ${citation.description}`;
  if (citation.kind === 'question') return `Open question: ${citation.description}`;
  if (citation.kind === 'calculation') return `Calculation: ${String(citation.calculation_type || '').replace(/_/g, ' ')}`;
  return citation.ref;
}

// Transform citation markers at the Markdown AST level so they work in tables,
// emphasis and lists, while code blocks and ordinary links remain untouched.
function remarkCitations(options) {
  return (tree) => {
    const walk = (node) => {
      if (!node.children || ['code', 'inlineCode', 'link', 'linkReference'].includes(node.type)) return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== 'text') { walk(child); return [child]; }
        const result = [];
        const pattern = /\[\s*((?:doc|msg|fact|calc|decision|question):[^\]]+?)\s*\]/g;
        let end = 0;
        for (const match of child.value.matchAll(pattern)) {
          result.push({ type: 'text', value: child.value.slice(end, match.index) });
          for (const ref of match[1].split(/[,;]\s*/).map((value) => value.trim())) {
            const citation = options.numbers.get(ref);
            if (citation) result.push({ type: 'link', url: `#citation-${citation.n}`, title: citation.label,
              children: [{ type: 'text', value: String(citation.n) }] });
          }
          end = match.index + match[0].length;
        }
        result.push({ type: 'text', value: child.value.slice(end) });
        return result;
      });
    };
    walk(tree);
  };
}

export function SourcesList({ citations = [], prefix }) {
  if (!citations.length) return null;
  return <ol className="message-sources" aria-label="Sources">
    {citations.map((citation, index) => <li key={citation.ref} id={`${prefix}-citation-${index + 1}`} tabIndex={-1}>
      <span>{index + 1}</span><details><summary>{citationLabel(citation)}</summary>
        <p>{citation.value || citation.description || citation.heading || citation.filename || citation.ref}</p>
        {citation.status && <p>Status: {citation.status}</p>}
      </details>
    </li>)}
  </ol>;
}

export function MessageText({ text, citations = [], showSources = true, sourcePrefix }) {
  const id = useId();
  const prefix = sourcePrefix || id;
  const numbers = new Map(citations.map((citation, index) => [citation.ref, { n: index + 1, label: citationLabel(citation) }]));
  return <div className="message-text">
    <ReactMarkdown remarkPlugins={[remarkGfm, [remarkCitations, { numbers }]]} components={{
      table: ({ children }) => <div className="artifact-table-wrap"><table className="artifact-table">{children}</table></div>,
      a: ({ href, title, children }) => href?.startsWith('#citation-')
        ? <sup><a className="cite" href={`#${prefix}-${href.slice(1)}`} title={title} aria-label={`Source ${children}: ${title}`}>{children}</a></sup>
        : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    }}>{String(text ?? '')}</ReactMarkdown>
    {showSources && <SourcesList citations={citations} prefix={prefix}/>}
  </div>;
}
