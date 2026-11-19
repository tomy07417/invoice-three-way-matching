// Render the real shared component with installed React; no browser, database or network.
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { renderToStaticMarkup } from '../node_modules/react-dom/server.node.js';
import React from '../node_modules/react/index.js';
import ts from '../node_modules/typescript/lib/typescript.js';
import { loadTypeScript } from './load-typescript.mjs';

export const DOCUMENT_ID = '11111111-2222-4333-8444-555555555555';

export async function documentMarkup(preview = true) {
  const { api } = await loadTypeScript('../lib/api.ts');
  const require = createRequire(new URL('../package.json', import.meta.url));
  const source = await readFile(new URL('../components/shared/workspace.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  const componentRequire = name => name === '../../lib/api' ? { api }
    : name === './workspace.css' ? {} : require(name);
  new Function('require', 'exports', outputText)(componentRequire, exports);
  return renderToStaticMarkup(React.createElement(exports.OriginalDocument, {
    invoiceId: DOCUMENT_ID, title: 'Original invoice document', preview, download: true,
  }));
}
