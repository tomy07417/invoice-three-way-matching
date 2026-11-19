// Real React SSR presentation of deterministic document validation, no browser/network.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import React from '../node_modules/react/index.js';
import { renderToStaticMarkup } from '../node_modules/react-dom/server.node.js';
import ts from '../node_modules/typescript/lib/typescript.js';

async function markup(validation) {
  const source = await readFile(new URL('../components/shared/workspace.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const require = createRequire(new URL('../package.json', import.meta.url));
  const exports = {};
  new Function('require', 'exports', outputText)(name => name === '../../lib/api' ? { api: {} }
    : name === './workspace.css' ? {} : require(name), exports);
  return renderToStaticMarkup(React.createElement(exports.DocumentValidationNotice, { validation }));
}

for (const [kind, outcome, state, code, ruleOutcome] of [
  ['quotation', 'Rejected', 'REJECTED', 'INVALID_DOCUMENT_TYPE', 'REJECT'],
  ['unknown', 'Held for Review', 'REVIEW_REQUIRED', 'DOCUMENT_TYPE_UNCERTAIN', 'HOLD'],
]) {
  test(`${kind} visibly explains its disposition and no matching`, async () => {
    const reason = kind === 'unknown' ? 'Document type is uncertain. Three-Way Matching was not run.'
      : 'Document type "quotation" is not an invoice. Three-Way Matching was not run.';
    const html = await markup({ policy_version: 'document-type-v1', document_type: kind,
      automatic_outcome: outcome, operational_state: state, findings: [{ code, outcome: ruleOutcome, reason }] });
    assert.match(html, /Document validation/);
    assert.ok(html.includes(outcome));
    assert.ok(html.includes(code));
    assert.match(html, /Three-Way Matching was not run/);
    assert.ok(!html.includes('Auto-Pay'));
    assert.ok(!html.includes('Incomplete — no final outcome'));
  });
}
test('historical invoices have no invented document validation panel', async () => {
  assert.equal(await markup(undefined), '');
});
