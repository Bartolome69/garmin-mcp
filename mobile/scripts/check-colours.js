#!/usr/bin/env node
// Token lint (build prompt, sections 2 and 13): no colour literal anywhere in app
// code outside the generated tokens. Any hex, rgb()/hsl() with numbers, a named
// colour used as a value, or a platform colour API fails. app.json may only use
// approved token values (its splash and icon backgrounds).
//
//   node scripts/check-colours.js        exit 1 and list each line that breaks the rule

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src');
const GENERATED = new Set([path.join(SRC, 'theme', 'tokens.ts'), path.join(SRC, 'theme', 'design-tokens.json')]);
const NAMED = ['white', 'black', 'red', 'green', 'blue', 'gray', 'grey', 'orange', 'yellow', 'purple', 'pink',
  'brown', 'cyan', 'magenta', 'silver', 'gold', 'navy', 'teal', 'maroon', 'lime', 'olive', 'aqua', 'fuchsia'];

const RULES = [
  { name: 'hex colour', re: /#[0-9a-fA-F]{3,8}\b/ },
  { name: 'rgb/hsl colour', re: /\b(rgba?|hsla?)\(\s*[\d.]/ },
  { name: 'named colour', re: new RegExp(`['"\`](${NAMED.join('|')})['"\`]`, 'i') },
  { name: 'platform colour API', re: /\b(PlatformColor|DynamicColorIOS)\s*\(/ },
];

/** Code without comments, keeping line numbers, so "#22" in a comment isn't a colour. */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`])\/\/.*$/gm, (m, lead) => lead + ' '.repeat(m.length - lead.length));
}

function scanText(text, file) {
  const found = [];
  stripComments(text).split('\n').forEach((line, i) => {
    for (const rule of RULES) {
      const m = rule.re.exec(line);
      if (m) found.push({ file, line: i + 1, rule: rule.name, match: m[0] });
    }
  });
  return found;
}

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === 'node_modules' || e.name === '__snapshots__' ? [] : walk(p);
    return /\.(tsx?|jsx?|mjs|cjs|json)$/.test(e.name) ? [p] : [];
  });
}

function approvedValues() {
  const t = JSON.parse(fs.readFileSync(path.join(SRC, 'theme', 'design-tokens.json'), 'utf8'));
  return new Set(Object.values(t.colors).flatMap((m) => Object.values(m)).map((v) => String(v).toUpperCase()));
}

function checkAppJson() {
  const allowed = approvedValues();
  const text = fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8');
  return [...text.matchAll(/#[0-9a-fA-F]{3,8}\b/g)]
    .filter((m) => !allowed.has(m[0].toUpperCase()))
    .map((m) => ({ file: path.join(ROOT, 'app.json'), line: text.slice(0, m.index).split('\n').length,
      rule: 'colour not in the approved tokens', match: m[0] }));
}

function check() {
  // App code only: tests may hold literal colours to compare against.
  const files = walk(SRC).filter((f) => !GENERATED.has(f) && !/\.test\.[jt]sx?$/.test(f));
  return [...files.flatMap((f) => scanText(fs.readFileSync(f, 'utf8'), f)), ...checkAppJson()];
}

module.exports = { check, scanText };

if (require.main === module) {
  const problems = check();
  for (const p of problems) console.error(`${path.relative(ROOT, p.file)}:${p.line}  ${p.rule}: ${p.match}`);
  if (problems.length) {
    console.error(`\n${problems.length} colour literal(s) outside src/theme/tokens.ts. Use a token.`);
    process.exit(1);
  }
  console.log('check-colours: no colour literals outside the tokens');
}
