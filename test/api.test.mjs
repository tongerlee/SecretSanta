import test from 'node:test';
import assert from 'node:assert/strict';
import { GetItemCommand, ScanCommand } from '@aws-sdk/client-dynamodb';
import { start } from './server.mjs';

const s = await start();
test.after(() => s.stop());
let ip = 0;
const post = async (path, body, fromIp) => {
  const r = await fetch(s.apiUrl + path, { method: 'POST', headers: fromIp ? { 'x-test-ip': fromIp } : {}, body: JSON.stringify(body) });
  return { status: r.status, data: await r.json(), headers: r.headers };
};
const create = async (extra = {}) => (await post('/api/create', { title: 'Secret Santa 2026', budget: '$50 以内', exchangeOn: '2026-12-24', ...extra }, '10.0.0.' + (++ip))).data;
const rawItem = async (id) => (await s.raw.send(new GetItemCommand({ TableName: s.table, Key: { pk: { S: 'E#' + id } } }))).Item;
const PEOPLE = [['Jennifer', '1111', '好喝的红酒'], ['小雪', '2222', ''], ['Mia', '3333', '毛绒拖鞋'], ['阿杰', '4444', ''], ['Leo', '5555', '']];
/** create an event and sign everybody up; returns ids, keys and each person's device token */
async function party(people = PEOPLE) {
  const ev = await create();
  const tokens = {};
  for (const [name, pin, wish] of people) tokens[name] = (await post('/api/join', { id: ev.id, name, pin, wish })).data.token;
  const admin = (body) => post('/api/admin', { id: ev.id, adminKey: ev.adminKey, ...body });
  const me = (name) => post('/api/me', { id: ev.id, name, token: tokens[name] });
  return { ...ev, tokens, admin, me };
}

test('creating an event gives a link id and an organizer key that is never stored', async () => {
  const ev = await create();
  assert.match(ev.id, /^[a-z0-9]{12}$/); assert.match(ev.adminKey, /^[0-9a-f]{32}$/);
  assert.deepEqual(ev.state, { id: ev.id, title: 'Secret Santa 2026', budget: '$50 以内', exchangeOn: '2026-12-24', status: 'open', drawAt: 0, people: [], min: 3, deleteAt: Date.UTC(2027, 0, 1, 12), keepDays: 7, orgPin: false });
  const item = await rawItem(ev.id);
  assert.ok(!JSON.stringify(item).includes(ev.adminKey), 'only a hash of the organizer key is in the table');
  assert.equal(Number(item.ttl.N), Date.UTC(2027, 0, 1, 12) / 1000, 'DynamoDB TTL is stamped a week after the exchange date');
  assert.equal((await post('/api/create', { title: 'x', exchangeOn: '2020-01-01' }, '10.9.9.1')).data.error, 'date_past');
  assert.equal((await post('/api/create', { title: 'x', exchangeOn: '2026-02-31' }, '10.9.9.1')).data.error, 'bad_date');
  assert.equal((await post('/api/create', { title: 'x', exchangeOn: '2099-12-24' }, '10.9.9.1')).data.error, 'date_far');
  assert.equal((await post('/api/state', { id: 'aaaaaaaaaaaa' })).data.error, 'not_found');
  assert.equal((await post('/api/state', { id: { $gt: '' } })).data.error, 'not_found');
});

test('one address cannot create events without limit, and the address itself is not stored', async () => {
  const from = '203.0.113.77';
  for (let i = 0; i < 5; i++) assert.equal((await post('/api/create', { title: 'e' + i, exchangeOn: '2026-12-24' }, from)).status, 200);
  assert.equal((await post('/api/create', { title: 'one too many', exchangeOn: '2026-12-24' }, from)).data.error, 'slow_down');
  assert.equal((await post('/api/create', { title: 'someone else', exchangeOn: '2026-12-24' }, '203.0.113.78')).status, 200);
  const all = JSON.stringify((await s.raw.send(new ScanCommand({ TableName: s.table }))).Items);
  assert.ok(!all.includes(from) && all.includes('RL#'));
});

test('events are separate: same name in two events, tokens and keys do not cross over', async () => {
  const a = await party(), b = await party();
  assert.notEqual(a.id, b.id);
  assert.equal((await post('/api/me', { id: b.id, name: 'Jennifer', token: a.tokens.Jennifer })).data.error, 'bad_token');
  assert.equal((await post('/api/admin', { id: b.id, adminKey: a.adminKey, action: 'draw' })).data.error, 'forbidden');
  assert.equal((await post('/api/admin', { id: b.id, action: 'draw' })).data.error, 'forbidden');
  assert.equal((await a.admin({ action: 'draw' })).data.state.status, 'drawn');
  assert.equal((await post('/api/state', { id: b.id })).data.status, 'open');
});

test('sign-up rules, and the device gets a token instead of keeping the PIN', async () => {
  const ev = await create();
  const j = (body) => post('/api/join', { id: ev.id, ...body });
  assert.equal((await j({ name: '  ', pin: '1234' })).data.error, 'bad_name');
  assert.equal((await j({ name: 'X', pin: '12' })).data.error, 'bad_pin_format');
  const first = await j({ name: 'Jennifer', pin: '1111', wish: '好喝的红酒' });
  assert.equal(first.status, 200); assert.ok(first.data.token.length >= 30); assert.equal(first.data.giftTo, null);
  assert.equal((await j({ name: ' jennifer ', pin: '9999' })).data.error, 'name_taken');
  assert.equal((await j({ name: 'ＪＥＮＮＩＦＥＲ', pin: '9999' })).data.error, 'name_taken');

  const item = JSON.stringify(await rawItem(ev.id));
  assert.ok(!item.includes(first.data.token) && !item.includes('"1111"'), 'neither the token nor the PIN is stored as-is');
  assert.equal((await post('/api/me', { id: ev.id, name: 'Jennifer', token: first.data.token })).status, 200);
  assert.equal((await post('/api/me', { id: ev.id, name: 'Jennifer', token: 'x'.repeat(32) })).data.error, 'bad_token');
  assert.equal((await post('/api/me', { id: ev.id, name: 'Jennifer', pin: '1111' })).data.error, 'bad_token', 'a PIN is not accepted where a token belongs');

  // a second device signs in with the PIN and gets its own token; signing out kills only that one
  const second = await post('/api/login', { id: ev.id, name: 'Jennifer', pin: '1111' });
  assert.notEqual(second.data.token, first.data.token);
  assert.equal((await post('/api/logout', { id: ev.id, name: 'Jennifer', token: second.data.token })).status, 200);
  assert.equal((await post('/api/me', { id: ev.id, name: 'Jennifer', token: second.data.token })).data.error, 'bad_token');
  assert.equal((await post('/api/me', { id: ev.id, name: 'Jennifer', token: first.data.token })).status, 200);
});

test('wrong PINs lock the name; an organizer reset signs every device out and leaves a trace', async () => {
  const p = await party(PEOPLE.slice(0, 3));
  const login = (pin) => post('/api/login', { id: p.id, name: '小雪', pin });
  for (let left = 4; left >= 1; left--) { const r = await login('0000'); assert.equal(r.status, 401); assert.equal(r.data.left, left); }
  assert.equal((await login('0000')).data.error, 'locked');
  assert.equal((await login('2222')).data.error, 'locked', 'right PIN is refused while locked');
  assert.equal((await p.me('小雪')).status, 200, 'the device that is already signed in keeps working');
  assert.equal((await p.admin({ action: 'list' })).data.people.find((x) => x.name === '小雪').locked, true);
  assert.equal((await p.admin({ action: 'setPin', name: '小雪', pin: '8888' })).status, 200);
  assert.equal((await p.me('小雪')).data.error, 'bad_token', 'old tokens die with the old PIN');
  const back = await login('8888');
  assert.equal(back.status, 200); assert.ok(back.data.me.pinResetAt > 0);
  assert.equal((await p.me('Jennifer')).data.me.pinResetAt, 0);
});

test('twelve people signing up at the same moment all get in', async () => {
  const ev = await create();
  const names = Array.from({ length: 12 }, (_, i) => 'guest' + i);
  const results = await Promise.all(names.map((name) => post('/api/join', { id: ev.id, name, pin: '7777' })));
  assert.deepEqual(results.map((r) => r.status), names.map(() => 200));
  assert.equal((await post('/api/state', { id: ev.id })).data.people.length, 12);
});

test('draw: everyone gets exactly one other person, and only their own result', async () => {
  const p = await party();
  assert.equal((await p.admin({ action: 'draw' })).data.state.status, 'drawn');
  const adminSees = await p.admin({ action: 'list' });
  assert.ok(!JSON.stringify(adminSees.data).includes('giftTo'), 'the organizer response carries no pairs');
  const pub = await post('/api/state', { id: p.id });
  assert.deepEqual(Object.keys(pub.data).sort(), ['budget', 'deleteAt', 'drawAt', 'exchangeOn', 'id', 'keepDays', 'min', 'orgPin', 'people', 'status', 'title']);

  const gives = {};
  for (const [name] of PEOPLE) {
    const r = await p.me(name);
    assert.deepEqual(Object.keys(r.data).sort(), ['giftTo', 'me', 'missed', 'state']);
    gives[name] = r.data.giftTo.name;
  }
  assert.deepEqual(Object.values(gives).sort(), PEOPLE.map((x) => x[0]).sort(), 'a permutation');
  for (const [name] of PEOPLE) assert.notEqual(gives[name], name, 'nobody draws themselves');

  const santa = PEOPLE.find(([name]) => gives[name] === 'Jennifer')[0];
  assert.equal((await p.me(santa)).data.giftTo.wish, '好喝的红酒');
  assert.equal((await post('/api/wish', { id: p.id, name: 'Jennifer', token: p.tokens.Jennifer, wish: '改主意了：咖啡豆' })).status, 200);
  assert.equal((await p.me(santa)).data.giftTo.wish, '改主意了:咖啡豆');
  assert.equal((await post('/api/me', { id: p.id, name: 'Mia', token: p.tokens.Jennifer })).data.error, 'bad_token', "someone else's token does not open Mia");
  assert.equal((await post('/api/join', { id: p.id, name: 'Late', pin: '1234' })).data.error, 'closed');
  assert.equal((await post('/api/leave', { id: p.id, name: 'Leo', token: p.tokens.Leo })).data.error, 'closed');
  assert.equal((await p.admin({ action: 'remove', name: 'Leo' })).data.error, 'closed');
  assert.equal((await p.admin({ action: 'draw' })).data.error, 'already_drawn');

  const item = await rawItem(p.id);
  for (const [name] of PEOPLE) assert.ok(!Buffer.from(item.draw.S, 'base64').toString('utf8').includes(name.toLowerCase()), 'pairs are not readable at a glance');
});

test('reopen clears results; a deadline in the past draws on the next visit', async () => {
  const p = await party();
  await p.admin({ action: 'draw' });
  assert.equal((await p.admin({ action: 'reopen' })).data.state.status, 'open');
  assert.equal((await p.me('Leo')).data.giftTo, null);
  assert.equal((await post('/api/leave', { id: p.id, name: 'Leo', token: p.tokens.Leo })).status, 200);
  const late = await post('/api/join', { id: p.id, name: 'Late', pin: '1234' });
  assert.equal((await p.admin({ action: 'settings', title: 'Renamed', budget: '$60', exchangeOn: '2026-12-25', drawAt: Date.now() + 400 })).data.state.status, 'open');
  await new Promise((r) => setTimeout(r, 500));
  const after = await post('/api/state', { id: p.id });
  assert.equal(after.data.status, 'drawn'); assert.equal(after.data.title, 'Renamed'); assert.equal(after.data.exchangeOn, '2026-12-25');
  const mine = await post('/api/me', { id: p.id, name: 'Late', token: late.data.token });
  assert.ok(mine.data.giftTo && mine.data.giftTo.name !== 'Late' && mine.data.missed === false);
  assert.equal((await p.admin({ action: 'settings', title: 'Renamed', budget: '$60', exchangeOn: '2020-01-01' })).data.error, 'date_past', 'a past date is refused instead of deleting the event');
});

test('a week after the exchange date the event is gone; the organizer can also delete it at once', async () => {
  const p = await party(), q = await party();
  process.env.SANTA_CLOCK_SKEW_MS = String(Date.UTC(2027, 0, 2) - Date.now());      // jump to 2 Jan 2027
  try {
    assert.equal((await post('/api/state', { id: p.id })).data.error, 'not_found');
    assert.equal(await rawItem(p.id), undefined, 'the record itself is deleted, not just hidden');
    assert.equal((await p.me('Jennifer')).data.error, 'not_found');
    assert.equal((await post('/api/join', { id: p.id, name: 'Ghost', pin: '1234' })).data.error, 'not_found');
  } finally { delete process.env.SANTA_CLOCK_SKEW_MS; }
  assert.equal((await post('/api/admin', { id: q.id, adminKey: 'wrong', action: 'destroy' })).data.error, 'forbidden');
  assert.equal((await q.admin({ action: 'destroy' })).status, 200);
  assert.equal(await rawItem(q.id), undefined);
  assert.equal((await post('/api/state', { id: q.id })).data.error, 'not_found');
});

test('the browser can call the API from another origin; odd requests are refused cleanly', async () => {
  const pre = await fetch(s.apiUrl + '/api/state', { method: 'OPTIONS', headers: { origin: s.siteUrl, 'access-control-request-method': 'POST' } });
  assert.equal(pre.status, 204); assert.equal(pre.headers.get('access-control-allow-origin'), s.siteUrl);
  assert.equal((await post('/api/nope', {})).status, 404);
  assert.equal((await fetch(s.apiUrl + '/api/join', { method: 'POST', body: '{not json' })).status, 400);
  assert.equal((await fetch(s.apiUrl + '/api/join', { method: 'POST', body: JSON.stringify({ name: 'x'.repeat(5000) }) })).status, 413);
  assert.equal((await fetch(s.apiUrl + '/api/state', { method: 'DELETE' })).status, 405);
  assert.deepEqual(await (await fetch(s.apiUrl + '/')).json(), { service: 'secret-santa', ok: true });
});

test('awkward names: reserved words draw like anyone else; blank-looking and direction-flipping names are tidied', async () => {
  const ch = (...codes) => String.fromCodePoint(...codes);
  const ev = await create({ title: 'T'.repeat(29) + '🎄🎄' });
  assert.equal(ev.state.title, 'T'.repeat(29) + '🎄', 'a title is cut by whole characters, never through the middle of an emoji');
  const tokens = new Map();           // a Map, not {}: with a plain object the name "__proto__" would silently vanish (the very bug under test)
  const join = (name) => post('/api/join', { id: ev.id, name, pin: '1234' });
  const signUp = async (typed, stored = typed) => {
    const r = await join(typed);
    assert.equal(r.status, 200, typed); assert.equal(r.data.me.name, stored); tokens.set(stored, r.data.token);
  };
  for (const name of ['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf']) await signUp(name);
  // names that would show as an empty gap on everyone's screen
  const blanks = { 'zero-width spaces': ch(0x200b, 0x200b), 'hangul filler': ch(0x3164), 'braille blank': ch(0x2800, 0x20, 0x2800), 'direction override': ch(0x202e),
    'byte-order mark': ch(0xfeff), 'a lone joiner': ch(0x200d), 'control characters': ch(0x07, 0x1b), 'a lone accent': ch(0x301) };
  for (const [what, name] of Object.entries(blanks)) assert.equal((await join(name)).data.error, 'bad_name', what);
  // hidden characters inside a real name are dropped, so look-alike duplicates are caught too
  await signUp(ch(0x202e) + 'gnp.exe', 'gnp.exe');
  await signUp('An' + ch(0x200b) + 'na', 'Anna');
  assert.equal((await join('anna')).data.error, 'name_taken');
  await signUp('x'.repeat(23) + '🎄' + 'yyy', 'x'.repeat(23) + '🎄');
  await signUp('👨‍👩‍👧 fam');                                  // emoji glued with zero-width joiners must survive

  assert.equal((await post('/api/admin', { id: ev.id, adminKey: ev.adminKey, action: 'draw' })).status, 200);
  const gives = new Map();
  for (const [name, token] of tokens) {
    const r = await post('/api/me', { id: ev.id, name, token });
    assert.ok(r.data.giftTo && !r.data.missed, name + ' has nobody to give to');
    gives.set(name, r.data.giftTo.name);
  }
  assert.equal(gives.size, 9);
  assert.deepEqual([...gives.values()].sort(), [...gives.keys()].sort(), 'everyone receives exactly once');
  assert.ok([...gives].every(([a, b]) => a !== b));
});

test('a PIN is hashed once per request and the same PIN still signs in afterwards (also after an organizer reset)', async () => {
  const p = await party();
  assert.equal((await post('/api/login', { id: p.id, name: 'Mia', pin: '3333' })).status, 200);
  assert.equal((await post('/api/login', { id: p.id, name: 'Mia', pin: '3334' })).data.error, 'bad_pin');
  await p.admin({ action: 'setPin', name: 'Mia', pin: '8080' });
  assert.equal((await post('/api/login', { id: p.id, name: 'Mia', pin: '3333' })).data.error, 'bad_pin');
  assert.equal((await post('/api/login', { id: p.id, name: 'Mia', pin: '8080' })).status, 200);
  const item = JSON.stringify(await rawItem(p.id));
  const salts = [...item.matchAll(/"salt":\{"S":"([^"]+)"/g)].map((m) => m[1]);
  assert.equal(new Set(salts).size, salts.length, 'every person has their own salt');
});

test('a lost organizer link: the role tied to one sign-up comes back with that name + PIN, and the old key dies', async () => {
  const p = await party();
  // nothing tied yet: nobody can claim, however right their PIN is
  assert.equal((await post('/api/claimAdmin', { id: p.id, name: 'Jennifer', pin: '1111' })).data.error, 'not_organizer');
  assert.equal((await p.admin({ action: 'list' })).data.organizerName, '');
  assert.equal((await p.me('Jennifer')).data.me.organizer, false);
  // tie it to Jennifer; only the organizer key can do that, and only to someone on the list
  assert.equal((await post('/api/admin', { id: p.id, adminKey: 'f'.repeat(32), action: 'linkOrganizer', name: 'Jennifer' })).status, 403);
  assert.equal((await p.admin({ action: 'linkOrganizer', name: 'Nobody' })).data.error, 'no_such_person');
  const linked = await p.admin({ action: 'linkOrganizer', name: 'Jennifer' });
  assert.equal(linked.data.organizerName, 'Jennifer');
  assert.equal((await p.me('Jennifer')).data.me.organizer, true);
  assert.equal((await p.me('Mia')).data.me.organizer, false);
  assert.ok(!JSON.stringify(await rawItem(p.id)).includes('1111'), 'still no PIN in the table');
  // the wrong person, a wrong PIN (counted like a sign-in), then the right one
  assert.equal((await post('/api/claimAdmin', { id: p.id, name: 'Mia', pin: '3333' })).data.error, 'not_organizer');
  const wrong = await post('/api/claimAdmin', { id: p.id, name: 'Jennifer', pin: '9999' });
  assert.equal(wrong.status, 401); assert.equal(wrong.data.left, 4);
  const claimed = await post('/api/claimAdmin', { id: p.id, name: 'Jennifer', pin: '1111' });
  assert.equal(claimed.status, 200); assert.match(claimed.data.adminKey, /^[0-9a-f]{32}$/); assert.notEqual(claimed.data.adminKey, p.adminKey);
  assert.equal(claimed.data.organizerName, 'Jennifer');
  // the old link is dead, the new one works, and the sign-in itself still works too
  assert.equal((await p.admin({ action: 'list' })).status, 403);
  const fresh = (body) => post('/api/admin', { id: p.id, adminKey: claimed.data.adminKey, ...body });
  assert.equal((await fresh({ action: 'list' })).status, 200);
  assert.equal((await post('/api/login', { id: p.id, name: 'Jennifer', pin: '1111' })).status, 200);
  // five wrong PINs on the claim lock the name for sign-in as well
  for (let i = 0; i < 5; i++) await post('/api/claimAdmin', { id: p.id, name: 'Jennifer', pin: '0000' });
  assert.equal((await post('/api/login', { id: p.id, name: 'Jennifer', pin: '1111' })).data.error, 'locked');
  // untie it; and a tie dies with the sign-up it was tied to
  assert.equal((await fresh({ action: 'linkOrganizer', name: '' })).data.organizerName, '');
  await fresh({ action: 'linkOrganizer', name: 'Mia' });
  assert.equal((await fresh({ action: 'remove', name: 'Mia' })).data.organizerName, '');
  await post('/api/join', { id: p.id, name: 'Mia', pin: '7777', wish: '' });
  assert.equal((await post('/api/claimAdmin', { id: p.id, name: 'Mia', pin: '7777' })).data.error, 'not_organizer', 'a new Mia inherits nothing');
  await fresh({ action: 'linkOrganizer', name: 'Leo' });
  await post('/api/leave', { id: p.id, name: 'Leo', token: p.tokens.Leo });
  assert.equal((await fresh({ action: 'list' })).data.organizerName, '');
});

test('an organizer PIN set at creation (or later) brings the controls back from any device', async () => {
  // creation validates it, and stores only a hash
  assert.equal((await post('/api/create', { title: 'x', exchangeOn: '2026-12-24', orgPin: '12' })).data.error, 'bad_pin_format');
  const ev = (await post('/api/create', { title: 'Spare key', exchangeOn: '2026-12-24', orgPin: '246810' })).data;
  assert.ok(ev.adminKey && ev.state.orgPin === true);
  assert.ok(!JSON.stringify(await rawItem(ev.id)).includes('246810'));
  assert.equal((await post('/api/state', { id: ev.id })).data.orgPin, true);
  // wrong PINs count and lock; the right one mints a new key and retires the old
  const wrong = await post('/api/claimAdmin', { id: ev.id, orgPin: '000000' });
  assert.equal(wrong.status, 401); assert.equal(wrong.data.left, 4);
  const got = await post('/api/claimAdmin', { id: ev.id, orgPin: '246810' });
  assert.equal(got.status, 200); assert.match(got.data.adminKey, /^[0-9a-f]{32}$/); assert.notEqual(got.data.adminKey, ev.adminKey);
  assert.equal((await post('/api/admin', { id: ev.id, adminKey: ev.adminKey, action: 'list' })).status, 403);
  assert.equal((await post('/api/admin', { id: ev.id, adminKey: got.data.adminKey, action: 'list' })).status, 200);
  for (let i = 0; i < 5; i++) await post('/api/claimAdmin', { id: ev.id, orgPin: '111111' });
  assert.equal((await post('/api/claimAdmin', { id: ev.id, orgPin: '246810' })).data.error, 'locked');
  // an older event without one: the claim says so; the organizer can set one from the controls
  const p = await party();
  assert.equal((await p.admin({ action: 'list' })).data.orgPin, false);
  assert.equal((await post('/api/claimAdmin', { id: p.id, orgPin: '1234' })).data.error, 'no_org_pin');
  assert.equal((await p.admin({ action: 'setOrgPin', pin: 'abcd' })).data.error, 'bad_pin_format');
  assert.equal((await p.admin({ action: 'setOrgPin', pin: '9876' })).data.orgPin, true);
  assert.equal((await post('/api/claimAdmin', { id: p.id, orgPin: '9876' })).status, 200);
  // changing it needs the organizer key, and the old PIN then stops working
  const k2 = (await post('/api/claimAdmin', { id: p.id, orgPin: '9876' })).data.adminKey;
  assert.equal((await post('/api/admin', { id: p.id, adminKey: k2, action: 'setOrgPin', pin: '5555' })).status, 200);
  assert.equal((await post('/api/claimAdmin', { id: p.id, orgPin: '9876' })).status, 401);
  assert.equal((await post('/api/claimAdmin', { id: p.id, orgPin: '5555' })).status, 200);
});
