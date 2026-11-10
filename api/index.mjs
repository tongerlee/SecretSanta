// Secret Santa API: one Lambda behind a Function URL. The pages are static files on Amplify Hosting and call this.
// Storage is DynamoDB, ONE item per event, updated with optimistic locking (read, change, conditional put, retry),
// so every change is atomic without transactions.
//
// Who can see what, per event:
//   - anyone with the event link: title, budget, exchange date, status, the list of names
//   - a participant (name + PIN once, then a random token kept on their device): their own wish and,
//     after the draw, who they give to
//   - the organizer (the secret key in their own link): names and wishes; can draw / reopen / remove /
//     reset a PIN / delete the event. The API never returns the pairs to the organizer either.
// Pairs are stored scrambled so a glance at the table does not spoil the draw. That is a spoiler guard, not
// encryption: whoever owns the AWS account can always dig them out.
// An event deletes itself a week after its exchange date (DynamoDB TTL, plus a check on every read).

import { createHash, randomBytes, randomInt, scryptSync, timingSafeEqual } from 'node:crypto';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DeleteCommand, DynamoDBDocumentClient, GetCommand, PutCommand, UpdateCommand } from '@aws-sdk/lib-dynamodb';

const TABLE = process.env.TABLE_NAME;
const MIN_PEOPLE = 3;
const MAX_PEOPLE = 60;
const MAX_FAILS = 5;                 // wrong PINs in a row before a lock
const LOCK_MS = 10 * 60 * 1000;
const KEEP_DAYS = 7;                 // an event is deleted this many days after its exchange date
const MAX_AHEAD_DAYS = 400;          // how far in the future an exchange date may be
const CREATE_PER_HOUR = 5;           // new events per network address per hour
const MAX_SESSIONS = 5;              // devices one person can stay signed in on
const DAY = 24 * 60 * 60 * 1000;
// Tests move the clock with this; it is never set in Lambda.
const clock = () => Date.now() + Number(process.env.SANTA_CLOCK_SKEW_MS || 0);

const ddb = DynamoDBDocumentClient.from(
  new DynamoDBClient(process.env.DDB_ENDPOINT
    ? { endpoint: process.env.DDB_ENDPOINT, region: 'local', credentials: { accessKeyId: 'local', secretAccessKey: 'local' } }
    : {}),
  { marshallOptions: { removeUndefinedValues: true } },
);

/* ---------- small helpers ---------- */
// Characters that draw nothing or flip the reading direction. Left in, they make "blank" or misleading names.
// (Zero-width joiners stay: family emoji and several scripts need them.)
const HIDDEN = [[0x202a, 0x202e], [0x2066, 0x2069], 0x200e, 0x200f, 0x061c, 0x200b, 0x2060, 0xfeff, 0x115f, 0x1160, 0x3164, 0xffa0, 0x2800, 0x180e, 0x034f];
const cp = (n) => '\\u{' + n.toString(16) + '}';
const HIDDEN_RE = new RegExp('[\\p{Cs}' + HIDDEN.map((x) => (Array.isArray(x) ? cp(x[0]) + '-' + cp(x[1]) : cp(x))).join('') + ']', 'gu');
// Tidy a piece of text someone typed: one spelling per character, no control or hidden characters, single spaces,
// and cut by whole characters so an emoji at the limit is never sliced in half.
const clean = (s, max) => (typeof s === 'string'
  ? Array.from(s.normalize('NFKC').replace(/\p{Cc}/gu, ' ').replace(HIDDEN_RE, '').replace(/\s+/g, ' ').trim()).slice(0, max).join('').trim()
  : '');
const hasSomethingToSee = (s) => /[\p{L}\p{N}\p{P}\p{S}]/u.test(s);
const nameKey = (name) => clean(name, 24).toLowerCase();
const pinOk = (pin) => typeof pin === 'string' && /^\d{4,8}$/.test(pin);
const fail = (code, error, extra) => ({ code, body: { error, ...extra } });
const sha = (s) => createHash('sha256').update(String(s)).digest();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sameHash = (stored, fresh) => { const a = Buffer.from(stored || '', 'base64'); return a.length === fresh.length && timingSafeEqual(a, fresh); };

const ID_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789';     // no look-alikes such as l/1 or o/0
const newId = () => Array.from({ length: 12 }, () => ID_CHARS[randomInt(ID_CHARS.length)]).join('');
const validId = (id) => typeof id === 'string' && /^[a-z0-9]{12}$/.test(id);
const itemKey = (id) => ({ pk: 'E#' + id });

/* ---------- dates and retention ---------- */
function parseDay(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || '');
  if (!m) return 0;
  const t = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  return new Date(t).getUTCDate() === +m[3] ? t : 0;      // rejects 2026-02-31 and the like
}
// midnight UTC of the day, plus the keep period, plus a day and a half so no time zone is cut short
const expiresAt = (exchangeOn) => { const t = parseDay(exchangeOn); return t ? t + (KEEP_DAYS + 1.5) * DAY : 0; };
function checkDate(exchangeOn, now) {
  const exp = expiresAt(exchangeOn);
  if (!exp) return 'bad_date';
  if (exp <= now) return 'date_past';                      // would be deleted on the very next request
  if (parseDay(exchangeOn) > now + MAX_AHEAD_DAYS * DAY) return 'date_far';
  return '';
}

/* ---------- storage ---------- */
async function load(id, now) {
  if (!validId(id)) return null;
  const out = await ddb.send(new GetCommand({ TableName: TABLE, Key: itemKey(id), ConsistentRead: true }));
  const doc = out.Item;
  if (!doc) return null;
  if (now >= expiresAt(doc.exchangeOn)) {                  // TTL deletion can lag; never serve an expired event
    await ddb.send(new DeleteCommand({ TableName: TABLE, Key: itemKey(id) }));
    return null;
  }
  return doc;
}

/** Run `change(doc, now)` against the latest state and save it if it asks to. Retries when someone else saved first. */
async function mutate(id, change) {
  for (let attempt = 0; attempt < 12; attempt++) {
    const now = clock();
    const doc = await load(id, now);
    if (!doc) return fail(404, 'not_found');
    const before = doc.v;
    const drewNow = autoDraw(doc, now);
    const res = change(doc, now);
    if (res.destroy) {
      await ddb.send(new DeleteCommand({ TableName: TABLE, Key: itemKey(id) }));
      return res;
    }
    if (!res.save && !drewNow) return res;
    doc.v = before + 1;
    doc.ttl = Math.floor(expiresAt(doc.exchangeOn) / 1000);
    try {
      await ddb.send(new PutCommand({
        TableName: TABLE, Item: doc,
        ConditionExpression: '#v = :v', ExpressionAttributeNames: { '#v': 'v' }, ExpressionAttributeValues: { ':v': before },
      }));
      return res;
    } catch (e) {
      if (e.name !== 'ConditionalCheckFailedException') throw e;
      await sleep(15 + randomInt(60) * (attempt + 1));
    }
  }
  return fail(503, 'busy');
}

/** At most a few new events per network address per hour. Only a hash of the address is kept, for two hours. */
async function mayCreate(ip, now) {
  const hour = Math.floor(now / 3600000);
  const out = await ddb.send(new UpdateCommand({
    TableName: TABLE,
    Key: { pk: 'RL#' + sha(ip + ':' + hour).toString('hex').slice(0, 32) },
    UpdateExpression: 'SET #t = if_not_exists(#t, :ttl) ADD #n :one',
    ExpressionAttributeNames: { '#n': 'n', '#t': 'ttl' },
    ExpressionAttributeValues: { ':one': 1, ':ttl': Math.floor(now / 1000) + 7200 },
    ReturnValues: 'UPDATED_NEW',
  }));
  return out.Attributes.n <= CREATE_PER_HOUR;
}

/* ---------- PINs and device tokens ---------- */
// scrypt is slow on purpose (it is what makes guessing PINs from a stolen table expensive). A request can be replayed
// against the table several times when many people act at once (see mutate), so each request carries a small
// notebook, `work`, and pays for scrypt once instead of once per replay. Nothing is remembered between requests.
const newWork = () => ({ derived: new Map(), fresh: null });
function derive(pin, saltB64, work) {
  if (!work.derived.has(saltB64)) work.derived.set(saltB64, scryptSync(pin, Buffer.from(saltB64, 'base64'), 32));
  return work.derived.get(saltB64);
}
function hashPin(pin, work) {
  if (!work.fresh) {
    const salt = randomBytes(12).toString('base64');
    work.fresh = { salt, hash: derive(pin, salt, work).toString('base64') };
  }
  return { ...work.fresh };
}
function checkPin(person, pin, work) {
  if (!pinOk(pin) || !person.salt || !person.hash) return false;
  return sameHash(person.hash, derive(pin, person.salt, work));
}
/** A random pass for one device. The device keeps the pass, the table keeps only its hash, the PIN stays in nobody's storage. */
function issueToken(person) {
  const token = randomBytes(24).toString('base64url');
  person.sessions = [...(person.sessions || []), sha(token).toString('base64')].slice(-MAX_SESSIONS);
  return token;
}
const tokenIndex = (person, token) => (typeof token === 'string' && token.length >= 20 && token.length <= 80
  ? (person.sessions || []).findIndex((s) => sameHash(s, sha(token))) : -1);

/* ---------- the pairs, stored scrambled ---------- */
function scramble(drawId, buf) {
  for (let off = 0, i = 0; off < buf.length; i++) {
    const ks = sha(`santa:${drawId}:${i}`);
    for (let j = 0; j < ks.length && off < buf.length; j++, off++) buf[off] ^= ks[j];
  }
  return buf;
}
const seal = (drawId, obj) => scramble(drawId, Buffer.from(JSON.stringify(obj), 'utf8')).toString('base64');
function unseal(drawId, text) {
  try { return JSON.parse(scramble(drawId, Buffer.from(text, 'base64')).toString('utf8')); } catch { return null; }
}

/* ---------- the draw: shuffle into a circle, everyone gives to the next person ---------- */
function doDraw(doc, now) {
  const n = doc.people.length;
  const order = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) { const j = randomInt(i + 1); [order[i], order[j]] = [order[j], order[i]]; }
  const to = Object.create(null);      // a bare map: a person may be called "__proto__" or "constructor"
  for (let i = 0; i < n; i++) to[doc.people[order[i]].key] = doc.people[order[(i + 1) % n]].key;
  doc.drawId = randomBytes(6).toString('hex');
  doc.draw = seal(doc.drawId, { to });
  doc.status = 'drawn';
  doc.drawnAt = now;
}
function autoDraw(doc, now) {
  if (doc.status !== 'open' || !doc.drawAt || now < doc.drawAt || doc.people.length < MIN_PEOPLE) return false;
  doDraw(doc, now);
  return true;
}
function recipientOf(doc, person) {
  if (doc.status !== 'drawn') return null;
  const d = unseal(doc.drawId, doc.draw);
  const key = d && d.to && Object.hasOwn(d.to, person.key) ? d.to[person.key] : null;
  return (typeof key === 'string' && doc.people.find((p) => p.key === key)) || null;
}

/* ---------- what each caller gets to see ---------- */
const publicState = (doc) => ({
  id: doc.id, title: doc.title, budget: doc.budget, exchangeOn: doc.exchangeOn, status: doc.status, drawAt: doc.drawAt || 0,
  people: doc.people.map((p) => p.name), min: MIN_PEOPLE, deleteAt: expiresAt(doc.exchangeOn), keepDays: KEEP_DAYS,
});
function personalView(doc, person) {
  const r = recipientOf(doc, person);
  return {
    state: publicState(doc),
    me: { name: person.name, wish: person.wish || '', pinResetAt: person.pinResetAt || 0 },
    giftTo: r ? { name: r.name, wish: r.wish || '' } : null,
    missed: doc.status === 'drawn' && !r,
  };
}
const adminView = (doc, now) => ({
  state: publicState(doc),
  people: doc.people.map((p) => ({ name: p.name, wish: p.wish || '', locked: (p.lockUntil || 0) > now })),
});

/* ---------- creating an event ---------- */
async function create(b, ip) {
  const now = clock();
  const bad = checkDate(b.exchangeOn, now);
  if (bad) return fail(400, bad);
  if (!(await mayCreate(ip || 'unknown', now))) return fail(429, 'slow_down');
  const id = newId();
  const adminKey = randomBytes(16).toString('hex');
  const doc = {
    ...itemKey(id), v: 1, id,
    title: clean(b.title, 30) || 'Secret Santa', budget: clean(b.budget, 20), exchangeOn: b.exchangeOn,
    status: 'open', drawAt: 0, drawId: '', draw: '', people: [],
    adminHash: sha(adminKey).toString('base64'), createdAt: now, ttl: Math.floor(expiresAt(b.exchangeOn) / 1000),
  };
  await ddb.send(new PutCommand({ TableName: TABLE, Item: doc, ConditionExpression: 'attribute_not_exists(pk)' }));
  return { code: 200, body: { id, adminKey, state: publicState(doc) } };     // the key is shown once and never stored
}

/* ---------- participant actions ---------- */
function join(doc, b, now, work) {
  if (doc.status !== 'open') return fail(409, 'closed');
  const name = clean(b.name, 24);
  if (!name || !hasSomethingToSee(name)) return fail(400, 'bad_name');
  if (!pinOk(b.pin)) return fail(400, 'bad_pin_format');
  if (doc.people.some((p) => p.key === nameKey(name))) return fail(409, 'name_taken');
  if (doc.people.length >= MAX_PEOPLE) return fail(409, 'full');
  const person = { key: nameKey(name), name, wish: clean(b.wish, 100), at: now, fails: 0, lockUntil: 0, sessions: [], ...hashPin(b.pin, work) };
  const token = issueToken(person);
  doc.people.push(person);
  return { code: 200, body: { ...personalView(doc, person), token }, save: true };
}

/** Name + PIN, used once per device. Wrong PINs are counted and saved; five in a row lock that name for ten minutes. */
function login(doc, b, now, work) {
  const person = doc.people.find((p) => p.key === nameKey(b.name));
  if (!person) return fail(404, 'no_such_person');
  if ((person.lockUntil || 0) > now) return fail(429, 'locked', { retryAfter: Math.ceil((person.lockUntil - now) / 1000) });
  if (!checkPin(person, b.pin, work)) {
    person.fails = (person.fails || 0) + 1;
    if (person.fails >= MAX_FAILS) {
      person.fails = 0; person.lockUntil = now + LOCK_MS;
      return { ...fail(429, 'locked', { retryAfter: LOCK_MS / 1000 }), save: true };
    }
    return { ...fail(401, 'bad_pin', { left: MAX_FAILS - person.fails }), save: true };
  }
  person.fails = 0;
  const token = issueToken(person);
  return { code: 200, body: { ...personalView(doc, person), token }, save: true };
}

/** Everything after sign-in is done with the device token. */
function withToken(action) {
  return (doc, b, now) => {
    const person = doc.people.find((p) => p.key === nameKey(b.name));
    if (!person) return fail(404, 'no_such_person');
    const at = tokenIndex(person, b.token);
    if (at < 0) return fail(401, 'bad_token');
    return action(doc, person, b, now, at);
  };
}
const me = withToken((doc, person) => ({ code: 200, body: personalView(doc, person) }));
const setWish = withToken((doc, person, b) => {
  person.wish = clean(b.wish, 100);
  return { code: 200, body: personalView(doc, person), save: true };
});
const leave = withToken((doc, person) => {
  if (doc.status !== 'open') return fail(409, 'closed');
  doc.people = doc.people.filter((p) => p !== person);
  return { code: 200, body: { state: publicState(doc) }, save: true };
});
const logout = withToken((doc, person, b, now, at) => {
  person.sessions.splice(at, 1);
  return { code: 200, body: { ok: true }, save: true };
});

/* ---------- organizer actions ---------- */
function admin(doc, b, now, work) {
  if (typeof b.adminKey !== 'string' || !sameHash(doc.adminHash, sha(b.adminKey))) return fail(403, 'forbidden');
  const done = () => ({ code: 200, body: adminView(doc, now), save: true });
  const target = () => doc.people.find((p) => p.key === nameKey(b.name));
  switch (b.action) {
    case 'list':
      return { code: 200, body: adminView(doc, now) };
    case 'draw':
      if (doc.status !== 'open') return fail(409, 'already_drawn');
      if (doc.people.length < MIN_PEOPLE) return fail(409, 'too_few');
      doDraw(doc, now);
      return done();
    case 'reopen':
      doc.status = 'open'; doc.draw = ''; doc.drawId = ''; doc.drawAt = 0;
      return done();
    case 'remove':
      if (doc.status !== 'open') return fail(409, 'closed');
      if (!target()) return fail(404, 'no_such_person');
      doc.people = doc.people.filter((p) => p !== target());
      return done();
    case 'setPin': {
      const person = target();
      if (!person) return fail(404, 'no_such_person');
      if (!pinOk(b.pin)) return fail(400, 'bad_pin_format');
      // signs that person out everywhere, and leaves a trace they see next time, so a reset can't be used to peek unnoticed
      Object.assign(person, hashPin(b.pin, work), { fails: 0, lockUntil: 0, sessions: [], pinResetAt: now });
      return done();
    }
    case 'settings': {
      if (typeof b.exchangeOn === 'string' && b.exchangeOn !== doc.exchangeOn) {
        const bad = checkDate(b.exchangeOn, now);
        if (bad) return fail(400, bad);
        doc.exchangeOn = b.exchangeOn;
      }
      doc.title = clean(b.title, 30) || doc.title;
      doc.budget = clean(b.budget, 20);
      const at = Number(b.drawAt);
      doc.drawAt = Number.isFinite(at) && at > 0 ? Math.floor(at) : 0;
      return done();
    }
    case 'destroy':
      return { code: 200, body: { ok: true }, destroy: true };
    default:
      return fail(400, 'bad_action');
  }
}

/* ---------- HTTP ---------- */
// CORS headers are added by the Function URL itself (see template.yaml), not here.
const json = (code, body) => ({
  statusCode: code,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  body: JSON.stringify(body),
});
const state = (doc) => ({ code: 200, body: publicState(doc) });
const ROUTES = { '/api/state': state, '/api/join': join, '/api/login': login, '/api/me': me, '/api/wish': setWish, '/api/leave': leave, '/api/logout': logout, '/api/admin': admin };

export async function handler(event) {
  const method = event.requestContext?.http?.method || 'GET';
  const path = event.rawPath || '/';
  try {
    if (method === 'GET' && path === '/') return json(200, { service: 'secret-santa', ok: true });
    if (method !== 'POST') return json(405, { error: 'method_not_allowed' });

    const raw = event.body ? (event.isBase64Encoded ? Buffer.from(event.body, 'base64').toString('utf8') : event.body) : '';
    if (raw.length > 4096) return json(413, { error: 'too_large' });
    let body;
    try { body = JSON.parse(raw || '{}'); } catch { return json(400, { error: 'bad_json' }); }
    if (!body || typeof body !== 'object' || Array.isArray(body)) return json(400, { error: 'bad_json' });

    if (path === '/api/create') {
      const res = await create(body, event.requestContext?.http?.sourceIp);
      return json(res.code, res.body);
    }
    const action = ROUTES[path];
    if (!action) return json(404, { error: 'not_found' });
    const work = newWork();
    const res = await mutate(body.id, (doc, now) => action(doc, body, now, work));
    return json(res.code, res.body);
  } catch (e) {
    console.error(e);
    return json(500, { error: 'server_error' });
  }
}
