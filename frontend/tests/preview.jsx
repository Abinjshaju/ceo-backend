import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArtifactsView } from '../src/artifacts.jsx';
import { MessageText } from '../src/content.jsx';
import '../src/styles.css';
const citations = [{ ref: 'fact:investment', kind: 'fact', field: 'investment', value: '₹20 lakh' }];
const base = { citations, source_refs: ['fact:investment'], project_version: 1 };
const artifacts = [
  { ...base, id: 'table', type: 'table', title: 'Investment comparison', data: { columns: ['Metric', 'Value'], rows: [['Investment', '**₹20 lakh** [fact:investment]']] } },
  ...['bar', 'line', 'pie'].map((type) => ({ ...base, id: type, type: 'chart', title: `${type} chart`, data: { chart_type: type, unit: '₹ lakh', x_label: 'Period', series: [{ name: 'Plan', points: [{ x: 'Year 1', y: 20 }, { x: 'Year 2', y: 35 }] }] } })),
  { ...base, id: 'timeline', type: 'timeline', title: 'Delivery plan', data: { items: [{ label: 'Launch', date: '2026-12-01', status: 'planned' }] } },
];
function Preview() {
  const [activeId, setActiveId] = useState('table');
  return <main style={{ maxWidth: 950, margin: '30px auto', padding: 20 }}><h1>Artifact rendering QA</h1>
    <section style={{ position: 'relative', minHeight: 500 }}><ArtifactsView artifacts={artifacts} activeId={activeId} setActiveId={setActiveId}/></section>
    <h2>Markdown table with citations</h2><MessageText citations={citations} text={'| Metric | Value |\n| --- | --- |\n| Investment | 20 [fact:investment] |'}/>
  </main>;
}
createRoot(document.getElementById('root')).render(<Preview/>);
