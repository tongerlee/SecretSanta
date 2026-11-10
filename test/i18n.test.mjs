// Guards the four languages against drifting apart: same keys everywhere, nothing left untranslated,
// nothing the page asks for that is missing, and no half-filled sentences.
//   node --test test/i18n.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (name) => readFileSync(new URL('../page/' + name, import.meta.url), 'utf8');
const L = new Function(read('i18n.js') + '\nreturn SANTA_I18N;')();
const app = read('app.js');
const body = read('body.html');
const base = L.dict[L.order[0]];
const sample = (key, fn) => (key.startsWith('fmt') ? [12, 24, 4, 20, 5].slice(0, fn.length) : ['71', '82', '93'].slice(0, fn.length));

test('all four languages are present and complete', () => {
  assert.deepEqual(L.order, ['zh', 'en', 'ko', 'vi']);
  for (const code of L.order) {
    assert.ok(L.names[code] && L.html[code], code + ' needs a name and an html lang');
    const d = L.dict[code];
    assert.deepEqual(Object.keys(d).sort(), Object.keys(base).sort(), code + ' has different keys');
    for (const [key, v] of Object.entries(d)) {
      assert.equal(typeof v, typeof base[key], `${code}.${key} should be a ${typeof base[key]}`);
      if (typeof v === 'function') assert.equal(v.length, base[key].length, `${code}.${key} takes a different number of values`);
    }
  }
});

test('every sentence comes out whole', () => {
  for (const code of L.order) {
    for (const [key, v] of Object.entries(L.dict[code])) {
      const args = typeof v === 'function' ? sample(key, v) : [];
      const out = typeof v === 'function' ? v(...args) : v;
      assert.equal(typeof out, 'string', `${code}.${key}`);
      assert.ok(out.trim().length > 0, `${code}.${key} is empty`);
      assert.ok(!/undefined|NaN|\[object|\$\{|null/.test(out), `${code}.${key} -> ${out}`);
      assert.equal(out, out.normalize('NFC'), `${code}.${key} is not composed (NFC)`);
      if (!key.startsWith('fmt')) for (const a of args) assert.ok(out.includes(a), `${code}.${key} drops a value: ${out}`);
    }
  }
});

test('plural and clock forms', () => {
  const { en, ko, vi, zh } = L.dict;
  assert.equal(en.e_bad_pin(1), 'Wrong PIN. 1 try left.');
  assert.match(en.e_bad_pin(4), /4 tries left/);
  assert.match(en.e_locked(1), /1 minute\./);
  assert.match(en.e_locked(10), /10 minutes\./);
  // Thursday 24 December, and 8:05 in the evening / 12:30 at night / noon
  assert.equal(zh.fmtDay(12, 24, 4), '12/24 周四');
  assert.equal(en.fmtDay(12, 24, 4), 'Thu, Dec 24');
  assert.equal(ko.fmtDay(12, 24, 4), '12월 24일 (목)');
  assert.equal(vi.fmtDay(12, 24, 4), 'Thứ Năm, 24/12');
  assert.equal(zh.fmtTime(12, 20, 0, 20, 5), '12/20 周日 20:05');
  assert.equal(en.fmtTime(12, 20, 0, 20, 5), 'Sun, Dec 20, 8:05 PM');
  assert.equal(en.fmtTime(12, 20, 0, 0, 30), 'Sun, Dec 20, 12:30 AM');
  assert.equal(en.fmtTime(12, 20, 0, 12, 0), 'Sun, Dec 20, 12:00 PM');
  assert.equal(ko.fmtTime(12, 20, 0, 20, 5), '12월 20일 (일) 오후 8:05');
  assert.equal(ko.fmtTime(12, 20, 0, 0, 30), '12월 20일 (일) 오전 12:30');
  assert.equal(vi.fmtTime(12, 20, 0, 20, 5), '20:05 Chủ Nhật, 20/12');
  assert.equal(en.fmtMd(12, 31), 'Dec 31');
  assert.equal(ko.fmtMd(12, 31), '12월 31일');
  assert.equal(vi.fmtMd(12, 31), '31/12');
});

test('the page only asks for words that exist, and no word is left unused', () => {
  const used = new Set();
  const html = body.replace(/<!--[\s\S]*?-->/g, '');
  const code = app.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
  for (const m of html.matchAll(/data-i18n(?:-ph|-aria|-alt)?="([^"]+)"/g)) used.add(m[1]);
  for (const m of code.matchAll(/\bt\('([A-Za-z_0-9]+)'\s*[,)]/g)) used.add(m[1]);
  const codes = /const ERR_CODES = \[([^\]]+)\]/.exec(app)[1].match(/[a-z_]+/g);
  assert.ok(codes.length >= 10);
  for (const c of codes) used.add('e_' + c);
  for (const key of used) assert.ok(key in base, 'the page asks for a missing word: ' + key);
  for (const key of Object.keys(base)) assert.ok(used.has(key), 'never shown anywhere: ' + key);
  // nothing written straight into the page any more (the pets' name tags are names, not sentences)
  const han = /\p{Script=Han}+/gu;
  assert.equal(html.replace(/<span class="nametag[^>]*>[^<]*<\/span>/g, '').match(han), null, 'text left in body.html');
  assert.equal(code.match(han), null, 'text left in app.js');
});
