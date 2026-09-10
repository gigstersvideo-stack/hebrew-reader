// Test harness for reader-prototype.html's classic <script> block, modeled
// on the trainer's test_harness.js (same vm-sandbox approach), but the
// reader's DOM coupling is much heavier (real querySelector/addEventListener
// usage, not just getElementById lookups) — so this fake DOM is a genuine
// (if partial) selector engine + event system, not just a handful of
// special-cased strings like the trainer's fakeDocument needed.
//
// Deliberately zero npm dependencies (no jsdom) to match the trainer's
// harness and this project's no-build-step ethos — see ROADMAP.md.
//
// Scope (see ROADMAP.md "у читалки нет автотестов" for why these first):
// the FSRS core (verbatim-ported copy, checked against the trainer's own
// reference values so the two copies can't silently drift), the exact-
// position resume mechanism (real bug found and fixed live this session —
// see reader-prototype.html's goToSentence/renderPage ordering comment),
// the library completion badge/sort, and the word-popover highlight
// clearing (root-match/selected) investigated earlier this session.
//
// NOT covered here and not coverable this way: actual visual layout bugs
// (the translate-toggle line-wrap fix) — those need a real browser/layout
// engine, not a DOM stub. Flag those for manual/live-browser verification
// instead of pretending this harness catches them.

const vm = require('vm');
const fs = require('fs');
const path = require('path');

// ---- fake DOM ----------------------------------------------------------

let allElements = []; // flat registry every createElement/getElementById-created el joins; queried by class/tag/attr (not true tree scoping — see file header)

function fakeStyle() { return new Proxy({}, { get: () => '', set: () => true }); }

function fakeClassList(el) {
  const set = new Set();
  return {
    add: (...c) => c.forEach(x => set.add(x)),
    remove: (...c) => c.forEach(x => set.delete(x)),
    toggle: (c, force) => { if (force === undefined) { if (set.has(c)) set.delete(c); else set.add(c); return set.has(c); } if (force) set.add(c); else set.delete(c); return force; },
    contains: (c) => set.has(c),
    get _set() { return set; },
  };
}

// Parses ONE compound selector segment, e.g. `.tword.root-match` or
// `div[data-sentence="s1"][data-word="2"]` or `button` — no descendant
// combinators or pseudo-classes (none of the tests below need them; a
// selector using one just matches nothing, same as a real querySelectorAll
// finding zero elements, so callers' .forEach() stay safe no-ops).
function parseSimpleSelector(sel) {
  sel = sel.trim();
  const attrs = [];
  sel = sel.replace(/\[([\w-]+)(?:="([^"]*)")?\]/g, (_, name, val) => { attrs.push({ name, val }); return ''; });
  const classes = [];
  sel = sel.replace(/\.([\w-]+)/g, (_, c) => { classes.push(c); return ''; });
  const tag = sel.trim() || null;
  return { tag, classes, attrs };
}

function elMatches(el, parsed) {
  if (parsed.tag && el.tag !== parsed.tag) return false;
  for (const c of parsed.classes) if (!el.classList.contains(c)) return false;
  for (const a of parsed.attrs) {
    const actual = el.dataset[a.name.replace(/^data-/, '')] ?? el.getAttribute(a.name);
    if (a.val !== undefined) { if (actual !== a.val) return false; }
    else if (actual === undefined || actual === null) return false;
  }
  return true;
}

function queryAll(selector) {
  if (/[\s:]/.test(selector)) return []; // unsupported (descendant/pseudo) -> empty, matches real querySelectorAll's "no match" shape for callers that just .forEach()
  const parsed = parseSimpleSelector(selector);
  return allElements.filter(el => !el._removed && elMatches(el, parsed));
}

function fakeEl(tag) {
  const children = [];
  const listeners = {};
  let _innerHTML = '';
  const el = {
    tag: tag || 'div',
    value: '', textContent: '', title: '', src: '', href: '',
    style: fakeStyle(),
    // A real assignment parses/replaces children; nothing here needs true
    // parent-child nesting to be queryable afterward since queryAll()
    // searches the global `allElements` registry regardless of caller (see
    // file header) — good enough for this stub's actual test targets
    // (popover content built via template-string innerHTML).
    get innerHTML() { return _innerHTML; },
    set innerHTML(html) {
      _innerHTML = html;
      children.length = 0;
      const tagRe = /<(\w+)([^>]*)>/g;
      let m;
      while ((m = tagRe.exec(html))) {
        const [, ctag, attrStr] = m;
        if (ctag === 'br' || ctag === 'img') continue;
        const child = fakeEl(ctag);
        const classMatch = attrStr.match(/class=["']([^"']*)["']/);
        if (classMatch) classMatch[1].split(/\s+/).filter(Boolean).forEach(c => child.classList.add(c));
        const idMatch = attrStr.match(/\bid=["']([^"']*)["']/);
        if (idMatch) child.id = idMatch[1];
        const dataRe = /data-([\w-]+)=["']([^"']*)["']/g;
        let dm;
        while ((dm = dataRe.exec(attrStr))) child.dataset[dm[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = dm[2];
        el.appendChild(child);
      }
    },
    dataset: {},
    _attrs: {},
    children,
    parentElement: null,
    _removed: false,
    get classList() { return this._classList || (this._classList = fakeClassList(this)); },
    get className() { return Array.from(this.classList._set).join(' '); },
    set className(v) { this.classList._set.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c)); },
    addEventListener: (type, handler) => { (listeners[type] || (listeners[type] = [])).push(handler); },
    removeEventListener: () => {},
    dispatch(type, evt) { (listeners[type] || []).forEach(h => h(evt || { stopPropagation(){}, preventDefault(){}, target: this })); },
    click() { this.dispatch('click'); },
    appendChild: (child) => { children.push(child); child.parentElement = el; return child; },
    removeChild: (child) => { const i = children.indexOf(child); if (i >= 0) children.splice(i, 1); return child; },
    remove: () => { el._removed = true; if (el.parentElement) el.parentElement.removeChild(el); },
    get nextElementSibling() {
      if (!el.parentElement) return null;
      const sibs = el.parentElement.children;
      const i = sibs.indexOf(el);
      return i >= 0 && i + 1 < sibs.length ? sibs[i + 1] : null;
    },
    get nextSibling() { return el.nextElementSibling; },
    get firstChild() { return children[0] || null; },
    get childElementCount() { return children.length; },
    closest(selector) {
      let cur = el;
      const parsed = parseSimpleSelector(selector);
      while (cur) { if (elMatches(cur, parsed)) return cur; cur = cur.parentElement; }
      return null;
    },
    querySelector(sel) { const r = queryAll(sel); return r.length ? r[0] : null; },
    querySelectorAll(sel) { return queryAll(sel); },
    getAttribute(name) { return name in el._attrs ? el._attrs[name] : null; },
    setAttribute(name, val) { el._attrs[name] = String(val); },
    removeAttribute(name) { delete el._attrs[name]; },
    getBoundingClientRect: () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }),
    scrollIntoView: () => {},
    focus: () => {}, blur: () => {},
    disabled: false, checked: false, placeholder: '',
    // <audio> stub — harmless on other tags too, matches the trainer
    // harness's permissive style.
    play: () => Promise.resolve(),
    pause: () => {},
    load: () => {},
    currentTime: 0, duration: 0, playbackRate: 1, paused: true, ended: false,
  };
  allElements.push(el);
  return el;
}

const fakeDocument = {
  // Searches the real registry of created elements (most-recently-created
  // first) before falling back to auto-creating a placeholder — so an id
  // that's genuinely not rendered yet (e.g. a sentence span before its
  // page has been painted) correctly returns nothing-useful, same as real
  // getElementById would, instead of masking a real "called before the DOM
  // exists" ordering bug the way an always-succeeds stub would.
  getElementById: (id) => {
    for (let i = allElements.length - 1; i >= 0; i--) {
      if (allElements[i].id === id && !allElements[i]._removed) return allElements[i];
    }
    const el = fakeEl();
    el.id = id;
    return el;
  },
  createElement: (tag) => fakeEl(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  querySelector(sel) { const r = queryAll(sel); return r.length ? r[0] : null; },
  querySelectorAll(sel) { return queryAll(sel); },
  addEventListener: () => {},
  body: fakeEl('body'),
};

let store = {};
const fakeLocalStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

const sandbox = {
  document: fakeDocument,
  localStorage: fakeLocalStorage,
  window: {},
  console,
  Date, Math, Array, Object, JSON, Set, Number, String, Boolean, RegExp, Promise,
  URLSearchParams: URL ? require('url').URLSearchParams : undefined,
  CSS: { escape: (s) => String(s).replace(/[^a-zA-Z0-9_֐-׿-]/g, '\\$&') },
  fetch: () => Promise.resolve({ ok: false, json: async () => ({}) }),
  navigator: { onLine: true, clipboard: { writeText: async () => {} }, userAgent: 'test', maxTouchPoints: 0 },
  location: { href: 'http://test.local/', search: '', pathname: '/', hostname: 'test.local' },
  history: { replaceState: () => {} },
  alert: () => {},
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  Audio: function () { return fakeEl('audio'); },
  scrollX: 0, scrollY: 0,
};
fakeDocument.documentElement = { clientWidth: 1024, clientHeight: 768 };
sandbox.window.location = sandbox.location;
sandbox.window.scrollX = 0;
sandbox.window.scrollY = 0;
sandbox.window.innerWidth = 1024;
sandbox.window.innerHeight = 768;
sandbox.window.addEventListener = () => {};
sandbox.window.speechSynthesis = undefined;
vm.createContext(sandbox);

// Self-extracting — reads reader-prototype.html directly (no separate
// pre-extraction step needed, unlike the trainer's harness which works off
// a fully-built html with real book data spliced in) so `node
// test_harness.js` from the repo root just works.
const html = fs.readFileSync(path.join(__dirname, 'reader-prototype.html'), 'utf-8');
const scriptMatch = /<script>([\s\S]*?)<\/script>/.exec(html);
if (!scriptMatch) throw new Error('could not find the classic <script> block in reader-prototype.html');
const script = scriptMatch[1];
vm.runInContext(script, sandbox, { filename: 'reader_script.js' });

// ---- assertions ----------------------------------------------------------

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log('OK   ', name); }
  else { fail++; console.log('FAIL ', name); }
}

function makeBook(id, n) {
  const sentences = [];
  for (let i = 1; i <= n; i++) {
    sentences.push({
      id: 's' + i,
      literal: 'literal ' + i, fluent: 'fluent ' + i,
      words: [
        { t: 'מִלָּה' + i, lemma: 'מִלָּה' + i, pos: 'сущ.', tr: 'word' + i, extra: '', root: i % 2 === 0 ? 'א-ב-ג' : '—' },
        { t: 'שְׁתַּיִם', lemma: 'שְׁתַּיִם', pos: 'сущ.', tr: 'two', extra: '', root: 'ש-ת-ה' },
      ],
      audio: null,
    });
  }
  return { sentences };
}

// ---- 1. FSRS core cross-checked against the trainer's own reference
// values (fsrs_reference.py / test_harness.js block 10) — this is a
// VERBATIM port per project memory (project_hebrew_trainer_login.md), so
// any divergence here means the two copies have silently drifted apart.
{
  const { fsrsNextState, fsrsNextIntervalDays, schedule } = sandbox;
  const close = (a, b) => Math.abs(a - b) < 1e-5;

  let r = fsrsNextState(0, 0, 0, 3);
  check('fsrs new-card Good matches reference D', close(r.difficulty, 2.118104));
  check('fsrs new-card Good matches reference S', close(r.stability, 2.306500));

  r = fsrsNextState(r.difficulty, r.stability, 1.0, 3);
  check('fsrs +1day Good matches reference D', close(r.difficulty, 2.111214));
  check('fsrs +1day Good matches reference S', close(r.stability, 7.315301));

  const lapse = fsrsNextState(5.0, 10.0, 10.0, 1);
  check('fsrs established +10d Again(lapse) matches reference D', close(lapse.difficulty, 8.341762));
  check('fsrs established +10d Again(lapse) matches reference S', close(lapse.stability, 1.391987));

  let st = null;
  st = schedule(st, 5);
  check('fsrs new card starts in learning, step 1 of 2', st.phase === 'learning' && st.stepIndex === 1);
  st = schedule(st, 5);
  check('fsrs graduates to review after 2 learning steps', st.phase === 'review' && st.reps === 1);
  st = schedule(st, 1);
  check('fsrs lapse moves to relearning', st.phase === 'relearning' && st.lapses === 1);
}

// ---- 2. getBookProgress: pure localStorage read, no DOM/initReader needed
{
  store = {};
  let p = sandbox.getBookProgress('never-opened');
  check('getBookProgress: untouched book is neither opened nor completed', p.opened === false && p.completed === false);

  store['hebrew-reader-page:started-book'] = '0';
  p = sandbox.getBookProgress('started-book');
  check('getBookProgress: a saved page (even 0) counts as opened', p.opened === true && p.completed === false);

  store['hebrew-reader-completed:done-book'] = '1';
  p = sandbox.getBookProgress('done-book');
  check('getBookProgress: completed flag detected', p.completed === true);
}

// ---- 3. renderLibrary sort: started-not-finished first, untouched in the
// middle, completed last — the exact tiering decided with the user before
// building it.
{
  store = {};
  allElements = [];
  store['hebrew-reader-page:mid-book'] = '2';       // started, not finished
  store['hebrew-reader-completed:done-book'] = '1'; // completed
  // untouched-book: no localStorage entries at all

  const manifest = [
    { id: 'done-book', title: 'Done' },
    { id: 'untouched-book', title: 'Untouched' },
    { id: 'mid-book', title: 'Mid' },
  ];
  sandbox.renderLibrary(manifest);
  const grid = fakeDocument.getElementById('bookGrid');
  const cardTitles = grid.children
    .filter(c => c.classList.contains('book-card') && !c.classList.contains('add-new'))
    .map(c => c.innerHTML);
  const idxOf = (needle) => cardTitles.findIndex(html => html.includes(needle));
  check('library sort: started-not-finished book sorts first', idxOf('Mid') === 0);
  check('library sort: completed book sorts last (of the 3 real cards)', idxOf('Done') === 2);
  check('library sort: completed card carries the "Прочитано" badge', cardTitles[idxOf('Done')].includes('Прочитано'));
  check('library sort: started card carries the "Продолжить" note', cardTitles[idxOf('Mid')].includes('Продолжить'));
  check('library sort: untouched card has neither badge', !cardTitles[idxOf('Untouched')].includes('Прочитано') && !cardTitles[idxOf('Untouched')].includes('Продолжить'));
}

// ---- 4. Exact-position resume — the real bug found and fixed live this
// session: the pre-existing "initial paint of the transport bar"
// goToSentence() call ran BEFORE the first renderPage(), so
// markSentencePlaying's getElementById always missed. Regression guard.
{
  store = {};
  allElements = [];
  const book = makeBook('resume-book', 5);
  store['hebrew-reader-sentence:resume-book'] = '3'; // simulate "was reading s4 (0-based idx 3) last time"

  sandbox.initReader(book, 'resume-book', { title: 'Resume test' });

  const playingEl = fakeDocument.getElementById('s4');
  check('resume: the saved sentence element exists after initReader', !!playingEl);
  check('resume: the saved sentence is marked .playing on reopen', playingEl.classList.contains('playing'));
  const anyOtherPlaying = queryAll('.sentence.playing').length;
  check('resume: exactly one sentence is marked playing', anyOtherPlaying === 1);
}

// ---- 5. Word-popover highlight clearing (root-match) — investigated
// earlier this session after a report of "highlight doesn't clear on next
// selection"; couldn't reproduce live, this locks the working behavior in.
{
  store = {};
  allElements = [];
  const book = makeBook('popover-book', 6); // words with root 'א-ב-ג' on even sentence indices (0-based) per makeBook
  sandbox.initReader(book, 'popover-book', { title: 'Popover test' });

  const words = queryAll('.tword');
  check('popover test: sentences actually rendered some words', words.length > 0);

  // Two genuinely non-overlapping root groups (see makeBook: 'א-ב-ג' only on
  // even sentences' first word, 'ש-ת-ה' on every sentence's second word) —
  // selecting one then the other must leave zero trace of the first, unlike
  // selecting two words that happen to share the SAME root (where the old
  // word legitimately stays highlighted as a root-mate of the new one).
  const rootWord = words.find(w => w.dataset.root === 'א-ב-ג');
  const otherRootWord = words.find(w => w.dataset.root === 'ש-ת-ה');
  check('popover test: found words from two distinct root groups', !!rootWord && !!otherRootWord);
  if (rootWord && otherRootWord) {
    rootWord.dispatch('click', { stopPropagation() {} });
    const matchesAfterFirst = queryAll('.tword.root-match').length;
    check('popover: selecting a word highlights its root-mates', matchesAfterFirst > 0);

    otherRootWord.dispatch('click', { stopPropagation() {} });
    const staleMatch = queryAll('.tword.root-match').some(el => el.dataset.root === 'א-ב-ג');
    const staleSelected = rootWord.classList.contains('selected');
    check('popover: previous (unrelated-root) selection leaves no root-match trace', staleMatch === false);
    check('popover: previous selection is no longer marked selected', staleSelected === false);
    check('popover: new selection highlights the new root-mates instead', queryAll('.tword.root-match').every(el => el.dataset.root === 'ש-ת-ה'));
  }
}

// ---- 6. Completion flag set on reaching the last page
{
  store = {};
  allElements = [];
  const book = makeBook('short-book', 3); // 1 page (well under PAGE_SIZE=20)
  sandbox.initReader(book, 'short-book', { title: 'Short book' });
  check('completion: a book whose only page IS the last page is marked completed on open', store['hebrew-reader-completed:short-book'] === '1');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
