import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
after(() => server.close());
const { MessageText } = await server.ssrLoadModule('/src/content.jsx');
const { ArtifactsView } = await server.ssrLoadModule('/src/artifacts.jsx');
const citations = [{ ref: 'fact:investment', kind: 'fact', field: 'investment', value: '₹20 lakh' }];
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));

test('Markdown tables resolve grouped citations inside cells and emphasis', () => {
  const html = render(MessageText, { text: '| Metric | Value |\n| --- | --- |\n| Budget | **20 [fact:investment; fact:unknown]** |', citations });
  assert.match(html, /<table/);
  assert.match(html, /<strong>20 <sup>/);
  assert.match(html, /href="#[^"]+-citation-1"/);
  assert.match(html, /<summary>Project fact: Investment<\/summary>/);
  assert.doesNotMatch(html, /fact:unknown/);
});

test('Markdown code stays literal and unsafe model HTML is not executable', () => {
  const html = render(MessageText, { text: '`[fact:investment]`\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert%281%29)', citations });
  assert.match(html, /<code>\[fact:investment\]<\/code>/);
  assert.doesNotMatch(html, /<script>|href="javascript:/);
});

test('saved table uses artifact citations without chat history', () => {
  const html = render(ArtifactsView, { artifacts: [{ id: 'table-1', type: 'table', title: 'Budget', source_refs: ['fact:investment'], citations,
    data: { columns: ['Metric', 'Value'], rows: [['Budget', '20 [fact:investment]']] } }], activeId: 'table-1', setActiveId() {} });
  assert.match(html, /href="#artifact-table-1-citation-1"/);
  assert.match(html, /id="artifact-table-1-citation-1"/);
  assert.match(html, /₹20 lakh/);
  assert.doesNotMatch(html, /\[fact:investment\]/);
});
