(() => {
'use strict';
const $ = (id) => document.getElementById(id);
const API = String(window.SANTA_API || '').replace(/\/+$/, '');      // set by config.js; empty means same origin
const EVENT_ID = new URLSearchParams(location.search).get('e') || '';
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } },
  set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* optional */ } },
};
const K_ME = 'santa:' + EVENT_ID + ':me';          // { name, token } - a random pass for this device, never the PIN
const K_ADMIN = 'santa:' + EVENT_ID + ':admin';    // the organizer key, only on the organizer's device
const K_LIST = 'santa:events';                     // events this device has touched, to find them again
const K_LANG = 'santa:lang';                       // the language picked on this device
const pageUrl = location.origin + location.pathname;
const eventUrl = (id) => pageUrl + '?e=' + id;

/* ---------- language ----------
   The words are in i18n.js. Which language to show, in order: a ?lang=ko in the link, then what this device picked
   before, then the phone's own language, then English. Tapping a language button switches on the spot, no reload. */
const L = SANTA_I18N;
const matchLang = (tag) => { const s = String(tag || '').toLowerCase(); return L.order.find((c) => s === c || s.startsWith(c + '-')) || null; };
function pickLang() {
  const params = new URLSearchParams(location.search);
  const fromLink = matchLang(params.get('lang'));
  if (params.has('lang')) {
    // Remember it, then take it out of the address bar so it does not override a later choice on reload.
    if (fromLink) store.set(K_LANG, fromLink);
    params.delete('lang');
    const rest = params.toString();
    try { history.replaceState(null, '', location.pathname + (rest ? '?' + rest : '') + location.hash); } catch (e) { /* fine */ }
  }
  const saved = fromLink || matchLang(store.get(K_LANG));
  if (saved) return saved;
  const wanted = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
  for (const tag of wanted) { const m = matchLang(tag); if (m) return m; }
  return L.fallback;
}
let lang = pickLang();
function t(key, ...args) {
  let v = L.dict[lang][key];
  if (v == null) v = L.dict[L.fallback][key];
  return typeof v === 'function' ? v(...args) : (v == null ? key : v);
}
function applyLang() {
  document.documentElement.lang = L.html[lang];
  for (const n of document.querySelectorAll('[data-i18n]')) n.textContent = t(n.dataset.i18n);
  for (const n of document.querySelectorAll('[data-i18n-ph]')) n.placeholder = t(n.dataset.i18nPh);
  for (const n of document.querySelectorAll('[data-i18n-aria]')) n.setAttribute('aria-label', t(n.dataset.i18nAria));
  for (const n of document.querySelectorAll('[data-i18n-alt]')) n.alt = t(n.dataset.i18nAlt);
  $('motionBtn').setAttribute('aria-label', motionPaused ? t('playAll') : t('pauseAll'));
  for (const b of $('langs').children) b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
}
let motionPaused = false;                          // one switch for pets, snow and lights
let rerender = () => {};                           // set by whichever page is showing
function setLang(next) {
  if (!L.dict[next] || next === lang) return;
  lang = next; store.set(K_LANG, lang);
  // Messages already on screen were written in the old language; drop them rather than leave a mix.
  for (const id of ['createErr', 'wishOk', 'wishErr', 'joinErr', 'adminOk', 'adminErr']) $(id).textContent = '';
  $('toast').hidden = true;
  applyLang(); rerender(); movedNote();
}
for (const code of L.order) {
  const b = el('button', '', L.names[code]); b.type = 'button'; b.dataset.lang = code; b.lang = L.html[code];
  $('langs').append(b);
}
$('langs').addEventListener('click', (ev) => { const b = ev.target.closest('button[data-lang]'); if (b) setLang(b.dataset.lang); });

/** The handwriting fonts cover Latin, Chinese and Korean but not Vietnamese letters: a name like "Hương" would come out
    half in one font, half in another, so names with those letters get a face that has all of them. */
const beyondPlainLatin = (text) => Array.from(String(text || '')).some((ch) => {
  const c = ch.codePointAt(0);
  return (c >= 0x100 && c <= 0x24F) || (c >= 0x300 && c <= 0x36F) || (c >= 0x1E00 && c <= 0x1EFF);
});
/** The festive title face has plain Latin only. Any title with other letters (Chinese, Korean, Vietnamese) is set in
    the ordinary bold face instead of coming out half and half. */
const titleNeedsPlain = (text) => Array.from(String(text || '')).some((ch) => {
  const c = ch.codePointAt(0);
  return c > 0x7F && !/[\p{P}\p{S}\p{Z}]/u.test(ch);
});
/** A colored circle with the first letter of a name, so a list of names reads as people, not as form controls. */
const AVATAR_COLORS = ['#b8203a', '#1c7450', '#c77d12', '#2b6cb0', '#7a3e9d', '#566e64'];
function avatar(name) {
  const s = String(name || '');
  let first = s;
  try { first = [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(s)][0].segment; } catch (e) { first = Array.from(s)[0] || '?'; }
  let hsh = 0; for (const ch of s) hsh = (hsh * 31 + ch.codePointAt(0)) >>> 0;
  const n = el('span', 'avatar', first.toLocaleUpperCase()); n.style.background = AVATAR_COLORS[hsh % AVATAR_COLORS.length]; n.setAttribute('aria-hidden', 'true');
  return n;
}
const chip = (name, asButton) => {
  const n = el(asButton ? 'button' : 'li', 'chip'); if (asButton) { n.type = 'button'; n.dataset.name = name; }
  n.append(avatar(name), el('span', 'chip-name', name)); return n;
};

/* ---------- talking to the API ---------- */
// The body is sent without a JSON content type on purpose: that keeps it a "simple" cross-origin request,
// so the browser does not need a preflight round trip for every call. The server parses JSON either way.
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
async function api(path, body) {
  for (let attempt = 0; ; attempt++) {
    let res, data = null;
    try { res = await fetch(API + '/api/' + path, { method: 'POST', cache: 'no-store', body: JSON.stringify(body || {}) }); }
    catch (e) { return { ok: false, status: 0, data: { error: 'network' } }; }
    try { data = await res.json(); } catch (e) { /* not json */ }
    // "Turned away at the door": when too many requests land in the same instant, AWS answers 429 before our code runs,
    // and our own code answers 503 "busy" when it could not save. Either way nothing happened, so asking again is safe.
    // Real answers always carry an error code (a locked PIN is 429 + "locked") and are never asked twice.
    const turnedAway = (res.status === 429 && !(data && data.error)) || (res.status === 503 && !!data && data.error === 'busy');
    if (!turnedAway || attempt >= 4) return { ok: res.ok, status: res.status, data: data || {} };
    await pause(250 + Math.random() * 400 * (attempt + 1));
  }
}
// Error codes the server sends that have their own sentence (key "e_<code>" in i18n.js). Anything else reads as a network hiccup.
const ERR_CODES = ['bad_name', 'bad_pin_format', 'name_taken', 'closed', 'full', 'no_such_person', 'too_few', 'already_drawn',
  'forbidden', 'not_found', 'bad_date', 'date_past', 'date_far', 'slow_down'];
function errText(d) {
  const code = d && d.error;
  if (code === 'bad_pin') return t('e_bad_pin', d.left || 0);
  if (code === 'locked') return t('e_locked', Math.max(1, Math.ceil((d.retryAfter || 60) / 60)));
  return ERR_CODES.includes(code) ? t('e_' + code) : t('e_network');
}

/* ---------- small UI helpers ---------- */
// Dates are put together by hand per language (12/24 周四 · Thu, Dec 24 · 12월 24일 (목) · Thứ Năm, 24/12) so every phone shows the same thing.
function dayText(ymd) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd || '');
  return m ? t('fmtDay', +m[2], +m[3], new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()) : '—';
}
const timeText = (ms) => { const d = new Date(ms); return t('fmtTime', d.getMonth() + 1, d.getDate(), d.getDay(), d.getHours(), d.getMinutes()); };
/** This device's time zone, short ("PST", "GMT+9"), so a time next to it is never read in the wrong zone. */
function tzName(ms) {
  try {
    const part = new Intl.DateTimeFormat(L.html[lang], { timeZoneName: 'short' }).formatToParts(new Date(ms || Date.now())).find((p) => p.type === 'timeZoneName');
    if (part && part.value) return part.value;
  } catch (e) { /* very old browser */ }
  const off = -new Date(ms || Date.now()).getTimezoneOffset(), sign = off < 0 ? '-' : '+', abs = Math.abs(off);
  return 'GMT' + sign + Math.floor(abs / 60) + (abs % 60 ? ':' + String(abs % 60).padStart(2, '0') : '');
}
const monthDayText = (ms) => { const d = new Date(ms); return t('fmtMd', d.getMonth() + 1, d.getDate()); };
function localInputValue(ms) {
  if (!ms) return '';
  return new Date(ms - new Date(ms).getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
let toastTimer = 0;
function toast(text, ms) {
  const box = $('toast'); box.textContent = text; box.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { box.hidden = true; }, ms || 2800);
}
const dlg = $('dlg');
let dlgDone = null, dlgValue = null;
function openDialog({ title, body, ok, cancel, pin }) {
  if (dlgDone) { const prev = dlgDone; dlgDone = null; prev(null); }
  $('dlgTitle').textContent = title;
  const b = $('dlgBody'); b.replaceChildren();
  for (const line of [].concat(body || [])) b.append(typeof line === 'string' ? el('p', '', line) : line);
  dlgValue = null;
  if (pin) {
    const input = el('input', 'pin'); input.id = 'dlgPin'; input.setAttribute('aria-label', 'PIN');
    input.type = pin === 'show' ? 'text' : 'password'; input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.maxLength = 8; input.autocomplete = 'off';
    b.append(input); dlgValue = input;
  }
  $('dlgOk').textContent = ok || t('ok');
  $('dlgCancel').textContent = cancel || t('cancel');
  if (!dlg.open) { if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', ''); }
  if (dlgValue) dlgValue.focus();
  return new Promise((resolve) => { dlgDone = resolve; });
}
function closeDialog(result) {
  const done = dlgDone; dlgDone = null;
  if (dlg.open) { if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open'); }
  $('dlgBody').replaceChildren();
  if (done) done(result);
}
$('dlgForm').addEventListener('submit', (ev) => { ev.preventDefault(); closeDialog(dlgValue ? dlgValue.value.trim() : true); });
$('dlgCancel').addEventListener('click', () => closeDialog(null));
dlg.addEventListener('close', () => { if (dlgDone && !dlg.open) closeDialog(null); });
document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('button[data-copy]');
  if (!btn) return;
  const input = $(btn.dataset.copy);
  let p; try { p = navigator.clipboard.writeText(input.value); } catch (e) { p = Promise.reject(e); }
  p.then(() => toast(t('copied')), () => { input.focus(); input.select(); toast(t('copyManual')); });
});
/** Remember that this device has to do with an event, so the home page can list it. */
function remember(id, title, isAdmin) {
  const list = (store.get(K_LIST) || []).filter((e) => e && e.id !== id);
  const old = (store.get(K_LIST) || []).find((e) => e && e.id === id);
  list.unshift({ id, title, admin: !!(isAdmin || (old && old.admin)) });
  store.set(K_LIST, list.slice(0, 20));
}
function forgetEvent(id) { store.set(K_LIST, (store.get(K_LIST) || []).filter((e) => e && e.id !== id)); }

/* ---------- a burst of confetti (skipped when the system asks for reduced motion) ---------- */
function confetti(x, y) {
  if (reduced) return;
  const cv = $('fx'), ctx = cv.getContext && cv.getContext('2d');
  if (!ctx) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  cv.width = innerWidth * dpr; cv.height = innerHeight * dpr; cv.hidden = false; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const colors = ['#b8203a', '#f2c14e', '#2f8a66', '#ffffff', '#e8495e', '#dcbf93'];
  const bits = Array.from({ length: 90 }, () => {
    const a = -Math.PI / 2 + (Math.random() - .5) * 1.6, v = 380 + Math.random() * 420;
    return { x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: 3 + Math.random() * 4, c: colors[(Math.random() * colors.length) | 0], spin: Math.random() * 6.28, w: 6 + Math.random() * 6 };
  });
  let last = 0, t0 = 0;
  const step = (now) => {
    if (!t0) t0 = now; const dt = last ? Math.min(.04, (now - last) / 1000) : 0; last = now;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    for (const b of bits) {
      b.vy += 900 * dt; b.vx *= .985; b.x += b.vx * dt; b.y += b.vy * dt; b.spin += dt * 8;
      ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.spin); ctx.fillStyle = b.c; ctx.globalAlpha = Math.max(0, 1 - (now - t0) / 1600);
      ctx.fillRect(-b.w / 2, -b.r / 2, b.w, b.r); ctx.restore();
    }
    if (now - t0 < 1600) requestAnimationFrame(step); else { ctx.clearRect(0, 0, innerWidth, innerHeight); cv.hidden = true; }
  };
  requestAnimationFrame(step);
}

/* ---------- the gift tag ---------- */
const PAW = '<svg class="paw" viewBox="0 0 40 40" aria-hidden="true"><ellipse cx="20" cy="26" rx="9" ry="7.5"/><ellipse cx="8.5" cy="16" rx="3.6" ry="4.6"/><ellipse cx="16" cy="9.5" rx="3.6" ry="4.8"/><ellipse cx="24" cy="9.5" rx="3.6" ry="4.8"/><ellipse cx="31.5" cy="16" rx="3.6" ry="4.6"/></svg>';
function buildTag(a) {
  const tag = el('div', 'tag');
  const inner = el('div', 'tag-inner');
  const back = el('button', 'tag-side tag-back'); back.type = 'button';
  back.append(el('span', 'hole'), el('span', 'for', t('tagFor', a.g)), el('span', 'cta', t('tapReveal')), el('span', 'peek', t('peek')));
  const front = el('div', 'tag-side tag-front'); front.hidden = true;
  front.insertAdjacentHTML('beforeend', PAW);
  const name = el('p', 'tag-name' + (beyondPlainLatin(a.r) ? ' plain' : ''), a.r);
  const stamp = el('span', 'tag-stamp', 'Approved for unnecessary thoughtfulness'); stamp.lang = 'en'; stamp.setAttribute('aria-hidden', 'true');
  front.append(el('span', 'hole'), stamp, el('p', 'tag-k', t('tagTo')), name);
  front.append(el('p', 'tag-wish', a.w ? t('tagWish', a.w) : t('tagNoWish')));
  const meta = [a.b && t('tagBudget', a.b), a.d && t('tagDate', a.d)].filter(Boolean).join(' · ');
  if (meta) front.append(el('p', 'tag-meta', meta));
  const hide = el('button', 'link', t('hide')); hide.type = 'button';
  front.append(hide);
  inner.append(back, front); tag.append(inner);
  const flip = (open) => {
    const swap = () => { back.hidden = open; front.hidden = !open; (open ? hide : back).focus({ preventScroll: true }); };
    if (reduced) return swap();
    inner.classList.add('fold');
    setTimeout(() => { swap(); inner.classList.remove('fold'); }, 200);
  };
  back.addEventListener('click', () => { const r = back.getBoundingClientRect(); confetti(r.left + r.width / 2, r.top + r.height / 2); flip(true); });
  hide.addEventListener('click', () => flip(false));
  return tag;
}

/* =====================================================================================
   Home page (no ?e=): create an event
   ===================================================================================== */
function renderMine() {
  const mine = (store.get(K_LIST) || []).filter((e) => e && typeof e.id === 'string');
  $('mineCard').hidden = mine.length === 0 || !$('createdCard').hidden;
  $('mineList').replaceChildren();
  for (const e of mine) {
    const li = el('li'); const who = el('div', 'who');
    const a = el('a', '', e.title || 'Secret Santa'); a.href = eventUrl(e.id);
    const b = el('b'); b.append(a); who.append(b, el('small', '', e.admin ? t('iOrganize') : t('iJoined')));
    li.append(avatar(e.title || 'S'), who); $('mineList').append(li);
  }
}
function startHome() {
  const now = new Date();
  const year = now.getMonth() === 11 && now.getDate() > 24 ? now.getFullYear() + 1 : now.getFullYear();
  $('skeleton').hidden = true;
  $('homeFields').hidden = false;
  $('createCard').hidden = false;
  $('newTitle').value = 'Secret Santa ' + year;
  $('newDate').value = year + '-12-24';
  $('newDate').min = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  rerender = renderMine;
  renderMine();

  $('createForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if ($('createBtn').disabled) return;
    if (!$('newDate').value) { $('createErr').textContent = t('e_bad_date'); return; }
    $('createBtn').disabled = true; $('createBtn').textContent = t('loading'); $('createForm').setAttribute('aria-busy', 'true'); $('createErr').textContent = '';
    const r = await api('create', { title: $('newTitle').value, budget: $('newBudget').value, exchangeOn: $('newDate').value });
    $('createBtn').disabled = false; $('createBtn').textContent = t('createBtn'); $('createForm').removeAttribute('aria-busy');
    if (!r.ok) { $('createErr').textContent = errText(r.data); return; }
    const { id, adminKey, state } = r.data;
    store.set('santa:' + id + ':admin', adminKey);
    remember(id, state.title, true);
    $('newShare').value = eventUrl(id);
    $('newAdmin').value = eventUrl(id) + '#admin=' + adminKey;
    $('newOpen').href = eventUrl(id);
    $('createCard').hidden = true; $('mineCard').hidden = true; $('createdCard').hidden = false;
    $('createdCard').scrollIntoView({ block: 'start', behavior: reduced ? 'auto' : 'smooth' });
  });
}

/* =====================================================================================
   Event page (?e=<id>)
   ===================================================================================== */
const S = {
  state: null,              // public: title, budget, exchangeOn, status, drawAt, people[]
  me: null,                 // { name, token } remembered on this device
  view: null,               // my personal view from the server: { me, giftTo, missed }
  adminKey: null, admin: null,
  gone: false, loadError: false, busy: false, tagKey: '', note: '',
};

function render() {
  const st = S.state;
  $('goneCard').hidden = !S.gone;
  const note = S.gone ? '' : (st ? (S.loadError ? t('staleNote') : '') : (S.loadError ? t('offlineNote') : ''));
  $('notice').textContent = note; $('notice').hidden = !note;
  $('skeleton').hidden = S.gone || !!st || S.loadError;          // grey bars stand in for the cards until the first answer arrives
  for (const id of ['meCard', 'joinCard', 'rosterCard', 'adminCard']) if (S.gone || !st) $(id).hidden = true;
  $('eventFields').hidden = S.gone || !st;
  if (S.gone || !st) { $('statusPill').hidden = true; $('deadline').hidden = true; $('retention').textContent = ''; return; }

  const drawn = st.status === 'drawn';
  $('title').textContent = st.title; document.title = st.title;
  $('title').classList.toggle('plain', titleNeedsPlain(st.title));
  $('fBudget').textContent = st.budget || '—';
  $('fDate').textContent = dayText(st.exchangeOn);
  const pill = $('statusPill'); pill.hidden = false;
  pill.textContent = drawn ? t('pillDrawn') : t('pillOpen');
  pill.className = 'pill' + (drawn ? ' drawn' : '');
  $('deadline').hidden = drawn || !st.drawAt;
  if (st.drawAt) $('deadlineText').textContent = t('deadline', timeText(st.drawAt), tzName(st.drawAt));
  $('retention').textContent = t('retention', monthDayText(st.deleteAt));

  const v = S.view;
  $('meCard').hidden = !v;
  $('joinCard').hidden = !!v || drawn;
  if (v) {
    $('meTitle').textContent = drawn ? t('meDrawn', v.me.name) : t('meJoined', v.me.name);
    $('meStatus').textContent = v.giftTo ? '' : (v.missed ? t('missed') : t('waitDraw'));
    $('meStatus').hidden = !!v.giftTo;
    $('resetNote').hidden = !v.me.pinResetAt;
    if (v.me.pinResetAt) $('resetNote').textContent = t('pinWasReset', timeText(v.me.pinResetAt));
    // The language is part of the key so that switching language rewrites the tag too (it comes back face down).
    const key = v.giftTo ? [lang, v.me.name, v.giftTo.name, v.giftTo.wish, st.budget, st.exchangeOn].join('\n') : '';
    if (key !== S.tagKey) {
      S.tagKey = key; $('tagSlot').replaceChildren();
      if (v.giftTo) $('tagSlot').append(buildTag({ g: v.me.name, r: v.giftTo.name, w: v.giftTo.wish, b: st.budget, d: dayText(st.exchangeOn) }));
    }
    if (document.activeElement !== $('wishInput') && !S.busy) $('wishInput').value = v.me.wish;
    $('leaveBtn').hidden = drawn;
    $('nudge').hidden = drawn;
    $('nudgeText').textContent = t('nudge', st.people.length);
  } else if (S.tagKey) { S.tagKey = ''; $('tagSlot').replaceChildren(); }

  const n = st.people.length;
  $('rosterCard').hidden = n === 0;
  // A first-time visitor while sign-ups are open: "Sign up" leads, the people already in come second as small chips.
  // After the draw there is nothing to sign up for, so "tap your name" is the main thing and the chips are big.
  const secondary = !v && !drawn;
  $('rosterCard').classList.toggle('after-join', secondary);
  $('rosterTitle').textContent = v || secondary ? t('rosterIn', n) : t('rosterTapDrawn');
  $('rosterHint').textContent = v ? '' : (drawn ? t('rosterHintDrawn') : t('rosterTapOpen'));
  $('rosterHint').hidden = !!v;
  $('names').hidden = !!v; $('roster').hidden = !v;
  $('names').classList.toggle('big', drawn);
  const box = v ? $('roster') : $('names'); box.replaceChildren();
  for (const name of st.people) box.append(chip(name, !v));
  renderAdmin();
}
function renderAdmin() {
  const a = S.admin, st = S.state;
  $('adminCard').hidden = !a;
  if (!a) return;
  const drawn = st.status === 'drawn', n = a.people.length;
  $('adminStatus').textContent = drawn ? t('adminDrawn') : (n < st.min ? t('adminNeed', n, st.min) : t('adminReady', n));
  const list = $('adminList'); list.replaceChildren();
  for (const p of a.people) {
    const li = el('li'); const who = el('div', 'who'); who.append(el('b', '', p.name)); li.append(avatar(p.name));
    who.append(el('small', '', (p.wish ? t('wishOf', p.wish) : t('noWish')) + (p.locked ? t('pinLocked') : '')));
    const acts = el('div', 'row');
    const pin = el('button', 'btn sm', t('resetPin')); pin.type = 'button'; pin.dataset.act = 'setPin'; pin.dataset.name = p.name;
    acts.append(pin);
    if (!drawn) { const rm = el('button', 'btn sm', t('remove')); rm.type = 'button'; rm.dataset.act = 'remove'; rm.dataset.name = p.name; acts.append(rm); }
    li.append(who, acts); list.append(li);
  }
  $('drawBtn').hidden = drawn; $('reopenBtn').hidden = !drawn;
  $('drawBtn').disabled = S.busy || n < st.min;
  $('drawBtn').textContent = n < st.min ? t('drawBtnMin', st.min) : t('drawBtnN', n);
  $('manageSummary').textContent = t('managePeople', n);
  $('cfgDrawAtHint').textContent = t('cfgDrawAtHint', tzName(st.drawAt || Date.now()));
  $('shareUrl').value = eventUrl(EVENT_ID);
  $('adminUrl').value = eventUrl(EVENT_ID) + '#admin=' + S.adminKey;
  if (!$('settings').open) {
    $('cfgTitle').value = st.title; $('cfgBudget').value = st.budget; $('cfgExchangeOn').value = st.exchangeOn; $('cfgDrawAt').value = localInputValue(st.drawAt);
  }
}

/* ---------- loading and refreshing ---------- */
function forget() { S.me = null; S.view = null; store.set(K_ME, null); }
function gone() { S.gone = true; S.state = null; S.view = null; S.admin = null; forgetEvent(EVENT_ID); }
async function refresh() {
  if (S.gone) return;
  if (S.me) {
    const r = await api('me', Object.assign({ id: EVENT_ID }, S.me));
    if (r.ok) { S.view = r.data; S.state = r.data.state; S.loadError = false; }
    else if (r.data.error === 'not_found') gone();
    else if (r.status === 401 || r.status === 404) { S.note = r.status === 404 ? t('removedNote') : t('reenterPin'); S.state = null; forget(); }
    else S.loadError = true;
  }
  if (!S.gone && (!S.me || !S.state)) {
    const r = await api('state', { id: EVENT_ID });
    if (r.ok) { S.state = r.data; S.loadError = false; }
    else if (r.data.error === 'not_found') gone();
    else S.loadError = true;
  }
  if (!S.gone && S.adminKey) {
    const r = await api('admin', { id: EVENT_ID, adminKey: S.adminKey, action: 'list' });
    if (r.ok) { S.admin = r.data; S.state = r.data.state; }
    else if (r.status === 403) { S.admin = null; S.adminKey = null; store.set(K_ADMIN, null); toast(errText(r.data), 5000); }
  }
  if (S.state) remember(EVENT_ID, S.state.title, !!S.admin);
  if (S.note) { toast(S.note, 4000); S.note = ''; }
  render();
}

/* ---------- participant actions ---------- */
/** The server answers a join or a PIN sign-in with a random token; that token, not the PIN, is what this device keeps. */
function signedIn(data) {
  S.me = { name: data.me.name, token: data.token }; store.set(K_ME, S.me);
  S.view = data; S.state = data.state;
  render();
}
function wireEvent() {
  $('joinForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (S.busy) return;
    // normalize: "ễ" typed as one letter or as e + two marks must count as the same name
    const name = $('joinName').value.trim().normalize('NFC'), pin = $('joinPin').value.trim(), wish = $('joinWish').value.trim();
    if (!name) { $('joinErr').textContent = t('e_bad_name'); $('joinName').focus(); return; }
    if (!/^\d{4,8}$/.test(pin)) { $('joinErr').textContent = t('e_bad_pin_format'); $('joinPin').focus(); return; }
    S.busy = true; $('joinBtn').disabled = true; $('joinBtn').textContent = t('loading'); $('joinForm').setAttribute('aria-busy', 'true'); $('joinErr').textContent = '';
    const r = await api('join', { id: EVENT_ID, name, pin, wish });
    S.busy = false; $('joinBtn').disabled = false; $('joinBtn').textContent = t('joinBtn'); $('joinForm').removeAttribute('aria-busy');
    if (!r.ok) { if (r.data.error === 'not_found') { gone(); render(); } else $('joinErr').textContent = errText(r.data); return; }
    $('joinName').value = ''; $('joinPin').value = ''; $('joinWish').value = '';
    signedIn(r.data);
    toast(t('joinedToast', pin), 5000);
    if (S.adminKey) refresh();
  });
  $('names').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('button[data-name]');
    if (!btn) return;
    const name = btn.dataset.name;
    let note = '';
    for (;;) {
      const pin = await openDialog({ title: t('pinDlgTitle', name), body: note ? [note] : [t('pinDlgBody')], ok: t('enter'), pin: 'hide' });
      if (pin == null) return;
      const r = await api('login', { id: EVENT_ID, name, pin });
      if (r.ok) { signedIn(r.data); return; }
      if (r.data.error === 'not_found') { gone(); render(); return; }
      note = errText(r.data);
    }
  });
  $('wishForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!S.me || S.busy) return;
    S.busy = true; $('wishOk').textContent = ''; $('wishErr').textContent = '';
    const r = await api('wish', Object.assign({ id: EVENT_ID, wish: $('wishInput').value.trim() }, S.me));
    S.busy = false;
    if (!r.ok) { $('wishErr').textContent = errText(r.data); return; }
    S.view = r.data; S.state = r.data.state; $('wishOk').textContent = t('saved'); $('wishInput').blur(); render();
  });
  $('shareBtn').addEventListener('click', async () => {
    const url = eventUrl(EVENT_ID), title = (S.state && S.state.title) || 'Secret Santa';
    if (navigator.share) { try { await navigator.share({ title, url }); } catch (e) { /* closed the sheet */ } return; }
    let p; try { p = navigator.clipboard.writeText(url); } catch (e) { p = Promise.reject(e); }
    p.then(() => toast(t('copied')), () => toast(t('copyManual')));
  });
  $('logoutBtn').addEventListener('click', () => {
    if (S.me) api('logout', Object.assign({ id: EVENT_ID }, S.me));       // cancels this device's token on the server too
    forget(); render();
  });
  $('leaveBtn').addEventListener('click', async () => {
    if (!S.me) return;
    const yes = await openDialog({ title: t('leaveTitle'), body: t('leaveBody'), ok: t('leaveOk'), cancel: t('leaveKeep') });
    if (!yes) return;
    const r = await api('leave', Object.assign({ id: EVENT_ID }, S.me));
    if (!r.ok) { toast(errText(r.data), 4000); return; }
    forget(); S.state = r.data.state; toast(t('leftToast')); render();
    if (S.adminKey) refresh();
  });

  /* organizer */
  async function adminDo(body, okText) {
    if (S.busy) return false;
    S.busy = true; $('adminOk').textContent = ''; $('adminErr').textContent = ''; render();
    const r = await api('admin', Object.assign({ id: EVENT_ID, adminKey: S.adminKey }, body));
    S.busy = false;
    if (r.ok && r.data.state) { S.admin = r.data; S.state = r.data.state; $('adminOk').textContent = okText; }
    else if (!r.ok) $('adminErr').textContent = errText(r.data);
    if (body.action !== 'destroy') await refresh();
    return r.ok;
  }
  $('drawBtn').addEventListener('click', async () => {
    const names = S.admin ? S.admin.people.map((p) => p.name) : [];
    const yes = await openDialog({ title: t('drawDlgTitle', names.length), body: [names.join(t('listSep')), t('drawDlgBody')], ok: t('draw') });
    if (yes) adminDo({ action: 'draw' }, t('drawDone'));
  });
  $('reopenBtn').addEventListener('click', async () => {
    const yes = await openDialog({ title: t('reopenTitle'), body: t('reopenBody'), ok: t('reopenOk') });
    if (yes) adminDo({ action: 'reopen' }, t('reopened'));
  });
  $('adminList').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('button[data-act]');
    if (!btn) return;
    const name = btn.dataset.name;
    if (btn.dataset.act === 'remove') {
      const yes = await openDialog({ title: t('removeTitle', name), body: t('removeBody'), ok: t('remove') });
      if (yes) adminDo({ action: 'remove', name }, t('removed', name));
    } else {
      const pin = await openDialog({ title: t('setPinTitle', name), body: t('setPinBody'), ok: t('save'), pin: 'show' });
      if (pin == null) return;
      if (!/^\d{4,8}$/.test(pin)) { $('adminErr').textContent = t('e_bad_pin_format'); return; }
      adminDo({ action: 'setPin', name, pin }, t('newPin', name, pin));
    }
  });
  $('cfgForm').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const at = $('cfgDrawAt').value ? new Date($('cfgDrawAt').value).getTime() : 0;
    adminDo({ action: 'settings', title: $('cfgTitle').value, budget: $('cfgBudget').value, exchangeOn: $('cfgExchangeOn').value, drawAt: Number.isFinite(at) ? at : 0 }, t('settingsSaved'));
  });
  $('destroyBtn').addEventListener('click', async () => {
    const yes = await openDialog({ title: t('destroyTitle'), body: t('destroyBody'), ok: t('del') });
    if (!yes) return;
    if (await adminDo({ action: 'destroy' }, '')) { store.set(K_ADMIN, null); store.set(K_ME, null); gone(); render(); toast(t('destroyed')); }
  });
}

function startEvent() {
  document.documentElement.classList.add('event');      // smaller illustration: the sign-up or the result should be in view sooner
  $('homeLink').hidden = false;
  // The organizer link carries the key after '#', so it is never sent to any server as part of the address.
  // Keep it on this device, then tidy the address bar so a screenshot or a copied link does not leak it.
  const m = /^#admin=([A-Za-z0-9_-]{8,128})$/.exec(location.hash);
  if (m) { store.set(K_ADMIN, m[1]); try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* fine */ } }
  S.adminKey = m ? m[1] : store.get(K_ADMIN);
  if (typeof S.adminKey !== 'string') S.adminKey = null;
  S.me = store.get(K_ME);
  if (S.me && (typeof S.me.name !== 'string' || typeof S.me.token !== 'string')) S.me = null;
  wireEvent();
  rerender = render;
  render();
  refresh();
  // New sign-ups and the draw show up without anyone reloading.
  setInterval(() => { if (!document.hidden && !S.busy && !dlg.open) refresh(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !S.busy && !dlg.open) refresh(); });
}

/* ---------- falling snow, behind the cards ---------- */
(function snow() {
  const cv = $('snow');
  const ctx = cv.getContext && cv.getContext('2d');
  if (!ctx) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let w = 0, h = 0, flakes = [], raf = 0, last = 0;
  const flake = (anywhere) => ({ x: Math.random() * w, y: anywhere ? Math.random() * h : -4, r: 1 + Math.random() * 2.2, v: 14 + Math.random() * 26, sway: Math.random() * 6.28, o: .4 + Math.random() * .55 });
  function size() {
    w = cv.clientWidth; h = cv.clientHeight;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const want = Math.round(Math.min(110, Math.max(40, (w * h) / 8000)));
    while (flakes.length < want) flakes.push(flake(true));
    flakes.length = want;
    for (const f of flakes) { if (f.x > w) f.x = Math.random() * w; if (f.y > h) f.y = Math.random() * h; }
  }
  function draw() {
    ctx.clearRect(0, 0, w, h); ctx.fillStyle = '#fff';
    for (const f of flakes) { ctx.globalAlpha = f.o; ctx.beginPath(); ctx.arc(f.x + Math.sin(f.sway) * 6, f.y, f.r, 0, 6.2832); ctx.fill(); }
  }
  function tick(now) {
    const dt = last ? Math.min(.05, (now - last) / 1000) : 0; last = now;
    for (const f of flakes) { f.y += f.v * dt; f.sway += dt * .9; if (f.y > h + 4) Object.assign(f, flake(false)); }
    draw(); raf = requestAnimationFrame(tick);
  }
  size(); draw();
  addEventListener('resize', () => { size(); draw(); });
  if (reduced) return;
  const running = () => !document.hidden && !motionPaused;
  const restart = () => { cancelAnimationFrame(raf); if (running()) { last = 0; raf = requestAnimationFrame(tick); } };
  restart();
  document.addEventListener('visibilitychange', restart);
  document.addEventListener('santa:motion', restart);
})();

/* One switch for everything that moves (pets, snow, lights). Keyboard and screen-reader friendly, hidden when the
   system asks for reduced motion because nothing animates then anyway. */
function setMotion(paused) {
  motionPaused = paused;
  document.documentElement.classList.toggle('motion-paused', paused);
  $('motionBtn').setAttribute('aria-pressed', String(paused));
  $('motionBtn').setAttribute('aria-label', paused ? t('playAll') : t('pauseAll'));
  document.dispatchEvent(new Event('santa:motion'));
}
$('motionBtn').addEventListener('click', () => setMotion(!motionPaused));
if (reduced) $('motionBtn').hidden = true;

// Pet reactions stay independent; rapid taps restart one short reaction.
for (const button of document.querySelectorAll('[data-pet]')) {
  const pet = document.querySelector('.pet-' + button.dataset.pet);
  const stage = button.closest('.stage');
  const curious = button.dataset.pet + '-curious';
  let reaction = 0, heartTimer = 0;
  const greet = () => { if (!motionPaused) stage.classList.add(curious); };
  const settle = () => stage.classList.remove(curious);
  button.addEventListener('pointerenter', (event) => { if (event.pointerType === 'mouse') greet(); });
  button.addEventListener('pointerleave', settle);
  button.addEventListener('focus', greet);
  button.addEventListener('blur', settle);
  button.addEventListener('click', () => {
    clearTimeout(heartTimer); cancelAnimationFrame(reaction);
    pet.classList.remove('is-petted'); button.classList.remove('loved');
    reaction = requestAnimationFrame(() => {
      reaction = requestAnimationFrame(() => {
        if (!motionPaused) pet.classList.add('is-petted');
        button.classList.add('loved');
        heartTimer = setTimeout(() => button.classList.remove('loved'), 1100);
      });
    });
  });
  pet.addEventListener('animationend', (event) => {
    if (event.animationName.endsWith('-petted')) pet.classList.remove('is-petted');
  });
}

/* ---------- opened at an old address? ----------
   The site has one official address (SANTA_HOME, written by deploy.sh). Opened anywhere else, the page points
   there. What a browser remembers (sign-ins, organizer access) is kept per address, so the link for an organizer
   carries the organizer key in the # part, exactly like the organizer link; participants sign in again with their PIN. */
function movedNote() {
  const home = String(window.SANTA_HOME || '').replace(/\/+$/, '');
  if (!home || home === location.origin) return;
  let target = home + '/';
  if (/^[a-z0-9]{12}$/.test(EVENT_ID)) {
    target += '?e=' + EVENT_ID;
    const key = store.get(K_ADMIN);
    if (typeof key === 'string' && key) target += '#admin=' + key;
  }
  const host = home.replace(/^https?:\/\//, '');
  $('movedText').textContent = t('movedText', host);
  $('movedLink').textContent = t('movedLink');
  $('movedLink').href = target;
  $('moved').hidden = false;
}

/* ---------- start ---------- */
applyLang();
if (/^[a-z0-9]{12}$/.test(EVENT_ID)) startEvent();
else if (EVENT_ID) { document.documentElement.classList.add('event'); $('homeLink').hidden = false; rerender = render; gone(); render(); }
else startHome();
movedNote();                                       // after the start: an organizer link's key is on this device by now
})();
