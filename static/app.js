// PDF Redactor — לוגיקת צד לקוח. אפס קריאות לרשת חיצונית.
'use strict';

const state = {
  session: null,
  filename: '',
  sources: [],        // [{name,start,pages}] — המסמכים שמוזגו, לפי הסדר
  pages: [],          // [{index,width,height,has_text}]
  candidates: [],     // [{kind,label,masked,page,rects,accepted,id}]
  manual: [],         // [{id,page,rects}]
  searchAreas: [],    // [{id,page,rects,query}]
  queries: [],        // מונחי החיפוש הפעילים — מורצים מחדש על עמודים שנוספו במיזוג
  skipped: [],        // [{name, pages:[...]}] — עמודים כפולים שדולגו
};

const $ = (id) => document.getElementById(id);
let nextId = 1;

/* ---------- אימות ---------- */

let setupMode = false;

async function initAuth() {
  let st;
  try {
    st = await (await fetch('/auth/status')).json();
  } catch {
    $('auth-screen').classList.remove('hidden');
    showAuthError('השרת אינו זמין.');
    return;
  }
  if (st.authenticated) {
    enterApp();
    return;
  }
  setupMode = !st.password_set;
  $('auth-screen').classList.remove('hidden');
  $('dropzone').classList.add('hidden');
  if (setupMode) {
    $('auth-title').textContent = 'קביעת סיסמה ראשונית';
    $('auth-hint').textContent = 'בחר סיסמה (8 תווים לפחות). היא אינה נשמרת בשום מקום — רק טביעת אצבע קריפטוגרפית שלה.';
    $('auth-password2').classList.remove('hidden');
    $('auth-submit').textContent = 'קבע סיסמה והיכנס';
  } else {
    $('auth-title').textContent = 'התחברות';
    $('auth-hint').textContent = 'הזן את סיסמת המערכת.';
    $('auth-password2').classList.add('hidden');
    $('auth-submit').textContent = 'כניסה';
    if (st.lockout_seconds > 0) {
      showAuthError(`יותר מדי ניסיונות. נסה שוב בעוד ${st.lockout_seconds} שניות.`);
    }
  }
  $('auth-password').focus();
}

function showAuthError(msg) {
  const el = $('auth-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

function enterApp() {
  $('auth-screen').classList.add('hidden');
  $('logout-btn').classList.remove('hidden');
  if (state.session) return; // כבר באמצע עבודה
  $('dropzone').classList.remove('hidden');
}

$('auth-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('auth-error').classList.add('hidden');
  const pw = $('auth-password').value;
  if (setupMode && pw !== $('auth-password2').value) {
    showAuthError('הסיסמאות אינן זהות.');
    return;
  }
  const res = await fetch(setupMode ? '/auth/setup' : '/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw }),
  });
  $('auth-password').value = '';
  $('auth-password2').value = '';
  if (res.ok) {
    enterApp();
  } else {
    const data = await res.json().catch(() => ({}));
    showAuthError(data.detail || 'שגיאה בהתחברות');
    $('auth-password').focus();
  }
});

$('logout-btn').addEventListener('click', async () => {
  await fetch('/auth/logout', { method: 'POST' }).catch(() => {});
  location.reload();
});

// כל תגובת 401 מכל בקשה מחזירה למסך ההתחברות (הטוקן פג)
const _origFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _origFetch(...args);
  if (res.status === 401 && !String(args[0]).startsWith('/auth/')) {
    location.reload();
  }
  return res;
};

initAuth();

/* ---------- העלאה ---------- */

const dropzone = $('dropzone');
const fileInput = $('file-input');

$('pick-btn').addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  const files = [...fileInput.files];
  // איפוס מיידי: בלי זה בחירה חוזרת של אותו קובץ (אחרי שגיאה, למשל)
  // לא מפעילה change בכלל, והלחיצה נראית כאילו לא קרה כלום
  fileInput.value = '';
  if (files.length) handleFiles(files);
});

// הוספת מסמכים נוספים לסשן קיים — שני כפתורים, אותו input
const addMoreInput = $('add-more-input');
$('add-more-btn').addEventListener('click', () => addMoreInput.click());
$('add-page-btn').addEventListener('click', () => addMoreInput.click());
addMoreInput.addEventListener('change', () => {
  const files = [...addMoreInput.files];
  addMoreInput.value = '';
  if (files.length) handleFiles(files);
});

['dragenter', 'dragover'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('dragover'); })
);
['dragleave', 'drop'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove('dragover'); })
);
dropzone.addEventListener('drop', (e) => {
  const files = [...e.dataTransfer.files];
  if (files.length) handleFiles(files);
});

// מונע העלאה מקבילה: לחיצה כפולה על "הוסף" בזמן שהעלאה רצה הייתה
// שולחת את אותו קובץ פעמיים, כל אחת עם X-Session שעדיין לא כלל את השנייה
let uploading = false;

async function handleFiles(files) {
  if (uploading) return;
  const pdfs = files.filter((f) => f.name.toLowerCase().endsWith('.pdf'));
  if (!pdfs.length) {
    alert('יש לבחור קובצי PDF.');
    return;
  }
  if (pdfs.length < files.length) {
    alert(`${files.length - pdfs.length} קבצים שאינם PDF דולגו.`);
  }

  uploading = true;
  const appending = !!state.session;
  const skipped = [];
  dropzone.classList.add('hidden');
  if (appending) $('workspace').classList.add('hidden');
  $('loading').classList.remove('hidden');

  // העלאה סדרתית: כל קובץ ממוזג לסוף המסמך של הסשן, לפי סדר הבחירה
  let data = null;
  try {
    for (let i = 0; i < pdfs.length; i++) {
      $('loading-text').textContent = pdfs.length > 1
        ? `מעבד ${i + 1} מתוך ${pdfs.length}: ${pdfs[i].name}`
        : 'מעבד את הקובץ ומזהה מידע רגיש…';
      const headers = {
        'Content-Type': 'application/octet-stream',
        'X-Filename': encodeURIComponent(pdfs[i].name),
      };
      if (state.session) headers['X-Session'] = state.session;
      try {
        const res = await fetch('/upload', { method: 'POST', headers, body: pdfs[i] });
        if (!res.ok) throw new Error((await res.json()).detail || 'שגיאה');
        data = await res.json();
        state.session = data.session;   // נקבע מהקובץ הראשון, ומשמש לשאר
        if (data.skipped_pages && data.skipped_pages.length) {
          skipped.push({ name: pdfs[i].name, pages: data.skipped_pages,
                         all: data.added === 0 });
        }
      } catch (err) {
        $('loading').classList.add('hidden');
        alert(`שגיאה בטעינת "${pdfs[i].name}": ${err.message}`);
        if (state.session) {
          $('workspace').classList.remove('hidden');   // נשארים עם מה שנטען עד כה
        } else {
          dropzone.classList.remove('hidden');
        }
        if (data) { state.skipped = skipped; applyUploadResult(data); }
        return;
      }
    }

    state.skipped = skipped;
    applyUploadResult(data);
    // מונחי חיפוש שכבר הופעלו חייבים לחול גם על העמודים שנוספו כרגע,
    // אחרת "הסר את כל המופעים של X" מפספס בדיוק את המסמך החדש
    if (appending && state.queries.length) {
      $('loading-text').textContent = 'מחיל את מונחי החיפוש על העמודים החדשים…';
      for (const q of [...state.queries]) await runSearch(q, true);
    }
    $('loading').classList.add('hidden');
    $('workspace').classList.remove('hidden');
  } finally {
    uploading = false;
  }
}

function applyUploadResult(data) {
  state.filename = data.filename;
  state.sources = data.sources;
  state.pages = data.pages;
  // שימור החלטות המשתמש על מועמדים שכבר הוצגו (לפי עמוד + סוג + מיקום)
  const prev = new Map(state.candidates.map((c) => [candKey(c), c.accepted]));
  state.candidates = data.candidates.map((c) => ({
    ...c, id: nextId++,
    accepted: prev.has(candKey(c)) ? prev.get(candKey(c)) : true,
  }));

  const multi = state.sources.length > 1;
  const totalPages = state.pages.length;
  $('file-name').textContent = multi
    ? `מסמך ממוזג — ${state.sources.length} קבצים`
    : data.filename;
  $('page-count').textContent = `${totalPages} עמודים`;

  const anyText = state.pages.some((p) => p.has_text);
  $('scan-banner').classList.toggle('hidden', anyText);

  renderSources();
  renderMergeNotice();
  renderPages();
  renderCandidates();
  renderManualList();
  updateCount();
}

function candKey(c) {
  return `${c.page}|${c.kind}|${c.rects[0].map((n) => Math.round(n)).join(',')}`;
}

/* ---------- רשימת המסמכים במיזוג ---------- */

function renderSources() {
  const el = $('sources-list');
  el.innerHTML = '';
  state.sources.forEach((s, i) => {
    const row = document.createElement('div');
    row.className = 'cand source-row';
    const range = s.pages === 1
      ? `עמוד ${s.start + 1}`
      : `עמודים ${s.start + 1}–${s.start + s.pages}`;
    row.innerHTML = `<span class="src-num">${i + 1}</span>
      <div class="cand-text">
        <div class="cand-kind">${escapeHtml(s.name)}</div>
        <div class="cand-meta">${range}</div>
      </div>`;
    row.title = 'לחץ למעבר לעמוד הראשון של הקובץ';
    row.addEventListener('click', () => scrollToPage(s.start));
    el.appendChild(row);
  });
}

/** הודעה על עמודים שלא נוספו במיזוג מפני שכבר היו במסמך. */
function renderMergeNotice() {
  const el = $('merge-notice');
  if (!state.skipped.length) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  el.innerHTML = state.skipped.map((sk) => {
    const name = escapeHtml(sk.name);
    if (sk.all) return `⚠️ "${name}" כבר קיים במסמך — לא נוסף שוב.`;
    const pg = sk.pages.length === 1
      ? `עמוד ${sk.pages[0]}`
      : `עמודים ${sk.pages.join(', ')}`;
    return `⚠️ מתוך "${name}": ${pg} כבר היו במסמך ולכן דולגו.`;
  }).join('<br>');
  el.classList.remove('hidden');
}

/** שם הקובץ שממנו הגיע העמוד. */
function sourceOfPage(pageIndex) {
  for (let i = state.sources.length - 1; i >= 0; i--) {
    if (pageIndex >= state.sources[i].start) return state.sources[i].name;
  }
  return '';
}

/* ---------- רינדור עמודים ---------- */

function renderPages() {
  const container = $('pages');
  container.innerHTML = '';
  state.pages.forEach((p) => {
    const num = document.createElement('div');
    num.className = 'page-num';
    num.textContent = state.sources.length > 1
      ? `עמוד ${p.index + 1} · ${sourceOfPage(p.index)}`
      : `עמוד ${p.index + 1}`;
    container.appendChild(num);

    const wrap = document.createElement('div');
    wrap.className = 'page-wrap';
    wrap.dataset.page = p.index;

    const img = document.createElement('img');
    img.src = `/page/${state.session}/${p.index}`;
    img.draggable = false;
    wrap.appendChild(img);

    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    wrap.appendChild(overlay);

    container.appendChild(wrap);
    attachDrawing(overlay, wrap, p);
    img.addEventListener('load', drawMarks);
  });
}

// המרה בין קואורדינטות מסך לקואורדינטות PDF (נקודות)
function pageScale(wrap, page) {
  const img = wrap.querySelector('img');
  return img.clientWidth / page.width;
}

function drawMarks() {
  document.querySelectorAll('.page-wrap').forEach((wrap) => {
    const pageIndex = +wrap.dataset.page;
    const page = state.pages[pageIndex];
    const overlay = wrap.querySelector('.overlay');
    overlay.querySelectorAll('.mark').forEach((m) => m.remove());
    const s = pageScale(wrap, page);

    const put = (rect, cls, onClick) => {
      const el = document.createElement('div');
      el.className = 'mark ' + cls;
      el.style.left = (rect[0] * s) + 'px';
      el.style.top = (rect[1] * s) + 'px';
      el.style.width = Math.max(2, (rect[2] - rect[0]) * s) + 'px';
      el.style.height = Math.max(2, (rect[3] - rect[1]) * s) + 'px';
      if (onClick) el.addEventListener('click', onClick);
      overlay.appendChild(el);
    };

    // התצוגה המקדימה משקפת את הסגנון שייבחר בפועל בקובץ הסופי
    const activeCls = $('black-boxes').checked ? 'active black' : 'active';

    state.candidates
      .filter((c) => c.page === pageIndex)
      .forEach((c) => c.rects.forEach((r) => put(r, c.accepted ? activeCls : '')));

    state.searchAreas
      .filter((a) => a.page === pageIndex)
      .forEach((a) => a.rects.forEach((r) => put(r, activeCls)));

    state.manual
      .filter((m) => m.page === pageIndex)
      .forEach((m) => m.rects.forEach((r) => put(r, 'manual', () => removeManual(m.id))));
  });
}

window.addEventListener('resize', drawMarks);
$('black-boxes').addEventListener('change', drawMarks);

/* ---------- ציור ידני ---------- */

function attachDrawing(overlay, wrap, page) {
  let start = null, box = null;

  overlay.addEventListener('mousedown', (e) => {
    if (e.target !== overlay) return;   // לחיצה על סימון קיים = מחיקה, לא ציור
    e.preventDefault();
    const r = overlay.getBoundingClientRect();
    start = { x: e.clientX - r.left, y: e.clientY - r.top };
    box = document.createElement('div');
    box.className = 'mark drawing';
    overlay.appendChild(box);
  });

  overlay.addEventListener('mousemove', (e) => {
    if (!start) return;
    const r = overlay.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    box.style.left = Math.min(start.x, x) + 'px';
    box.style.top = Math.min(start.y, y) + 'px';
    box.style.width = Math.abs(x - start.x) + 'px';
    box.style.height = Math.abs(y - start.y) + 'px';
  });

  const finish = (e) => {
    if (!start) return;
    const r = overlay.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const s = pageScale(wrap, page);
    const rect = [
      Math.min(start.x, x) / s, Math.min(start.y, y) / s,
      Math.max(start.x, x) / s, Math.max(start.y, y) / s,
    ];
    box.remove();
    start = null; box = null;
    // התעלמות מגרירות זעירות (קליק בטעות)
    if ((rect[2] - rect[0]) * s < 5 || (rect[3] - rect[1]) * s < 5) return;
    state.manual.push({ id: nextId++, page: page.index, rects: [rect] });
    drawMarks();
    renderManualList();
    updateCount();
  };

  overlay.addEventListener('mouseup', finish);
  overlay.addEventListener('mouseleave', (e) => { if (start) finish(e); });
}

function removeManual(id) {
  state.manual = state.manual.filter((m) => m.id !== id);
  drawMarks();
  renderManualList();
  updateCount();
}

function renderManualList() {
  const el = $('manual-list');
  const all = [...state.manual.map((m) => ({ ...m, type: 'manual' })),
               ...state.searchAreas.map((a) => ({ ...a, type: 'search' }))];
  if (!all.length) {
    el.textContent = 'אין סימונים ידניים.';
    return;
  }
  el.innerHTML = '';
  all.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'cand';
    const label = item.type === 'search'
      ? `חיפוש: "${item.query}" — עמוד ${item.page + 1}`
      : `אזור מסומן — עמוד ${item.page + 1}`;
    row.innerHTML = `<span class="cand-text">${escapeHtml(label)}</span>`;
    const btn = document.createElement('button');
    btn.className = 'btn tiny';
    btn.textContent = 'הסר';
    btn.addEventListener('click', () => {
      if (item.type === 'search') {
        state.searchAreas = state.searchAreas.filter((a) => a.id !== item.id);
        // המונח יורד מהרשימה הפעילה רק כשלא נשאר ממנו אף מופע, כדי
        // שמיזוג עתידי לא יחזיר לסימון מונח שהמשתמש ביטל
        if (!state.searchAreas.some((a) => a.query === item.query)) {
          state.queries = state.queries.filter((q) => q !== item.query);
        }
      } else {
        state.manual = state.manual.filter((m) => m.id !== item.id);
      }
      drawMarks(); renderManualList(); updateCount();
    });
    row.appendChild(btn);
    el.appendChild(row);
  });
}

/* ---------- מועמדים אוטומטיים ---------- */

function renderCandidates() {
  const el = $('candidates');
  el.innerHTML = '';
  if (!state.candidates.length) {
    el.innerHTML = '<div class="muted small">לא זוהו פרטים רגישים אוטומטית.</div>';
    return;
  }
  state.candidates.forEach((c) => {
    const row = document.createElement('label');
    row.className = 'cand';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = c.accepted;
    cb.addEventListener('change', () => { c.accepted = cb.checked; drawMarks(); updateCount(); });
    const txt = document.createElement('div');
    txt.className = 'cand-text';
    const src = state.sources.length > 1 ? ` · ${escapeHtml(sourceOfPage(c.page))}` : '';
    txt.innerHTML = `<div class="cand-kind">${escapeHtml(c.label)}</div>
                     <div class="cand-meta">${escapeHtml(c.masked)} · עמוד ${c.page + 1}${src}</div>`;
    row.appendChild(cb);
    row.appendChild(txt);
    row.addEventListener('dblclick', () => scrollToPage(c.page));
    el.appendChild(row);
  });
}

$('accept-all').addEventListener('click', () => {
  const allOn = state.candidates.every((c) => c.accepted);
  state.candidates.forEach((c) => { c.accepted = !allOn; });
  renderCandidates(); drawMarks(); updateCount();
});

function scrollToPage(i) {
  const wrap = document.querySelector(`.page-wrap[data-page="${i}"]`);
  if (wrap) wrap.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ---------- חיפוש טקסט ---------- */

$('search-btn').addEventListener('click', doSearch);
$('search-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

function searchKey(page, rects, query) {
  const r = rects.map((v) => v.map((n) => Math.round(n)).join(',')).join(';');
  return `${page}|${query}|${r}`;
}

/** מריץ מונח חיפוש ומוסיף רק מופעים שאינם מסומנים כבר.
 *  silent: הרצה חוזרת אחרי מיזוג — בלי לדרוס את הודעת החיפוש האחרונה. */
async function runSearch(q, silent = false) {
  const out = $('search-result');
  if (!silent) out.textContent = 'מחפש…';
  let data;
  try {
    const res = await fetch('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: state.session, query: q }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'שגיאה');
    data = await res.json();
  } catch (err) {
    if (!silent) out.textContent = 'שגיאה בחיפוש: ' + err.message;
    return { found: 0, added: 0 };
  }

  // הרצה חוזרת של אותו מונח (ידנית או אחרי מיזוג) לא מסמנת שוב את
  // אותו מופע — אחרת אותו אזור נספר פעמיים ומופיע כפול ברשימה
  const seen = new Set(state.searchAreas.map((a) => searchKey(a.page, a.rects, a.query)));
  let added = 0;
  data.results.forEach((r) => {
    const key = searchKey(r.page, r.rects, q);
    if (seen.has(key)) return;
    seen.add(key);
    state.searchAreas.push({ id: nextId++, page: r.page, rects: r.rects, query: q });
    added++;
  });

  if (data.results.length && !state.queries.includes(q)) state.queries.push(q);
  if (!silent) {
    if (!data.results.length) out.textContent = 'לא נמצאו מופעים.';
    else if (!added) out.textContent = `כל ${data.results.length} המופעים של המונח הזה כבר מסומנים.`;
    else out.textContent = `נוספו ${added} מופעים להסרה.`;
  }
  if (added) { drawMarks(); renderManualList(); updateCount(); }
  return { found: data.results.length, added };
}

async function doSearch() {
  const q = $('search-input').value.trim();
  if (!q) return;
  const { found } = await runSearch(q);
  if (found) $('search-input').value = '';
}

/* ---------- ביצוע ההסרה ---------- */

function collectAreas() {
  const areas = [];
  state.candidates.filter((c) => c.accepted).forEach((c) => areas.push({ page: c.page, rects: c.rects }));
  state.searchAreas.forEach((a) => areas.push({ page: a.page, rects: a.rects }));
  state.manual.forEach((m) => areas.push({ page: m.page, rects: m.rects }));
  return areas;
}

function updateCount() {
  const n = collectAreas().length;
  const btn = $('redact-btn');
  // מותר להפיק קובץ גם בלי סימונים: כך אפשר רק למזג מסמכים,
  // או לנקות מטאדאטה ממסמך יחיד.
  btn.disabled = !state.session;
  if (n > 0) {
    $('selected-count').textContent = `${n} אזורים מסומנים`;
    btn.textContent = 'בצע הסרה';
  } else {
    $('selected-count').textContent = state.sources.length > 1
      ? 'לא סומן דבר — הקובץ ימוזג בלבד'
      : 'לא סומן דבר — תנוקה רק המטאדאטה';
    btn.textContent = state.sources.length > 1 ? 'צור מסמך ממוזג' : 'שמור מסמך נקי';
  }
}

$('redact-btn').addEventListener('click', async () => {
  const areas = collectAreas();
  $('workspace').classList.add('hidden');
  $('loading').classList.remove('hidden');
  $('loading-text').textContent = areas.length
    ? 'מבצע הסרה ומאמת…'
    : (state.sources.length > 1 ? 'ממזג את המסמכים…' : 'מכין את הקובץ…');

  // ערכים לאימות: המועמדים שאושרו (הערך המקורי אינו נשלח לדפדפן — נשלחות מחרוזות החיפוש)
  const forbidden = state.searchAreas.map((a) => a.query);

  let data;
  try {
    const res = await fetch('/redact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session: state.session,
        areas,
        forbidden_values: [...new Set(forbidden)],
        rasterize: $('rasterize').checked,
        black_boxes: $('black-boxes').checked,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'שגיאה');
    data = await res.json();
  } catch (err) {
    alert('שגיאה בביצוע ההסרה: ' + err.message);
    $('loading').classList.add('hidden');
    $('workspace').classList.remove('hidden');
    return;
  }

  $('loading').classList.add('hidden');
  $('workspace').classList.remove('hidden');
  showReport(data, areas.length);
});

function showReport(data, areaCount) {
  const r = data.report;
  const verb = areaCount > 0 ? 'ההסרה הושלמה' : 'הקובץ נוצר';
  $('modal-title').textContent = r.clean ? `✅ ${verb}` : `⚠️ ${verb} עם אזהרה`;
  const lines = [];
  if (areaCount > 0) {
    const style = $('black-boxes').checked ? 'מסומנים במלבן שחור' : 'נשארו ריקים';
    lines.push(line(true, `${areaCount} אזורים הוסרו פיזית מתוכן הקובץ (לא ניתן לשחזור), ו${style}.`));

    const dirty = r.dirty_regions.length;
    lines.push(line(dirty === 0, dirty === 0
      ? `אימות: כל ${r.regions_checked} האזורים נבדקו בקובץ הסופי ונמצאו ריקים מטקסט.`
      : `אזהרה: ב-${dirty} אזורים נותר טקסט: ` +
        r.dirty_regions.map((d) => `עמוד ${d.page + 1} ("${d.text}")`).join(', ')));
  } else if (state.sources.length > 1) {
    lines.push(line(true,
      `${state.sources.length} מסמכים מוזגו ל-${state.pages.length} עמודים. לא הוסר מידע — לא סומן דבר.`));
  } else {
    lines.push(line(true, 'לא הוסר מידע — לא סומן דבר.'));
  }

  if (r.term_leaks.length) {
    lines.push(line(false, 'מונחי החיפוש הבאים עדיין מופיעים במסמך: ' + r.term_leaks.join(', ')));
  }

  lines.push(line(r.metadata_empty, r.metadata_empty
    ? 'המטאדאטה נוקתה במלואה.'
    : 'נותרה מטאדאטה: ' + JSON.stringify(r.remaining_metadata)));
  lines.push(line(true, `גודל הקובץ: ${(data.size / 1024).toFixed(0)} KB`));
  $('modal-body').innerHTML = lines.join('');
  $('modal').classList.remove('hidden');
}

function line(ok, text) {
  return `<div class="report-line"><span class="${ok ? 'report-ok' : 'report-bad'}">${ok ? '✔' : '✖'}</span><span>${escapeHtml(text)}</span></div>`;
}

$('download-btn').addEventListener('click', () => {
  window.location.href = `/download/${state.session}`;
});
$('modal-close').addEventListener('click', () => $('modal').classList.add('hidden'));

$('reset-btn').addEventListener('click', async () => {
  if (state.session) {
    await fetch(`/cleanup/${state.session}`, { method: 'POST' }).catch(() => {});
  }
  state.session = null;
  state.sources = [];
  state.pages = [];
  state.candidates = [];
  state.manual = [];
  state.searchAreas = [];
  state.queries = [];
  state.skipped = [];
  $('search-result').textContent = '';
  $('workspace').classList.add('hidden');
  $('modal').classList.add('hidden');
  dropzone.classList.remove('hidden');
  fileInput.value = '';
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
