import { test } from 'node:test';
import assert from 'node:assert/strict';
import { artifactBody, factRows, reportMarkdown } from '../src/report-export.js';

test('table artifacts become Markdown tables with escaped pipes', () => {
  const body = artifactBody({ type: 'table', data: { columns: ['Item', 'Cost'], rows: [['A | B', 10]] } });
  assert.equal(body, '| Item | Cost |\n| --- | --- |\n| A \\| B | 10 |');
});

test('chart series are merged by x value', () => {
  const body = artifactBody({
    type: 'chart',
    data: { unit: 'INR', x_label: 'Month', series: [
      { name: 'Revenue', points: [{ x: 'Jan', y: 4 }, { x: 'Feb', y: 6 }] },
      { name: 'Cost', points: [{ x: 'Feb', y: 2 }] },
    ] },
  });
  assert.match(body, /\| Month \| Revenue \(INR\) \| Cost \(INR\) \|/);
  assert.match(body, /\| Jan \| 4 \| Not recorded \|/);
});

test('scenario artifacts show inputs, results, formula and assumptions', () => {
  const body = artifactBody({ type: 'scenario', data: {
    inputs: { investment: 100 }, outputs: { roi_percent: 25 }, formula: 'roi = x', assumptions: ['Constant benefit'],
  } });
  assert.match(body, /\| investment \| 100 \|/);
  assert.match(body, /\| roi percent \| 25 \|/);
  assert.match(body, /Formula: `roi = x`/);
  assert.match(body, /- Constant benefit/);
});

test('unselected parts are left out of a project chapter', () => {
  const markdown = reportMarkdown([{ name: 'Loyalty', status: 'active', facts: null, brief: null, chats: null, documents: null, artifacts: null }], { title: 'T', company: '', date: 'today' });
  assert.match(markdown, /## 1\. Loyalty\n\n\*Status: Active\*/);
  assert.doesNotMatch(markdown, /### 1\.\d/);
});

test('the assessment never shows a score', () => {
  const markdown = reportMarkdown([{ name: 'A', status: 'active', facts: [], chats: null, documents: null, artifacts: null,
    brief: { score: 72, summary: 'Strong payback.', key_risks: ['Vendor delay'], recommended_next_step: 'Confirm date' } }],
    { title: 'T', company: 'Acme', date: 'today' });
  assert.doesNotMatch(markdown, /score|72/i);
  assert.match(markdown, /Strong payback\./);
  assert.match(markdown, /Recommended next step:\*\* Confirm date/);
});

test('market listings are not cited as supporting documents', () => {
  const markdown = reportMarkdown([{ name: 'A', status: 'active', facts: null, brief: null, chats: null, artifacts: null,
    documents: [{ name: 'Plan.pdf', status: 'ready' }, { name: 'Sample listing.pdf', status: 'listed' }] }], { title: 'T', company: '', date: 'today' });
  assert.match(markdown, /Plan\.pdf \(Analyzed\)/);
  assert.doesNotMatch(markdown, /Sample listing/);
});

test('exhibits are numbered across the whole report with sources', () => {
  const table = { type: 'table', title: 'Budget', data: { columns: ['Q'], rows: [['1']] } };
  const markdown = reportMarkdown([
    { name: 'A', status: 'active', facts: null, brief: null, chats: null, documents: null, artifacts: [table] },
    { name: 'B', status: 'active', facts: null, brief: null, chats: null, documents: null, artifacts: [{ ...table, title: 'Spend' }] },
  ], { title: 'T', company: '', date: 'today' });
  assert.match(markdown, /\*\*Exhibit 1: Budget\*\*/);
  assert.match(markdown, /\*\*Exhibit 2: Spend\*\*/);
  assert.match(markdown, /\*Source: B project records\.\*/);
});

test('facts use the supplied labels and skip empty values', () => {
  const rows = factRows({ budget: { value: '₹18 lakh' }, owner: { value: '' } }, { budget: 'Budget' });
  assert.deepEqual(rows, [{ label: 'Budget', value: '₹18 lakh' }]);
});

test('the report has a cover line, contents, executive summary and methodology', () => {
  const markdown = reportMarkdown([{ name: 'A', status: 'active', facts: [], brief: null, chats: 'Summary', documents: [], artifacts: [] }], { title: 'Project report: A', company: 'Acme', date: '1 January 2026' });
  assert.ok(markdown.startsWith('# Project report: A'));
  assert.match(markdown, /Prepared by Acme · 1 January 2026/);
  assert.match(markdown, /## Contents\n\n1\. A/);
  assert.match(markdown, /## Executive summary\n\n- \*\*A:\*\* Summary/);
  assert.match(markdown, /## Methodology and sources/);
  assert.match(markdown, /No exhibits have been prepared\./);
});
