const { check, scanText } = require('./check-colours');

describe('token lint', () => {
  it('finds no colour literal in the app', () => {
    expect(check()).toEqual([]);
  });
  it('catches hex, rgb, named and platform colours, but not comments', () => {
    const found = scanText([
      "const a = { color: '#fff' };",
      'const b = `rgba(0, 0, 0, 0.5)`;',
      "const c = { backgroundColor: 'white' };",
      "const d = PlatformColor('label');",
      '// PR #22 and #fff in a comment are fine',
      'const e = `rgba(${r}, ${g}, ${b}, 1)`; // built from a token',
    ].join('\n'), 'x.ts');
    expect(found.map((f) => [f.line, f.rule])).toEqual([
      [1, 'hex colour'], [2, 'rgb/hsl colour'], [3, 'named colour'], [4, 'platform colour API'],
    ]);
  });
});
