/// <reference types="node" />
/**
 * @jest-environment node
 */
// Section 6: the engines are pure. No UI, no network, no storage, no Coach.
import { describe, expect, it } from '@jest/globals';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';

describe('domain/', () => {
  const files = readdirSync(__dirname).filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'));
  it.each(files)('%s imports only other domain files', (file) => {
    const source = readFileSync(join(__dirname, file), 'utf8');
    const imports = [...source.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(imports.filter((i) => !i.startsWith('./'))).toEqual([]);
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    expect(code).not.toMatch(/\bfetch\(|XMLHttpRequest|coach/i);
  });
});
