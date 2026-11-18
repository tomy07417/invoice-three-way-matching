import { readFile } from 'node:fs/promises';
import ts from '../node_modules/typescript/lib/typescript.js';

// Type-only imports disappear; transport modules deliberately have no runtime dependencies.
export async function loadTypeScript(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}
