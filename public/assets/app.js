const DAYS_OF_WEEK = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const FALLBACK_THEME = {
  id: 'default',
  label: 'Room',
  bannerBg: '#334155',
  bannerText: '#ffffff',
  badgeBg: 'rgba(148, 163, 184, 0.2)',
  badgeText: '#e2e8f0'
};

let ageGroups = [];
let ageGroupsMap = {};
let roomsList = [];
let roomsMap = {};
let scheduleList = [];
const STATE_CACHE_KEY = 'lbc_signs_cached_state_v1';
let appSettings = { theme: 'dark', timezone: 'America/Chicago', showLogo: true };
let lastStateEtag = null;
let knownBuildId = null;
let serverClockOffsetMs = 0;
let isOffline = false;
let lastSyncedAt = null;
let requestedUrlRoom = new URLSearchParams(window.location.search).get('room');

let useLiveClock = false;
let isAdminUnlocked = false;
let currentUser = null;
let csrfToken = '';
let pendingAdminTab = 'admin';
let currentTab = 'display';

let toastTimer = null;
let activeUndoAction = null;
const debounceTimers = new Map();

// Pure DOM helper — never interpolates dynamic data into innerHTML
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) {
    if (val === undefined || val === null || val === false) continue;
    if (key === 'className') node.className = val;
    else if (key === 'text') node.textContent = String(val);
    else if (key === 'value') node.value = String(val);
    else if (key === 'selected') node.selected = Boolean(val);
    else if (key === 'style' && typeof val === 'object') Object.assign(node.style, val);
    else if (key === 'dataset' && typeof val === 'object') {
      for (const [dk, dv] of Object.entries(val)) node.dataset[dk] = String(dv);
    } else {
      node.setAttribute(key, String(val));
    }
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

function getTheme(ageGroupId) {
  if (ageGroupId && ageGroupsMap[ageGroupId]) return ageGroupsMap[ageGroupId];
  const def = ageGroups.find(ag => ag.isDefault);
  return def || ageGroups[0] || FALLBACK_THEME;
}

function resolveAgeGroupIdByName(rawName) {
  const needle = String(rawName || '').trim().toLowerCase();
  if (!needle) return (ageGroups.find(ag => ag.isDefault) || ageGroups[0] || FALLBACK_THEME).id;
  const direct = ageGroups.find(ag => ag.id.toLowerCase() === needle || ag.label.toLowerCase() === needle);
  if (direct) return direct.id;
  const aliased = ageGroups.find(ag => (ag.aliases || []).some(a => String(a).trim().toLowerCase() === needle));
  if (aliased) return aliased.id;
  return (ageGroups.find(ag => ag.isDefault) || ageGroups[0] || FALLBACK_THEME).id;
}

async function apiFetch(url, options = {}, _retriedCsrf = false) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    if (!csrfToken) await checkAuthStatus();
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  const res = await fetch(url, { ...options, method, headers });
  if (res.status === 403 && !_retriedCsrf) {
    const clone = res.clone();
    const body = await clone.json().catch(() => ({}));
    if (body.code === 'CSRF_ERROR') {
      await checkAuthStatus();
      return apiFetch(url, options, true);
    }
  }
  return res;
}

function setSaveStatus(state, text) {
  const badge = document.getElementById('adminSaveStatus');
  if (!badge) return;
  if (!state) {
    badge.classList.add('hidden');
    return;
  }
  badge.textContent = text;
  badge.classList.remove('hidden');
  if (state === 'saving') {
    badge.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold border bg-amber-500/15 text-amber-300 border-amber-500/30';
  } else if (state === 'saved') {
    badge.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold border bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
  } else {
    badge.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold border bg-rose-500/15 text-rose-300 border-rose-500/30';
  }
}

function saveStateToCache(data) {
  try {
    localStorage.setItem(STATE_CACHE_KEY, JSON.stringify({ savedAt: Date.now(), data }));
  } catch (err) {}
}

function loadStateFromCache() {
  try {
    const raw = localStorage.getItem(STATE_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && parsed.data ? parsed : null;
  } catch (err) {
    return null;
  }
}

function updateDisplayFooter() {
  const offBadge = document.getElementById('offlineBadge');
  const updText = document.getElementById('lastUpdatedText');
  if (offBadge) offBadge.classList.toggle('hidden', !isOffline);
  if (updText) {
    if (lastSyncedAt) {
      const timeStr = new Date(lastSyncedAt).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
      updText.textContent = isOffline ? `Last synced ${timeStr}` : `Updated ${timeStr}`;
    } else {
      updText.textContent = '';
    }
  }
}

function applyStatePayload(data, fromCache = false) {
  if (!fromCache && knownBuildId && data.buildId && data.buildId !== knownBuildId) {
    window.location.reload();
    return;
  }
  if (!fromCache && data.buildId) knownBuildId = data.buildId;
  if (!fromCache && data.serverNow) {
    const parsedServer = Date.parse(data.serverNow);
    if (!Number.isNaN(parsedServer)) {
      serverClockOffsetMs = parsedServer - Date.now();
    }
  }
  appSettings = { theme: 'dark', timezone: 'America/Chicago', showLogo: true, ...(data.settings || {}) };
  ageGroups = data.ageGroups || [];
  ageGroupsMap = data.ageGroupsMap || {};
  roomsList = data.rooms || [];
  roomsMap = data.roomsMap || {};
  scheduleList = data.scheduleList || [];
  if (!fromCache) {
    isOffline = false;
    lastSyncedAt = Date.now();
    saveStateToCache(data);
  }
  populateSelectors();
  renderScheduleTable();
  if (currentTab === 'admin') renderAdminPanel();
  renderDisplay();
  updateDisplayFooter();
}

async function fetchServerState(force = false) {
  try {
    const headers = {};
    if (!force && lastStateEtag) headers['If-None-Match'] = lastStateEtag;
    const res = await fetch('/api/state', { headers });
    if (res.status === 304) {
      isOffline = false;
      lastSyncedAt = Date.now();
      updateDisplayFooter();
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const etag = res.headers.get('ETag');
    if (etag) lastStateEtag = etag;
    const data = await res.json();
    applyStatePayload(data, false);
  } catch (err) {
    console.error('Sync error:', err);
    isOffline = true;
    if (!roomsList.length) {
      const cached = loadStateFromCache();
      if (cached) {
        lastSyncedAt = cached.savedAt || null;
        applyStatePayload(cached.data, true);
      }
    }
    updateDisplayFooter();
  }
}

function handleAuthExpired() {
  isAdminUnlocked = false;
  currentUser = null;
  updateAuthUI();
  switchTab('admin');
}

async function patchRoom(roomId, changes) {
  const room = roomsMap[roomId];
  if (!room) return null;
  setSaveStatus('saving', 'Saving...');
  try {
    const res = await apiFetch(`/api/rooms/${encodeURIComponent(roomId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...changes, version: room.version })
    });
    if (res.status === 401) { handleAuthExpired(); return null; }
    const data = await res.json().catch(() => ({}));
    if (res.status === 409) {
      setSaveStatus('error', 'Conflict — refreshed');
      showAdminToast(data.error || 'Room changed in another tab; refreshed latest data.', { isError: true });
      await fetchServerState(true);
      return null;
    }
    if (!res.ok) {
      setSaveStatus('error', 'Save failed');
      showAdminToast(data.error || 'Could not save room changes.', { isError: true });
      return null;
    }
    roomsMap[roomId] = data.room;
    const idx = roomsList.findIndex(r => r.id === roomId);
    if (idx >= 0) roomsList[idx] = data.room;
    lastStateEtag = null;
    setSaveStatus('saved', 'Saved');
    return data.room;
  } catch (err) {
    console.error('Room save error:', err);
    setSaveStatus('error', 'Network error');
    return null;
  }
}

async function patchClass(classId, changes) {
  const item = scheduleList.find(c => c.id === classId);
  if (!item) return null;
  setSaveStatus('saving', 'Saving...');
  try {
    const res = await apiFetch(`/api/classes/${classId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...changes, version: item.version })
    });
    if (res.status === 401) { handleAuthExpired(); return null; }
    const data = await res.json().catch(() => ({}));
    if (res.status === 409) {
      setSaveStatus('error', data.code === 'SCHEDULE_OVERLAP' ? 'Time overlap' : 'Conflict — refreshed');
      showAdminToast(data.error || 'Class changed in another tab; refreshed latest data.', { isError: true });
      await fetchServerState(true);
      return null;
    }
    if (!res.ok) {
      setSaveStatus('error', 'Save failed');
      showAdminToast(data.error || 'Could not save class changes.', { isError: true });
      return null;
    }
    const idx = scheduleList.findIndex(c => c.id === classId);
    if (idx >= 0) scheduleList[idx] = data.class;
    lastStateEtag = null;
    setSaveStatus('saved', 'Saved');
    return data.class;
  } catch (err) {
    console.error('Class save error:', err);
    setSaveStatus('error', 'Network error');
    return null;
  }
}

function scheduleDebounced(key, fn, delayMs = 450) {
  if (debounceTimers.has(key)) clearTimeout(debounceTimers.get(key));
  debounceTimers.set(key, setTimeout(() => {
    debounceTimers.delete(key);
    fn();
  }, delayMs));
}

function flushDebounced(key, fn) {
  if (debounceTimers.has(key)) {
    clearTimeout(debounceTimers.get(key));
    debounceTimers.delete(key);
    fn();
  }
}

async function checkAuthStatus() {
  try {
    const res = await fetch('/api/auth');
    if (!res.ok) return;
    const data = await res.json();
    csrfToken = data.csrfToken || '';
    isAdminUnlocked = Boolean(data.authenticated);
    currentUser = data.user || null;
    updateAuthUI();
  } catch (err) {}
}

function formatTime12h(hhmm) {
  if (!hhmm || !hhmm.includes(':')) return hhmm || '';
  const [hStr, mStr] = hhmm.split(':');
  let h = parseInt(hStr, 10);
  const ampm = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${mStr} ${ampm}`;
}

function formatTimestampShort(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function normalizeTime(val) {
  if (typeof val === 'number') {
    const totalMinutes = Math.round(val * 24 * 60);
    const h = String(Math.floor(totalMinutes / 60) % 24).padStart(2, '0');
    const m = String(totalMinutes % 60).padStart(2, '0');
    return `${h}:${m}`;
  }
  const str = String(val || '').trim().toUpperCase();
  const match = str.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/);
  if (!match) return '09:00';
  let h = parseInt(match[1], 10);
  const m = match[2];
  const ampm = match[3];
  if (ampm === 'PM' && h < 12) h += 12;
  if (ampm === 'AM' && h === 12) h = 0;
  return `${String(h).padStart(2, '0')}:${m}`;
}

function hhmmToMinutes(hhmm) {
  if (!hhmm || !hhmm.includes(':')) return 0;
  const [h, m] = hhmm.split(':').map(n => parseInt(n, 10));
  return (h || 0) * 60 + (m || 0);
}

function getActiveDayAndTime() {
  if (useLiveClock) {
    const correctedNow = new Date(Date.now() + serverClockOffsetMs);
    const tz = (appSettings && appSettings.timezone) || 'America/Chicago';
    try {
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        weekday: 'long',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23'
      }).formatToParts(correctedNow);
      const weekday = (parts.find(p => p.type === 'weekday') || {}).value || DAYS_OF_WEEK[correctedNow.getDay()];
      let hour = (parts.find(p => p.type === 'hour') || {}).value || '00';
      if (hour === '24') hour = '00';
      const minute = (parts.find(p => p.type === 'minute') || {}).value || '00';
      return { day: weekday, time: `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}` };
    } catch (err) {
      return {
        day: DAYS_OF_WEEK[correctedNow.getDay()],
        time: `${String(correctedNow.getHours()).padStart(2, '0')}:${String(correctedNow.getMinutes()).padStart(2, '0')}`
      };
    }
  }
  return {
    day: document.getElementById('daySelect').value,
    time: document.getElementById('timeSelect').value || '09:30'
  };
}

function populateSelectors() {
  const roomSelect = document.getElementById('roomSelect');
  const adminRoomFilter = document.getElementById('adminRoomFilter');
  const adminAgeFilter = document.getElementById('adminAgeFilter');
  const newRoomDefaultAge = document.getElementById('newRoomDefaultAge');
  const newClassAgeGroup = document.getElementById('newClassAgeGroup');
  const newClassRoom = document.getElementById('newClassRoom');

  const prevRoom = roomSelect.value || new URLSearchParams(window.location.search).get('room');
  const prevAdminFilter = adminRoomFilter.value;
  const prevAgeFilter = adminAgeFilter ? adminAgeFilter.value : 'ALL';

  roomSelect.replaceChildren();
  adminRoomFilter.replaceChildren(
    el('option', { value: 'ALL', text: 'All Rooms' }),
    el('option', { value: 'UNASSIGNED', text: 'Unassigned Classes' })
  );
  if (newClassRoom) {
    newClassRoom.replaceChildren(el('option', { value: '', text: 'Unassigned (Assign Later)' }));
  }

  roomsList.forEach(room => {
    const shortName = room.name.replace(/^Room\s+\S+\s*—\s*/i, '');
    const label = shortName === room.name ? room.name : `${room.id} — ${shortName}`;
    roomSelect.appendChild(el('option', { value: room.id, text: label }));
    adminRoomFilter.appendChild(el('option', { value: room.id, text: label }));
    if (newClassRoom) newClassRoom.appendChild(el('option', { value: room.id, text: label }));
  });

  if (adminAgeFilter) {
    adminAgeFilter.replaceChildren(el('option', { value: 'ALL', text: 'All Age Groups' }));
    ageGroups.forEach(ag => adminAgeFilter.appendChild(el('option', { value: ag.id, text: ag.label })));
    if (prevAgeFilter === 'ALL' || ageGroupsMap[prevAgeFilter]) adminAgeFilter.value = prevAgeFilter || 'ALL';
  }

  if (newRoomDefaultAge) {
    const prevDef = newRoomDefaultAge.value;
    newRoomDefaultAge.replaceChildren(el('option', { value: '', text: 'None (Neutral when empty)' }));
    ageGroups.forEach(ag => newRoomDefaultAge.appendChild(el('option', { value: ag.id, text: ag.label })));
    if (prevDef && ageGroupsMap[prevDef]) newRoomDefaultAge.value = prevDef;
  }

  if (newClassAgeGroup) {
    const prevAg = newClassAgeGroup.value;
    newClassAgeGroup.replaceChildren();
    ageGroups.forEach(ag => newClassAgeGroup.appendChild(el('option', { value: ag.id, text: ag.label, selected: ag.isDefault })));
    if (prevAg && ageGroupsMap[prevAg]) newClassAgeGroup.value = prevAg;
  }

  if (prevRoom && roomsMap[prevRoom]) roomSelect.value = prevRoom;
  if (prevAdminFilter === 'ALL' || prevAdminFilter === 'UNASSIGNED' || roomsMap[prevAdminFilter]) {
    adminRoomFilter.value = prevAdminFilter || 'ALL';
  }

  const daySelect = document.getElementById('daySelect');
  const prevDay = daySelect.value;
  const days = [...new Set(scheduleList.map(s => s.day))];
  daySelect.replaceChildren();
  (days.length ? days : ['Sunday']).forEach(d => {
    daySelect.appendChild(el('option', { value: d, text: d }));
  });
  if (days.includes(prevDay)) daySelect.value = prevDay;

  document.getElementById('datasetBadge').textContent = `${roomsList.length} rooms · ${scheduleList.length} classes`;
}

function applyDisplayTheme() {
  const isLight = (appSettings && appSettings.theme) === 'light';
  const view = document.getElementById('viewDisplay');
  const frame = document.getElementById('ipadFrame');
  const body = document.getElementById('displayBody');
  const card = document.getElementById('currentClassCard');
  const nextCard = document.getElementById('upNextCard');

  view.style.backgroundColor = isLight ? '#FAF8F4' : '';
  frame.className = isLight
    ? 'w-full max-w-4xl rounded-3xl overflow-hidden shadow-xl border border-stone-200 transition-colors duration-300 bg-white'
    : 'w-full max-w-4xl rounded-3xl overflow-hidden shadow-2xl border border-slate-800 transition-colors duration-300 bg-slate-900';
  body.className = isLight
    ? 'p-6 sm:p-10 space-y-6 bg-[#FAF8F4] transition-colors duration-300'
    : 'p-6 sm:p-10 space-y-6 bg-slate-900 transition-colors duration-300';
  card.className = isLight
    ? 'rounded-2xl bg-white border border-stone-200 p-6 sm:p-8 shadow-sm transition-colors duration-300'
    : 'rounded-2xl bg-slate-800/70 border border-slate-700/70 p-6 sm:p-8 transition-colors duration-300';
  nextCard.className = isLight
    ? 'rounded-2xl bg-white border border-stone-200 px-6 py-4 flex flex-wrap items-center justify-between gap-3 shadow-sm transition-colors duration-300'
    : 'rounded-2xl bg-slate-800/40 border border-slate-800 px-6 py-4 flex flex-wrap items-center justify-between gap-3 transition-colors duration-300';

  document.getElementById('currentClassHeading').className = isLight
    ? 'text-xs font-semibold uppercase tracking-widest text-[#5B6270]'
    : 'text-xs font-semibold uppercase tracking-widest text-slate-400';
  document.getElementById('currentTimeLabel').className = isLight ? 'text-[#5B6270]' : 'text-slate-400';
  document.getElementById('currentLeaderLabel').className = isLight ? 'text-[#5B6270]' : 'text-slate-400';
  document.getElementById('currentWindow').className = isLight ? 'font-semibold text-[#1C1F24]' : 'font-semibold text-white';
  document.getElementById('currentLeader').className = isLight ? 'font-semibold text-[#1C1F24]' : 'font-semibold text-white';
  document.getElementById('upNextLabel').className = isLight
    ? 'text-xs font-bold uppercase tracking-wider text-[#5B6270]'
    : 'text-xs font-bold uppercase tracking-wider text-slate-400';
  document.getElementById('nextClass').className = isLight
    ? 'text-base sm:text-lg font-semibold text-[#1C1F24]'
    : 'text-base sm:text-lg font-semibold text-slate-200';
  document.getElementById('nextWindow').className = isLight
    ? 'text-sm font-medium text-[#5B6270]'
    : 'text-sm font-medium text-slate-400';

  const noteBanner = document.getElementById('roomNoteBanner');
  noteBanner.className = noteBanner.classList.contains('hidden')
    ? (isLight
        ? 'hidden px-6 sm:px-10 py-3 bg-amber-100 border-b border-amber-300 text-amber-950 text-sm sm:text-base font-medium flex items-center gap-2'
        : 'hidden px-6 sm:px-10 py-3 bg-amber-500/15 border-b border-amber-500/30 text-amber-200 text-sm sm:text-base font-medium flex items-center gap-2')
    : (isLight
        ? 'px-6 sm:px-10 py-3 bg-amber-100 border-b border-amber-300 text-amber-950 text-sm sm:text-base font-medium flex items-center gap-2'
        : 'px-6 sm:px-10 py-3 bg-amber-500/15 border-b border-amber-500/30 text-amber-200 text-sm sm:text-base font-medium flex items-center gap-2');
}

function renderDisplay() {
  applyDisplayTheme();
  const isLight = (appSettings && appSettings.theme) === 'light';
  const showLogo = appSettings.showLogo !== false;
  const logoPlate = document.getElementById('headerLogoPlate');
  if (logoPlate) logoPlate.classList.toggle('hidden', !showLogo);

  const ipadFrame = document.getElementById('ipadFrame');
  const unknownCard = document.getElementById('unknownRoomCard');

  const urlKiosk = new URLSearchParams(window.location.search).get('kiosk') === '1';
  if (urlKiosk && requestedUrlRoom && roomsList.length && !roomsMap[requestedUrlRoom]) {
    ipadFrame.classList.add('hidden');
    unknownCard.classList.remove('hidden');
    document.getElementById('unknownRoomHeading').textContent = `Room "${requestedUrlRoom}" Not Found`;
    document.getElementById('unknownRoomMessage').textContent =
      `No room with ID "${requestedUrlRoom}" is in the current schedule. This screen will load automatically as soon as it is added.`;
    return;
  }
  unknownCard.classList.add('hidden');
  ipadFrame.classList.remove('hidden');

  const roomId = document.getElementById('roomSelect').value;
  const { day, time } = getActiveDayAndTime();
  const room = roomsMap[roomId];
  if (!room) return;

  const dayClasses = scheduleList
    .filter(item => item.room === roomId && item.day === day)
    .sort((a, b) => a.start.localeCompare(b.start));

  const matchingNow = dayClasses.filter(c => time >= c.start && time < c.end);
  const current = matchingNow.find(c => !c.cancelled) || matchingNow[0] || null;
  const next = dayClasses.find(c => c.start > time && !c.cancelled) || null;

  const nowMins = hhmmToMinutes(time);
  const minsUntilNext = next ? (hhmmToMinutes(next.start) - nowMins) : Infinity;
  const showUpNextHero = !current && next && minsUntilNext > 0 && minsUntilNext <= 30;
  const afterNext = showUpNextHero
    ? (dayClasses.find(c => c.id !== next.id && c.start >= next.end && !c.cancelled) || null)
    : next;

  const effectiveAgeId =
    room.overrideAgeGroupId ||
    (current && current.ageGroupId) ||
    (next && next.ageGroupId) ||
    room.defaultAgeGroupId ||
    null;
  const theme = effectiveAgeId ? getTheme(effectiveAgeId) : FALLBACK_THEME;

  const bannerEl = document.getElementById('ageBanner');
  bannerEl.className = 'px-6 sm:px-10 py-6 sm:py-8 flex flex-wrap items-center justify-between gap-4 transition-colors duration-300';
  bannerEl.style.backgroundColor = theme.bannerBg;
  bannerEl.style.color = theme.bannerText;

  document.getElementById('ageBadge').textContent = theme.label;
  document.getElementById('roomTitle').textContent = room.name;
  document.getElementById('displayTime').textContent = formatTime12h(time);
  document.getElementById('displayDay').textContent = day || '—';

  const noteBanner = document.getElementById('roomNoteBanner');
  if (room.note && room.note.trim()) {
    document.getElementById('roomNoteText').textContent = room.note.trim();
    noteBanner.classList.remove('hidden');
  } else {
    noteBanner.classList.add('hidden');
  }

  const statusText = document.getElementById('statusText');
  const statusIndicator = document.getElementById('statusIndicator');
  const cancelledPill = document.getElementById('cancelledPill');
  const upNextCountdown = document.getElementById('upNextCountdown');
  const currentHeading = document.getElementById('currentClassHeading');
  const currentClassEl = document.getElementById('currentClass');
  const progressWrap = document.getElementById('classProgressWrap');
  const progressTrack = document.getElementById('classProgressTrack');
  const progressBar = document.getElementById('classProgressBar');
  const progressText = document.getElementById('classProgressText');

  if (current && current.cancelled) {
    currentHeading.textContent = 'Current Class';
    statusText.textContent = 'Cancelled';
    statusIndicator.className = 'w-2.5 h-2.5 rounded-full bg-rose-400';
    cancelledPill.classList.remove('hidden');
    upNextCountdown.classList.add('hidden');
    progressWrap.classList.add('hidden');
    currentClassEl.textContent = current.title;
    currentClassEl.className = isLight
      ? 'text-2xl sm:text-4xl font-bold text-rose-700 line-through mb-3'
      : 'text-2xl sm:text-4xl font-bold text-rose-300 line-through mb-3';
    document.getElementById('currentWindow').textContent = `${formatTime12h(current.start)} – ${formatTime12h(current.end)}`;
    document.getElementById('currentLeader').textContent = current.leader || '—';
  } else if (current) {
    currentHeading.textContent = 'Current Class';
    statusText.textContent = 'In Session';
    statusIndicator.className = 'w-2.5 h-2.5 rounded-full bg-emerald-300 animate-pulse';
    cancelledPill.classList.add('hidden');
    upNextCountdown.classList.add('hidden');
    currentClassEl.textContent = current.title;
    currentClassEl.className = isLight
      ? 'text-2xl sm:text-4xl font-bold text-[#1C1F24] mb-3'
      : 'text-2xl sm:text-4xl font-bold text-white mb-3';
    document.getElementById('currentWindow').textContent = `${formatTime12h(current.start)} – ${formatTime12h(current.end)}`;
    document.getElementById('currentLeader').textContent = current.leader || '—';

    const startM = hhmmToMinutes(current.start);
    const endM = hhmmToMinutes(current.end);
    const totalM = Math.max(1, endM - startM);
    const elapsedM = Math.max(0, Math.min(totalM, nowMins - startM));
    const remainM = Math.max(0, endM - nowMins);
    const pct = Math.round((elapsedM / totalM) * 100);
    progressWrap.classList.remove('hidden');
    progressTrack.className = isLight
      ? 'w-full h-3 rounded-full bg-[#E7E3DC] overflow-hidden'
      : 'w-full h-3 rounded-full bg-slate-700/80 overflow-hidden';
    progressBar.style.width = `${pct}%`;
    progressBar.style.backgroundColor = theme.bannerBg;
    progressText.className = isLight
      ? 'text-xs sm:text-sm font-semibold text-[#5B6270] tabular-nums'
      : 'text-xs sm:text-sm font-semibold text-slate-400 tabular-nums';
    progressText.textContent = remainM <= 5 ? 'Ending soon' : `${remainM} min left`;
  } else if (showUpNextHero) {
    currentHeading.textContent = 'Starting Soon';
    statusText.textContent = 'Up Next';
    statusIndicator.className = 'w-2.5 h-2.5 rounded-full bg-amber-300 animate-pulse';
    cancelledPill.classList.add('hidden');
    upNextCountdown.textContent = `Starts in ${minsUntilNext} min`;
    upNextCountdown.classList.remove('hidden');
    progressWrap.classList.add('hidden');
    currentClassEl.textContent = next.title;
    currentClassEl.className = isLight
      ? 'text-2xl sm:text-4xl font-bold text-[#1C1F24] mb-3'
      : 'text-2xl sm:text-4xl font-bold text-white mb-3';
    document.getElementById('currentWindow').textContent = `${formatTime12h(next.start)} – ${formatTime12h(next.end)}`;
    document.getElementById('currentLeader').textContent = next.leader || '—';
  } else {
    currentHeading.textContent = 'Current Class';
    statusText.textContent = 'Room Open';
    statusIndicator.className = 'w-2.5 h-2.5 rounded-full bg-slate-300';
    cancelledPill.classList.add('hidden');
    upNextCountdown.classList.add('hidden');
    progressWrap.classList.add('hidden');
    currentClassEl.textContent = 'No Class Scheduled Right Now';
    currentClassEl.className = isLight
      ? 'text-2xl sm:text-4xl font-bold text-[#5B6270] mb-3'
      : 'text-2xl sm:text-4xl font-bold text-slate-300 mb-3';
    document.getElementById('currentWindow').textContent = 'Open / Available';
    document.getElementById('currentLeader').textContent = '—';
  }

  if (afterNext) {
    document.getElementById('nextClass').textContent = afterNext.title;
    document.getElementById('nextWindow').textContent = `${formatTime12h(afterNext.start)} – ${formatTime12h(afterNext.end)}`;
  } else {
    document.getElementById('nextClass').textContent = showUpNextHero ? 'Last class today' : 'No more classes today';
    document.getElementById('nextWindow').textContent = '';
  }
  updateDisplayFooter();
}

function renderScheduleTable() {
  const tbody = document.getElementById('scheduleTableBody');
  tbody.replaceChildren();
  document.getElementById('tableCount').textContent = `${scheduleList.length} rows`;
  scheduleList.forEach(item => {
    const room = item.room ? (roomsMap[item.room] || { name: `Room ${item.room}` }) : { name: 'Unassigned' };
    const theme = getTheme(item.ageGroupId);
    const tr = el('tr', { className: 'hover:bg-slate-800/40' }, [
      el('td', { className: 'px-4 py-3 font-medium text-white', text: room.name }),
      el('td', { className: 'px-4 py-3' }, [
        el('span', {
          className: 'px-2 py-0.5 rounded-full text-xs font-semibold',
          style: { backgroundColor: theme.badgeBg, color: theme.badgeText },
          text: theme.label
        })
      ]),
      el('td', { className: 'px-4 py-3', text: item.day }),
      el('td', { className: 'px-4 py-3 whitespace-nowrap', text: `${formatTime12h(item.start)} – ${formatTime12h(item.end)}` }),
      el('td', { className: 'px-4 py-3 font-medium text-slate-100', text: item.title }),
      el('td', { className: 'px-4 py-3', text: item.leader || '—' })
    ]);
    tbody.appendChild(tr);
  });
}

function showAdminToast(msg, opts = {}) {
  const toast = document.getElementById('adminToast');
  const textEl = document.getElementById('adminToastText');
  const undoBtn = document.getElementById('adminUndoBtn');
  textEl.textContent = msg;
  activeUndoAction = opts.onUndo || null;
  undoBtn.classList.toggle('hidden', !activeUndoAction);

  toast.className = opts.isError
    ? 'rounded-xl px-4 py-2.5 text-sm font-medium bg-rose-500/15 text-rose-300 border border-rose-500/30 flex items-center justify-between gap-3'
    : 'rounded-xl px-4 py-2.5 text-sm font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center justify-between gap-3';
  toast.classList.remove('hidden');

  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.add('hidden');
    activeUndoAction = null;
  }, opts.onUndo ? 8000 : 3500);
}

let activeAdminSubTab = 'rooms';
let ageGroupsWithUsage = [];
let curatedPalette = [
  { name: 'Indigo', hex: '#4338ca' },
  { name: 'Teal', hex: '#0f766e' },
  { name: 'Sky Blue', hex: '#0369a1' },
  { name: 'Emerald', hex: '#047857' },
  { name: 'Amber', hex: '#b45309' },
  { name: 'Purple', hex: '#7e22ce' },
  { name: 'Raspberry', hex: '#be185d' },
  { name: 'Royal Blue', hex: '#1d4ed8' },
  { name: 'Crimson', hex: '#b91c1c' },
  { name: 'Slate', hex: '#475569' }
];
let agPreviewLight = false;
let activeSwapClassId = null;
let pendingDeleteAgeGroupId = null;

function hexToLuminance(hex) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || '').trim());
  if (!m) return 0;
  const rgb = [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)].map(v => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

function calcContrastRatio(hex1, hex2) {
  const l1 = hexToLuminance(hex1);
  const l2 = hexToLuminance(hex2);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return Math.round(((lighter + 0.05) / (darker + 0.05)) * 100) / 100;
}

function pickAutoTextColor(bgHex) {
  return calcContrastRatio(bgHex, '#ffffff') >= calcContrastRatio(bgHex, '#0f172a') ? '#ffffff' : '#0f172a';
}

function switchAdminSubTab(subTab) {
  activeAdminSubTab = subTab;
  document.getElementById('adminSubRooms').classList.toggle('hidden', subTab !== 'rooms');
  document.getElementById('adminSubClasses').classList.toggle('hidden', subTab !== 'classes');
  document.getElementById('adminSubAgeGroups').classList.toggle('hidden', subTab !== 'agegroups');
  document.getElementById('adminSubSettings').classList.toggle('hidden', subTab !== 'settings');

  const map = {
    rooms: 'subTabRooms',
    classes: 'subTabClasses',
    agegroups: 'subTabAgeGroups',
    settings: 'subTabSettings'
  };
  for (const [k, btnId] of Object.entries(map)) {
    const btn = document.getElementById(btnId);
    if (!btn) continue;
    btn.className = k === subTab
      ? 'px-3.5 py-1.5 rounded-lg text-xs sm:text-sm font-semibold bg-indigo-600 text-white transition'
      : 'px-3.5 py-1.5 rounded-lg text-xs sm:text-sm font-semibold bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition';
  }

  if (subTab === 'agegroups') fetchAgeGroupsDetailed();
  if (subTab === 'settings') {
    renderSettingsControls();
    fetchAdminUsers();
    fetchAuditLog();
  }
}

function renderSettingsControls() {
  const isLight = (appSettings && appSettings.theme) === 'light';
  const darkBtn = document.getElementById('themeDarkBtn');
  const lightBtn = document.getElementById('themeLightBtn');
  if (darkBtn && lightBtn) {
    darkBtn.className = !isLight
      ? 'px-4 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white transition'
      : 'px-4 py-1.5 rounded-lg text-xs font-semibold text-slate-300 hover:text-white transition';
    lightBtn.className = isLight
      ? 'px-4 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white transition'
      : 'px-4 py-1.5 rounded-lg text-xs font-semibold text-slate-300 hover:text-white transition';
  }
  const tzSel = document.getElementById('churchTzSelect');
  if (tzSel && appSettings && appSettings.timezone) {
    if ([...tzSel.options].some(o => o.value === appSettings.timezone)) {
      tzSel.value = appSettings.timezone;
    }
  }
  const logoChk = document.getElementById('showLogoCheck');
  if (logoChk) logoChk.checked = appSettings.showLogo !== false;
}

function renderAgeGroupPreview() {
  const label = document.getElementById('agLabelInput').value.trim() || 'Age Group';
  const bgRaw = document.getElementById('agColorHex').value.trim();
  const bg = /^#[0-9a-f]{6}$/i.test(bgRaw) ? bgRaw : '#4338ca';
  const autoText = document.getElementById('agAutoTextCheck').checked;
  document.getElementById('agCustomTextWrap').classList.toggle('hidden', autoText);
  const customText = document.getElementById('agTextHex').value.trim();
  const fg = autoText ? pickAutoTextColor(bg) : (/^#[0-9a-f]{6}$/i.test(customText) ? customText : '#ffffff');

  const ratio = calcContrastRatio(bg, fg);
  const badge = document.getElementById('agContrastBadge');
  if (ratio >= 7.0) {
    badge.textContent = `Contrast ${ratio}:1 · WCAG AAA`;
    badge.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold border bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
  } else if (ratio >= 4.5) {
    badge.textContent = `Contrast ${ratio}:1 · WCAG AA`;
    badge.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold border bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
  } else {
    badge.textContent = `Contrast ${ratio}:1 · Low Contrast (< 4.5:1)`;
    badge.className = 'inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold border bg-amber-500/15 text-amber-300 border-amber-500/30';
  }

  const miniBanner = document.getElementById('agMiniBanner');
  miniBanner.style.backgroundColor = bg;
  miniBanner.style.color = fg;
  document.getElementById('agMiniBadge').textContent = label;

  const miniBody = document.getElementById('agMiniBody');
  const miniFrame = document.getElementById('agMiniFrame');
  if (agPreviewLight) {
    miniFrame.className = 'rounded-2xl overflow-hidden border border-stone-300 shadow-lg bg-[#FAF8F4]';
    miniBody.className = 'p-4 space-y-2 bg-[#FAF8F4] text-[#1C1F24]';
  } else {
    miniFrame.className = 'rounded-2xl overflow-hidden border border-slate-700 shadow-lg bg-slate-900';
    miniBody.className = 'p-4 space-y-2 bg-slate-900 text-white';
  }
}

function renderPaletteSwatches() {
  const container = document.getElementById('agPaletteSwatches');
  if (!container) return;
  container.replaceChildren();
  curatedPalette.forEach(p => {
    const btn = el('button', {
      type: 'button',
      title: `${p.name} (${p.hex})`,
      'data-palette-hex': p.hex,
      className: 'w-7 h-7 rounded-full border-2 border-white/30 hover:scale-110 transition cursor-pointer',
      style: { backgroundColor: p.hex }
    });
    container.appendChild(btn);
  });
}

function resetAgeGroupForm() {
  document.getElementById('agEditId').value = '';
  document.getElementById('agFormHeading').textContent = 'Create Age Group';
  document.getElementById('agSubmitBtn').textContent = 'Create Age Group';
  document.getElementById('agResetFormBtn').classList.add('hidden');
  document.getElementById('agLabelInput').value = '';
  document.getElementById('agColorPicker').value = '#4338ca';
  document.getElementById('agColorHex').value = '#4338ca';
  document.getElementById('agAutoTextCheck').checked = true;
  document.getElementById('agTextPicker').value = '#ffffff';
  document.getElementById('agTextHex').value = '#ffffff';
  document.getElementById('agIsDefaultCheck').checked = false;
  document.getElementById('agAliasesInput').value = '';
  renderAgeGroupPreview();
}

function populateAgeGroupFormForEdit(ag) {
  document.getElementById('agEditId').value = ag.id;
  document.getElementById('agFormHeading').textContent = `Edit Age Group — ${ag.label}`;
  document.getElementById('agSubmitBtn').textContent = 'Save Age Group';
  document.getElementById('agResetFormBtn').classList.remove('hidden');
  document.getElementById('agLabelInput').value = ag.label;
  document.getElementById('agColorPicker').value = ag.bannerBg;
  document.getElementById('agColorHex').value = ag.bannerBg;
  const autoText = ag.bannerText.toLowerCase() === pickAutoTextColor(ag.bannerBg).toLowerCase();
  document.getElementById('agAutoTextCheck').checked = autoText;
  document.getElementById('agTextPicker').value = ag.bannerText;
  document.getElementById('agTextHex').value = ag.bannerText;
  document.getElementById('agIsDefaultCheck').checked = Boolean(ag.isDefault);
  document.getElementById('agAliasesInput').value = (ag.aliases || []).join(', ');
  renderAgeGroupPreview();
}

async function fetchAgeGroupsDetailed() {
  try {
    const res = await fetch('/api/age-groups');
    if (!res.ok) return;
    const data = await res.json();
    ageGroupsWithUsage = data.ageGroups || [];
    if (data.palette && data.palette.length) curatedPalette = data.palette;
    renderPaletteSwatches();
    renderAgeGroupsTable();
    renderAgeGroupPreview();
  } catch (err) {}
}

function renderAgeGroupsTable() {
  const tbody = document.getElementById('adminAgeGroupsBody');
  if (!tbody) return;
  tbody.replaceChildren();
  const list = ageGroupsWithUsage.length ? ageGroupsWithUsage : ageGroups;
  list.forEach((ag, idx) => {
    const ratio = ag.contrastRatio || calcContrastRatio(ag.bannerBg, ag.bannerText);
    const upBtn = el('button', {
      type: 'button',
      'data-ag-move': ag.id,
      'data-ag-dir': '-1',
      className: 'px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-xs border border-slate-700',
      text: '↑'
    });
    if (idx === 0) upBtn.disabled = true;

    const downBtn = el('button', {
      type: 'button',
      'data-ag-move': ag.id,
      'data-ag-dir': '1',
      className: 'px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-xs border border-slate-700',
      text: '↓'
    });
    if (idx === list.length - 1) downBtn.disabled = true;

    const labelNodes = [
      el('span', { className: 'w-4 h-4 rounded-full border border-white/25 inline-block', style: { backgroundColor: ag.bannerBg } }),
      el('span', { className: 'font-semibold text-white', text: ag.label }),
      el('span', { className: 'font-mono text-xs text-slate-400', text: ag.bannerBg })
    ];
    if (ag.isDefault) {
      labelNodes.push(el('span', { className: 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-indigo-500/20 text-indigo-300', text: 'Default' }));
    }

    const tr = el('tr', { className: 'hover:bg-slate-800/40' }, [
      el('td', { className: 'px-4 py-3 whitespace-nowrap' }, [
        el('div', { className: 'flex items-center gap-1' }, [upBtn, downBtn])
      ]),
      el('td', { className: 'px-4 py-3 whitespace-nowrap' }, [
        el('div', { className: 'flex items-center gap-2' }, labelNodes)
      ]),
      el('td', { className: 'px-4 py-3 whitespace-nowrap text-xs' }, [
        el('span', {
          className: `px-2 py-0.5 rounded font-semibold ${ratio >= 4.5 ? 'bg-emerald-500/15 text-emerald-300' : 'bg-amber-500/15 text-amber-300'}`,
          text: `${ratio}:1`
        })
      ]),
      el('td', { className: 'px-4 py-3 text-xs text-slate-400', text: (ag.aliases || []).join(', ') || '—' }),
      el('td', { className: 'px-4 py-3 whitespace-nowrap text-xs text-slate-300', text: `${ag.classCount ?? 0} classes · ${ag.roomCount ?? 0} rooms` }),
      el('td', { className: 'px-4 py-3 text-right whitespace-nowrap' }, [
        el('div', { className: 'inline-flex items-center gap-1.5' }, [
          el('button', {
            type: 'button',
            'data-ag-edit': ag.id,
            className: 'px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-indigo-600 text-slate-200 hover:text-white border border-slate-700 transition',
            text: 'Edit'
          }),
          el('button', {
            type: 'button',
            'data-ag-delete': ag.id,
            className: 'px-2.5 py-1 rounded-lg text-xs font-semibold bg-rose-600/15 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 transition',
            text: 'Delete'
          })
        ])
      ])
    ]);
    tbody.appendChild(tr);
  });
}

function openSwapModal(classId) {
  const item = scheduleList.find(c => c.id === classId);
  if (!item) return;
  activeSwapClassId = classId;
  document.getElementById('swapError').classList.add('hidden');
  document.getElementById('swapClassSummary').textContent =
    `${item.title} · ${item.day} ${formatTime12h(item.start)}–${formatTime12h(item.end)} (Currently: ${item.room ? `Room ${item.room}` : 'Unassigned'})`;

  const roomSel = document.getElementById('swapTargetRoomSelect');
  roomSel.replaceChildren(el('option', { value: '', text: 'Unassigned (No Room)' }));
  roomsList.forEach(r => {
    if (r.id === item.room) return;
    const busy = scheduleList.find(
      c => c.id !== item.id && !c.cancelled && c.room === r.id && c.day === item.day && c.start < item.end && c.end > item.start
    );
    const status = busy ? `Busy: ${busy.title}` : 'Free';
    roomSel.appendChild(el('option', { value: r.id, text: `${r.name} (${status})` }));
  });

  const exSel = document.getElementById('swapExchangeClassSelect');
  exSel.replaceChildren();
  scheduleList.filter(c => c.id !== item.id && c.room !== item.room).forEach(c => {
    exSel.appendChild(el('option', {
      value: String(c.id),
      text: `${c.room ? `Room ${c.room}` : 'Unassigned'} — ${c.title} (${c.day} ${formatTime12h(c.start)})`
    }));
  });

  document.querySelector('input[name="swapAction"][value="move"]').checked = true;
  document.getElementById('swapTargetRoomWrap').classList.remove('hidden');
  document.getElementById('swapExchangeWrap').classList.add('hidden');
  document.getElementById('swapModal').classList.remove('hidden');
}

function openDeleteAgeModal(groupId, classCount, roomCount) {
  const ag = ageGroupsMap[groupId];
  if (!ag) return;
  pendingDeleteAgeGroupId = groupId;
  document.getElementById('deleteAgeSummary').textContent =
    `"${ag.label}" is currently used by ${classCount} class(es) and ${roomCount} room(s). Select another age group to reassign them atomically before deleting.`;
  const sel = document.getElementById('deleteAgeReassignSelect');
  sel.replaceChildren();
  ageGroups.filter(g => g.id !== groupId).forEach(g => {
    sel.appendChild(el('option', { value: g.id, text: g.label }));
  });
  document.getElementById('deleteAgeModal').classList.remove('hidden');
}

function renderAdminPanel() {
  const query = (document.getElementById('adminSearch').value || '').trim().toLowerCase();
  const grid = document.getElementById('adminRoomsGrid');
  grid.replaceChildren();

  roomsList.forEach(room => {
    const id = room.id;
    if (query && !id.toLowerCase().includes(query) && !room.name.toLowerCase().includes(query)) return;
    const displayTheme = getTheme(room.overrideAgeGroupId || room.defaultAgeGroupId);
    const classCount = scheduleList.filter(c => c.room === id).length;

    const nameInput = el('input', {
      type: 'text',
      'data-name-room': id,
      value: room.name,
      className: 'flex-1 min-w-[140px] bg-slate-800/70 text-white font-bold text-sm rounded-lg px-2.5 py-1 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500'
    });

    const ageSelect = el('select', {
      'data-age-room': id,
      className: 'w-full bg-slate-800 text-slate-100 text-sm rounded-lg px-2.5 py-1.5 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500'
    }, [
      el('option', { value: '', text: 'Auto (Use Active Class Age Group)', selected: !room.overrideAgeGroupId }),
      ...ageGroups.map(ag => el('option', {
        value: ag.id,
        text: `Override: ${ag.label}`,
        selected: room.overrideAgeGroupId === ag.id
      }))
    ]);

    const noteInput = el('input', {
      type: 'text',
      'data-note-room': id,
      value: room.note || '',
      placeholder: 'e.g. Moved to Hall B',
      className: 'w-full bg-slate-800 text-slate-100 text-sm rounded-lg px-2.5 py-1.5 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500'
    });

    const card = el('div', { className: 'rounded-2xl bg-slate-900 border border-slate-800 p-5 space-y-4' }, [
      el('div', { className: 'flex flex-wrap items-center justify-between gap-2' }, [
        el('div', { className: 'flex items-center gap-2 flex-1 min-w-0' }, [
          el('span', { className: 'w-3 h-3 rounded-full shrink-0', style: { backgroundColor: displayTheme.bannerBg } }),
          nameInput,
          el('span', { className: 'text-xs text-slate-400 shrink-0', text: `${classCount} cls` })
        ]),
        el('div', { className: 'flex items-center gap-1.5' }, [
          el('button', {
            type: 'button',
            'data-copy-link': id,
            className: 'text-xs font-semibold px-2 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700 transition',
            text: 'Copy Link'
          }),
          el('button', {
            type: 'button',
            'data-preview-room': id,
            className: 'text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-indigo-600 text-slate-200 hover:text-white border border-slate-700 transition',
            text: 'Preview'
          }),
          el('button', {
            type: 'button',
            'data-delete-room': id,
            className: 'text-xs font-semibold px-2 py-1.5 rounded-lg bg-rose-600/15 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 transition',
            text: 'Delete'
          })
        ])
      ]),
      el('div', { className: 'grid grid-cols-1 sm:grid-cols-2 gap-3' }, [
        el('label', { className: 'block text-xs text-slate-400' }, [
          el('span', { className: 'block mb-1', text: 'Age Color Override' }),
          ageSelect
        ]),
        el('label', { className: 'block text-xs text-slate-400' }, [
          el('span', { className: 'block mb-1', text: 'Door Notice (Optional)' }),
          noteInput
        ])
      ])
    ]);
    grid.appendChild(card);
  });

  const roomFilter = document.getElementById('adminRoomFilter').value;
  const ageFilterEl = document.getElementById('adminAgeFilter');
  const ageFilter = ageFilterEl ? ageFilterEl.value : 'ALL';
  const tbody = document.getElementById('adminClassesBody');
  tbody.replaceChildren();
  scheduleList
    .filter(item => {
      if (roomFilter === 'UNASSIGNED' && item.room) return false;
      if (roomFilter !== 'ALL' && roomFilter !== 'UNASSIGNED' && item.room !== roomFilter) return false;
      if (ageFilter !== 'ALL' && item.ageGroupId !== ageFilter) return false;
      return true;
    })
    .forEach(item => {
      const roomSelectInline = el('select', {
        'data-edit-room': String(item.id),
        className: 'bg-slate-800/80 text-white text-xs rounded-lg px-2 py-1 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500'
      }, [
        el('option', { value: '', text: 'Unassigned', selected: !item.room }),
        ...roomsList.map(r => el('option', { value: r.id, text: r.id, selected: item.room === r.id }))
      ]);

      const classAgeSelect = el('select', {
        'data-edit-age': String(item.id),
        className: 'bg-slate-800/80 text-slate-100 text-xs rounded-lg px-2 py-1 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500'
      }, ageGroups.map(ag => el('option', {
        value: ag.id,
        text: ag.label,
        selected: item.ageGroupId === ag.id
      })));

      const titleInput = el('input', {
        type: 'text',
        'data-edit-title': String(item.id),
        value: item.title,
        className: `w-full min-w-[150px] bg-slate-800/80 text-sm rounded-lg px-2.5 py-1 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 ${item.cancelled ? 'line-through text-rose-300' : 'text-white'}`
      });

      const leaderInput = el('input', {
        type: 'text',
        'data-edit-leader': String(item.id),
        value: item.leader || '',
        className: 'w-full min-w-[120px] bg-slate-800/80 text-slate-200 text-sm rounded-lg px-2.5 py-1 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500'
      });

      const swapBtn = el('button', {
        type: 'button',
        'data-open-swap': String(item.id),
        className: 'px-2 py-1 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-indigo-600 text-slate-200 hover:text-white border border-slate-700 transition',
        text: 'Swap'
      });

      const cancelBtn = el('button', {
        type: 'button',
        'data-toggle-cancel': String(item.id),
        className: `px-2.5 py-1 rounded-lg text-xs font-semibold transition ${item.cancelled ? 'bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-600/30' : 'bg-rose-600/20 text-rose-300 border border-rose-500/40 hover:bg-rose-600/30'}`,
        text: item.cancelled ? 'Restore' : 'Cancel'
      });

      const delBtn = el('button', {
        type: 'button',
        'data-delete-class': String(item.id),
        className: 'px-2 py-1 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-rose-600/30 text-slate-400 hover:text-rose-200 border border-slate-700 transition',
        text: 'Del'
      });

      const tr = el('tr', { className: item.cancelled ? 'bg-rose-950/20' : 'hover:bg-slate-800/40' }, [
        el('td', { className: 'px-3 py-3 whitespace-nowrap' }, [roomSelectInline]),
        el('td', { className: 'px-3 py-3 whitespace-nowrap' }, [classAgeSelect]),
        el('td', { className: 'px-3 py-3 whitespace-nowrap text-xs' }, [
          el('div', { className: 'font-medium text-slate-200', text: item.day }),
          el('div', { className: 'text-slate-400', text: `${formatTime12h(item.start)} – ${formatTime12h(item.end)}` })
        ]),
        el('td', { className: 'px-3 py-3' }, [titleInput]),
        el('td', { className: 'px-3 py-3' }, [leaderInput]),
        el('td', { className: 'px-3 py-3 text-right whitespace-nowrap' }, [
          el('div', { className: 'inline-flex items-center gap-1' }, [swapBtn, cancelBtn, delBtn])
        ])
      ]);
      tbody.appendChild(tr);
    });

  renderSettingsControls();
}

async function fetchAdminUsers() {
  const section = document.getElementById('adminUsersSection');
  if (!currentUser || currentUser.role !== 'super_admin') {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');
  try {
    const res = await apiFetch('/api/users');
    if (!res.ok) return;
    const data = await res.json();
    renderUsersTable(data.users || []);
  } catch (err) {}
}

function renderUsersTable(users) {
  const tbody = document.getElementById('adminUsersBody');
  tbody.replaceChildren();
  users.forEach(u => {
    const isSelf = currentUser && currentUser.id === u.id;
    const toggleBtn = el('button', {
      type: 'button',
      'data-toggle-user-active': String(u.id),
      'data-next-active': u.isActive ? '0' : '1',
      className: `px-2.5 py-1 rounded-lg text-xs font-semibold border transition ${u.isActive ? 'bg-rose-600/15 text-rose-300 border-rose-500/30 hover:bg-rose-600/25' : 'bg-emerald-600/15 text-emerald-300 border-emerald-500/30 hover:bg-emerald-600/25'}`,
      text: u.isActive ? 'Deactivate' : 'Reactivate'
    });
    if (isSelf) toggleBtn.disabled = true;

    const tr = el('tr', { className: 'hover:bg-slate-800/40' }, [
      el('td', { className: 'px-4 py-3' }, [
        el('div', { className: 'font-semibold text-white', text: u.displayName }),
        el('div', { className: 'text-xs text-slate-400', text: `@${u.username}` })
      ]),
      el('td', { className: 'px-4 py-3 text-xs uppercase tracking-wide text-indigo-300 font-semibold', text: u.role === 'super_admin' ? 'Super Admin' : 'Admin' }),
      el('td', { className: 'px-4 py-3' }, [
        el('span', {
          className: `px-2 py-0.5 rounded-full text-xs font-semibold ${u.isActive ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700 text-slate-400'}`,
          text: u.isActive ? 'Active' : 'Disabled'
        })
      ]),
      el('td', { className: 'px-4 py-3 text-xs text-slate-400', text: formatTimestampShort(u.lastLoginAt) }),
      el('td', { className: 'px-4 py-3 text-right whitespace-nowrap' }, [isSelf ? el('span', { className: 'text-xs text-slate-500', text: 'You' }) : toggleBtn])
    ]);
    tbody.appendChild(tr);
  });
}

async function fetchAuditLog() {
  if (!isAdminUnlocked) return;
  try {
    const res = await apiFetch('/api/audit?limit=40');
    if (!res.ok) return;
    const data = await res.json();
    renderAuditTable(data.entries || []);
  } catch (err) {}
}

function renderAuditTable(entries) {
  const tbody = document.getElementById('adminAuditBody');
  tbody.replaceChildren();
  if (!entries.length) {
    tbody.appendChild(el('tr', {}, [
      el('td', { colspan: '5', className: 'px-4 py-4 text-center text-slate-500', text: 'No audit events recorded yet.' })
    ]));
    return;
  }
  entries.forEach(entry => {
    const target = entry.targetType ? `${entry.targetType}:${entry.targetId}` : (entry.targetId || '—');
    const tr = el('tr', { className: 'hover:bg-slate-800/40' }, [
      el('td', { className: 'px-4 py-2.5 whitespace-nowrap text-slate-400', text: formatTimestampShort(entry.timestamp) }),
      el('td', { className: 'px-4 py-2.5 font-medium text-white', text: entry.username || '—' }),
      el('td', { className: 'px-4 py-2.5 font-mono text-xs text-indigo-300', text: entry.action }),
      el('td', { className: 'px-4 py-2.5 text-slate-300', text: target }),
      el('td', { className: 'px-4 py-2.5 text-slate-400 font-mono text-xs', text: entry.actorIp || '—' })
    ]);
    tbody.appendChild(tr);
  });
}

function openPasswordModal(forced = false) {
  const modal = document.getElementById('passwordModal');
  document.getElementById('mustChangeBanner').classList.toggle('hidden', !forced);
  document.getElementById('closePasswordModalBtn').classList.toggle('hidden', forced);
  document.getElementById('changePwdError').classList.add('hidden');
  document.getElementById('currentPwdInput').value = '';
  document.getElementById('newPwdInput').value = '';
  modal.classList.remove('hidden');
}

function closePasswordModal() {
  document.getElementById('passwordModal').classList.add('hidden');
}

function setKioskMode(enabled) {
  if (enabled) switchTab('display');
  document.getElementById('topHeader').classList.toggle('hidden', enabled);
  document.getElementById('exitKioskBtn').classList.toggle('hidden', !enabled);
}

function setLiveClock(enabled) {
  useLiveClock = enabled;
  const btn = document.getElementById('liveClockBtn');
  const daySelect = document.getElementById('daySelect');
  const timeSelect = document.getElementById('timeSelect');
  btn.textContent = enabled ? 'Live Clock: On' : 'Live Clock: Off';
  btn.className = enabled ? 'px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white transition' : 'px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition';
  daySelect.disabled = enabled;
  timeSelect.disabled = enabled;
  daySelect.classList.toggle('opacity-50', enabled);
  timeSelect.classList.toggle('opacity-50', enabled);
  renderDisplay();
}

function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => {});
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
  }
}

function showUploadStatus(message, isError) {
  const statusEl = document.getElementById('uploadStatus');
  statusEl.textContent = message;
  statusEl.className = `rounded-xl px-4 py-3 text-sm font-medium ${isError ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30' : 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'}`;
  statusEl.classList.remove('hidden');
}

let activePreviewToken = null;

function clearImportPreview() {
  activePreviewToken = null;
  document.getElementById('importPreviewPanel').classList.add('hidden');
}

function renderImportPreview(data) {
  activePreviewToken = data.previewToken || null;
  const panel = document.getElementById('importPreviewPanel');
  panel.classList.remove('hidden');

  document.getElementById('previewFilename').textContent = `${data.filename} (${String(data.sourceType || '').toUpperCase()})`;
  const s = data.summary || {};
  document.getElementById('previewSummaryText').textContent =
    `${s.roomsTotal || 0} rooms · ${s.classesTotal || 0} classes · ${s.errorsCount || 0} errors · ${s.warningsCount || 0} warnings`;

  const commitBtn = document.getElementById('commitImportBtn');
  commitBtn.disabled = !data.canCommit || !activePreviewToken;

  const d = data.diff || {};
  const badges = document.getElementById('previewDiffBadges');
  badges.replaceChildren(
    el('span', { className: 'px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30', text: `+${(d.roomsAdded || []).length} new rooms` }),
    el('span', { className: 'px-2.5 py-1 rounded-lg text-xs font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30', text: `~${(d.roomsUpdated || []).length} updated rooms` }),
    el('span', { className: 'px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 border border-slate-700', text: `-${(d.roomsRemoved || []).length} rooms not in file` }),
    el('span', { className: 'px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30', text: `${d.existingNoticesCount || 0} notices · ${d.existingColorOverridesCount || 0} color overrides · ${d.existingAdminClassesCount || 0} admin classes` })
  );

  const errBox = document.getElementById('previewErrorsBox');
  const errList = document.getElementById('previewErrorsList');
  errList.replaceChildren();
  if (data.errors && data.errors.length) {
    errBox.classList.remove('hidden');
    data.errors.forEach(err => {
      errList.appendChild(el('li', { text: `Row ${err.row} [${err.column}]: ${err.message}` }));
    });
  } else {
    errBox.classList.add('hidden');
  }

  const warnBox = document.getElementById('previewWarningsBox');
  const warnList = document.getElementById('previewWarningsList');
  warnList.replaceChildren();
  if (data.warnings && data.warnings.length) {
    warnBox.classList.remove('hidden');
    data.warnings.forEach(w => {
      const prefix = w.row ? `Row ${w.row} [${w.column}]: ` : `[${w.column}]: `;
      warnList.appendChild(el('li', { text: `${prefix}${w.message}` }));
    });
  } else {
    warnBox.classList.add('hidden');
  }

  document.getElementById('previewOptionsBox').classList.toggle('hidden', !data.canCommit);

  const tbody = document.getElementById('previewRowsBody');
  tbody.replaceChildren();
  (data.previewRows || []).forEach(row => {
    const clsCell = el('td', { className: 'px-3 py-2 font-medium text-slate-100' }, [
      el('span', { className: row.cancelled ? 'line-through text-rose-300' : '', text: row.title })
    ]);
    if (row.cancelled) {
      clsCell.appendChild(el('span', { className: 'ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-rose-500/20 text-rose-300', text: 'Cancelled' }));
    }
    tbody.appendChild(el('tr', { className: 'hover:bg-slate-800/40' }, [
      el('td', { className: 'px-3 py-2 text-slate-500 tabular-nums', text: String(row.rowNum) }),
      el('td', { className: 'px-3 py-2 font-medium text-white', text: row.roomName }),
      el('td', { className: 'px-3 py-2' }, [
        el('span', {
          className: 'px-2 py-0.5 rounded-full text-xs font-semibold',
          style: { backgroundColor: row.badgeBg, color: row.badgeText },
          text: row.ageGroupLabel
        })
      ]),
      el('td', { className: 'px-3 py-2 whitespace-nowrap', text: `${row.day} · ${formatTime12h(row.start)}–${formatTime12h(row.end)}` }),
      clsCell,
      el('td', { className: 'px-3 py-2 text-slate-400', text: row.leader || '—' })
    ]));
  });
}

async function handleSpreadsheetFile(file) {
  if (!file) return;
  showUploadStatus(`Validating "${file.name}" on server...`, false);
  clearImportPreview();

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await apiFetch('/api/import/preview', { method: 'POST', body: form });
    if (res.status === 401) { handleAuthExpired(); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showUploadStatus(data.error || `Could not validate "${file.name}".`, true);
      return;
    }
    renderImportPreview(data);
    if (data.canCommit) {
      showUploadStatus(`Validation passed for "${data.filename}". Review the preview below and click Publish Schedule to go live.`, false);
    } else {
      showUploadStatus(`Found ${data.errors.length} blocking error(s) in "${data.filename}". Fix them and re-upload.`, true);
    }
  } catch (err) {
    showUploadStatus(`Upload error for "${file.name}": ${err.message}`, true);
  }
}

async function commitStagedImport() {
  if (!activePreviewToken) return;
  const modeEl = document.querySelector('input[name="importMode"]:checked');
  const mode = modeEl ? modeEl.value : 'replace';
  const preserveRoomNotices = document.getElementById('optPreserveNotices').checked;
  const preserveRoomOverrides = document.getElementById('optPreserveOverrides').checked;
  const preserveAdminClasses = document.getElementById('optPreserveAdminClasses').checked;

  const btn = document.getElementById('commitImportBtn');
  btn.disabled = true;
  btn.textContent = 'Publishing...';
  try {
    const res = await apiFetch('/api/import/commit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        previewToken: activePreviewToken,
        mode,
        preserveRoomNotices,
        preserveRoomOverrides,
        preserveAdminClasses
      })
    });
    if (res.status === 401) { handleAuthExpired(); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showUploadStatus(data.error || 'Could not publish schedule.', true);
      btn.disabled = false;
      btn.textContent = 'Publish Schedule';
      return;
    }
    clearImportPreview();
    lastStateEtag = null;
    if (data.state) applyStatePayload(data.state);
    else await fetchServerState(true);
    showUploadStatus(`Published "${data.filename}" (${data.mode} mode). Previous schedule saved as rollback snapshot #${data.snapshotId}.`, false);
    fetchImportHistory();
  } catch (err) {
    showUploadStatus(`Publish error: ${err.message}`, true);
  } finally {
    btn.textContent = 'Publish Schedule';
  }
}

async function fetchImportHistory() {
  if (!isAdminUnlocked) return;
  try {
    const res = await apiFetch('/api/import/history?limit=10');
    if (!res.ok) return;
    const data = await res.json();
    renderImportHistory(data.snapshots || []);
  } catch (err) {}
}

function renderImportHistory(snapshots) {
  const tbody = document.getElementById('importHistoryBody');
  tbody.replaceChildren();
  if (!snapshots.length) {
    tbody.appendChild(el('tr', {}, [
      el('td', { colspan: '5', className: 'px-4 py-4 text-center text-slate-500', text: 'No import snapshots yet. Your first publish will automatically create one.' })
    ]));
    return;
  }
  snapshots.forEach(snap => {
    const s = snap.summary || {};
    const prevText = `${s.previousRooms ?? '—'} rooms · ${s.previousClasses ?? '—'} classes`;
    const rollbackBtn = el('button', {
      type: 'button',
      'data-rollback-snapshot': String(snap.id),
      'data-rollback-name': snap.filename,
      className: 'px-3 py-1 rounded-lg text-xs font-semibold bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30 transition',
      text: 'Restore Previous'
    });
    tbody.appendChild(el('tr', { className: 'hover:bg-slate-800/40' }, [
      el('td', { className: 'px-4 py-2.5 whitespace-nowrap text-slate-400', text: formatTimestampShort(snap.createdAt) }),
      el('td', { className: 'px-4 py-2.5 font-medium text-white', text: `#${snap.id} — ${snap.filename}` }),
      el('td', { className: 'px-4 py-2.5 text-slate-300', text: `@${snap.createdBy}` }),
      el('td', { className: 'px-4 py-2.5 text-slate-400', text: prevText }),
      el('td', { className: 'px-4 py-2.5 text-right whitespace-nowrap' }, [rollbackBtn])
    ]));
  });
}

function downloadSampleExcel() {
  window.location.href = '/api/template.xlsx';
}

function exportCurrentExcel() {
  window.location.href = '/api/export.xlsx';
}

function updateAuthUI() {
  const badge = document.getElementById('currentUserBadge');
  const pwdBtn = document.getElementById('changePwdBtn');
  const logoutBtn = document.getElementById('logoutBtn');
  if (isAdminUnlocked && currentUser) {
    badge.textContent = `${currentUser.displayName} (@${currentUser.username})`;
    badge.classList.remove('hidden');
    pwdBtn.classList.remove('hidden');
    logoutBtn.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
    pwdBtn.classList.add('hidden');
    logoutBtn.classList.add('hidden');
  }
}

function setTabClasses(tab) {
  const activeCls = 'px-3 py-1 rounded-md text-xs sm:text-sm font-medium bg-indigo-600 text-white transition';
  const idleCls = 'px-3 py-1 rounded-md text-xs sm:text-sm font-medium text-slate-300 hover:text-white transition';
  document.getElementById('tabDisplay').className = tab === 'display' ? activeCls : idleCls;
  document.getElementById('tabUpload').className  = tab === 'upload'  ? activeCls : idleCls;
  document.getElementById('tabAdmin').className   = tab === 'admin'   ? activeCls : idleCls;
}

function switchTab(tab) {
  currentTab = tab;
  if ((tab === 'upload' || tab === 'admin') && !isAdminUnlocked) {
    pendingAdminTab = tab;
    document.getElementById('viewDisplay').classList.add('hidden');
    document.getElementById('viewUpload').classList.add('hidden');
    document.getElementById('viewAdmin').classList.add('hidden');
    document.getElementById('viewLogin').classList.remove('hidden');
    document.getElementById('simulatorControls').classList.add('hidden');
    document.getElementById('loginError').classList.add('hidden');
    document.getElementById('adminPasswordInput').value = '';
    setTabClasses(tab);
    return;
  }
  document.getElementById('viewLogin').classList.add('hidden');
  document.getElementById('viewDisplay').classList.toggle('hidden', tab !== 'display');
  document.getElementById('viewUpload').classList.toggle('hidden', tab !== 'upload');
  document.getElementById('viewAdmin').classList.toggle('hidden', tab !== 'admin');
  document.getElementById('simulatorControls').classList.toggle('hidden', tab !== 'display');
  setTabClasses(tab);
  if (tab === 'upload') {
    fetchImportHistory();
  }
  if (tab === 'admin') {
    renderAdminPanel();
    switchAdminSubTab(activeAdminSubTab);
  }
}

document.getElementById('tabDisplay').addEventListener('click', () => switchTab('display'));
document.getElementById('tabUpload').addEventListener('click', () => switchTab('upload'));
document.getElementById('tabAdmin').addEventListener('click', () => switchTab('admin'));

document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('adminUsernameInput').value.trim();
  const password = document.getElementById('adminPasswordInput').value;
  const errEl = document.getElementById('loginError');
  errEl.classList.add('hidden');

  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    csrfToken = data.csrfToken || csrfToken;
    currentUser = data.user || null;
    isAdminUnlocked = true;
    updateAuthUI();
    switchTab(pendingAdminTab || 'admin');
    if (currentUser && currentUser.mustChangePassword) {
      openPasswordModal(true);
    }
  } else {
    errEl.textContent = data.error || 'Invalid username or password.';
    errEl.classList.remove('hidden');
  }
});

document.getElementById('logoutBtn').addEventListener('click', async () => {
  const res = await apiFetch('/api/logout', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (data.csrfToken) csrfToken = data.csrfToken;
  isAdminUnlocked = false;
  currentUser = null;
  updateAuthUI();
  switchTab('display');
});

document.getElementById('changePwdBtn').addEventListener('click', () => openPasswordModal(false));
document.getElementById('closePasswordModalBtn').addEventListener('click', closePasswordModal);

document.getElementById('changePasswordForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const currentPassword = document.getElementById('currentPwdInput').value;
  const newPassword = document.getElementById('newPwdInput').value;
  const errEl = document.getElementById('changePwdError');
  errEl.classList.add('hidden');

  const res = await apiFetch('/api/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ currentPassword, newPassword })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    errEl.textContent = data.error || 'Could not update password.';
    errEl.classList.remove('hidden');
    return;
  }
  if (data.csrfToken) csrfToken = data.csrfToken;
  if (data.user) currentUser = data.user;
  closePasswordModal();
  showAdminToast('Password updated successfully.');
  fetchAuditLog();
});

document.getElementById('createUserForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('newUsername').value.trim();
  const displayName = document.getElementById('newDisplayName').value.trim();
  const password = document.getElementById('newUserPassword').value;
  const role = document.getElementById('newUserRole').value;

  const res = await apiFetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, displayName, password, role, mustChangePassword: true })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not create staff account.', { isError: true });
    return;
  }
  e.target.reset();
  showAdminToast(`Created staff account @${data.user.username}.`);
  fetchAdminUsers();
  fetchAuditLog();
});

document.getElementById('adminUsersBody').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-toggle-user-active]');
  if (!btn || btn.disabled) return;
  const userId = Number(btn.getAttribute('data-toggle-user-active'));
  const isActive = btn.getAttribute('data-next-active') === '1';
  const res = await apiFetch(`/api/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ isActive })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not update staff account.', { isError: true });
    return;
  }
  showAdminToast(`${isActive ? 'Reactivated' : 'Deactivated'} @${data.user.username}.`);
  fetchAdminUsers();
  fetchAuditLog();
});

document.getElementById('refreshAuditBtn').addEventListener('click', fetchAuditLog);
document.getElementById('cancelPreviewBtn').addEventListener('click', clearImportPreview);
document.getElementById('commitImportBtn').addEventListener('click', commitStagedImport);
document.getElementById('refreshHistoryBtn').addEventListener('click', fetchImportHistory);

document.getElementById('importHistoryBody').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-rollback-snapshot]');
  if (!btn) return;
  const snapId = Number(btn.getAttribute('data-rollback-snapshot'));
  const snapName = btn.getAttribute('data-rollback-name') || `#${snapId}`;
  if (!window.confirm(`Restore the schedule to the state saved before "${snapName}" (snapshot #${snapId})?`)) return;
  const res = await apiFetch(`/api/import/rollback/${snapId}`, { method: 'POST' });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showUploadStatus(data.error || 'Rollback failed.', true);
    return;
  }
  lastStateEtag = null;
  if (data.state) applyStatePayload(data.state);
  else await fetchServerState(true);
  showUploadStatus(`Restored schedule from snapshot #${snapId} (${data.restoredRooms} rooms, ${data.restoredClasses} classes).`, false);
  fetchImportHistory();
});

document.getElementById('adminUndoBtn').addEventListener('click', async () => {
  if (!activeUndoAction) return;
  const fn = activeUndoAction;
  activeUndoAction = null;
  document.getElementById('adminToast').classList.add('hidden');
  await fn();
});

document.getElementById('roomSelect').addEventListener('change', renderDisplay);
document.getElementById('daySelect').addEventListener('change', renderDisplay);
document.getElementById('timeSelect').addEventListener('input', renderDisplay);
document.getElementById('liveClockBtn').addEventListener('click', () => setLiveClock(!useLiveClock));
document.getElementById('kioskBtn').addEventListener('click', () => setKioskMode(true));
document.getElementById('exitKioskBtn').addEventListener('click', () => setKioskMode(false));
document.getElementById('downloadSampleBtn').addEventListener('click', downloadSampleExcel);
document.getElementById('exportExcelBtn').addEventListener('click', exportCurrentExcel);
document.getElementById('adminSearch').addEventListener('input', renderAdminPanel);
document.getElementById('adminRoomFilter').addEventListener('change', renderAdminPanel);
document.getElementById('adminAgeFilter').addEventListener('change', renderAdminPanel);

document.querySelectorAll('[data-admin-subtab]').forEach(btn => {
  btn.addEventListener('click', () => switchAdminSubTab(btn.getAttribute('data-admin-subtab')));
});

// Settings: Theme & Timezone
async function saveAppSettings(changes, msg) {
  const res = await apiFetch('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes)
  });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not save settings.', { isError: true });
    return;
  }
  appSettings = data.settings || appSettings;
  lastStateEtag = null;
  renderSettingsControls();
  renderDisplay();
  fetchAuditLog();
  showAdminToast(msg);
}

document.getElementById('themeDarkBtn').addEventListener('click', () => saveAppSettings({ theme: 'dark' }, 'Door Display theme set to Dark (default).'));
document.getElementById('themeLightBtn').addEventListener('click', () => saveAppSettings({ theme: 'light' }, 'Door Display theme set to Light (warm off-white).'));
document.getElementById('churchTzSelect').addEventListener('change', (e) => saveAppSettings({ timezone: e.target.value }, `Church timezone set to ${e.target.value}.`));
document.getElementById('showLogoCheck').addEventListener('change', (e) => saveAppSettings({ showLogo: e.target.checked }, `Church logo ${e.target.checked ? 'enabled' : 'hidden'} in app header.`));

// Rooms CRUD: Create & Clear All Notices
document.getElementById('createRoomForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = document.getElementById('newRoomId').value.trim();
  const name = document.getElementById('newRoomName').value.trim();
  const defaultAgeGroupId = document.getElementById('newRoomDefaultAge').value || null;
  const note = document.getElementById('newRoomNote').value.trim();
  const res = await apiFetch('/api/rooms', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, name, defaultAgeGroupId, note })
  });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not create room.', { isError: true });
    return;
  }
  e.target.reset();
  lastStateEtag = null;
  await fetchServerState(true);
  showAdminToast(`Created room "${data.room.name}".`);
});

document.getElementById('clearNoticesBtn').addEventListener('click', async () => {
  if (!window.confirm('Clear all live Door Notices across every room?')) return;
  const res = await apiFetch('/api/rooms/clear-notices', { method: 'POST' });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showAdminToast(data.error || 'Could not clear notices.', { isError: true }); return; }
  lastStateEtag = null;
  await fetchServerState(true);
  showAdminToast(`Cleared ${data.clearedCount} Door Notice(s).`);
});

// Classes CRUD: Create
document.getElementById('createClassForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    title: document.getElementById('newClassTitle').value.trim(),
    ageGroupId: document.getElementById('newClassAgeGroup').value,
    roomId: document.getElementById('newClassRoom').value || null,
    leader: document.getElementById('newClassLeader').value.trim(),
    day: document.getElementById('newClassDay').value,
    start: document.getElementById('newClassStart').value,
    end: document.getElementById('newClassEnd').value
  };
  const res = await apiFetch('/api/classes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not add class.', { isError: true });
    return;
  }
  document.getElementById('newClassTitle').value = '';
  document.getElementById('newClassLeader').value = '';
  lastStateEtag = null;
  await fetchServerState(true);
  showAdminToast(`Added class "${data.class.title}".`);
});

// Move / Swap Modal
document.getElementById('closeSwapModalBtn').addEventListener('click', () => document.getElementById('swapModal').classList.add('hidden'));
document.querySelectorAll('input[name="swapAction"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const isExchange = document.querySelector('input[name="swapAction"]:checked').value === 'exchange';
    document.getElementById('swapTargetRoomWrap').classList.toggle('hidden', isExchange);
    document.getElementById('swapExchangeWrap').classList.toggle('hidden', !isExchange);
  });
});

document.getElementById('swapForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!activeSwapClassId) return;
  const errEl = document.getElementById('swapError');
  errEl.classList.add('hidden');
  const action = document.querySelector('input[name="swapAction"]:checked').value;
  const body = action === 'exchange'
    ? { classId: activeSwapClassId, exchangeWithClassId: Number(document.getElementById('swapExchangeClassSelect').value) }
    : { classId: activeSwapClassId, targetRoomId: document.getElementById('swapTargetRoomSelect').value || null };
  const res = await apiFetch('/api/classes/swap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    errEl.textContent = data.error || 'Could not apply room change.';
    errEl.classList.remove('hidden');
    return;
  }
  document.getElementById('swapModal').classList.add('hidden');
  lastStateEtag = null;
  await fetchServerState(true);
  showAdminToast(data.mode === 'swap' ? 'Swapped rooms for both classes.' : 'Moved class to new room.');
});

// Age Groups Manager Events
document.getElementById('agLabelInput').addEventListener('input', renderAgeGroupPreview);
document.getElementById('agColorPicker').addEventListener('input', (e) => {
  document.getElementById('agColorHex').value = e.target.value;
  renderAgeGroupPreview();
});
document.getElementById('agColorHex').addEventListener('input', (e) => {
  if (/^#[0-9a-f]{6}$/i.test(e.target.value.trim())) {
    document.getElementById('agColorPicker').value = e.target.value.trim();
  }
  renderAgeGroupPreview();
});
document.getElementById('agAutoTextCheck').addEventListener('change', renderAgeGroupPreview);
document.getElementById('agTextPicker').addEventListener('input', (e) => {
  document.getElementById('agTextHex').value = e.target.value;
  renderAgeGroupPreview();
});
document.getElementById('agTextHex').addEventListener('input', (e) => {
  if (/^#[0-9a-f]{6}$/i.test(e.target.value.trim())) {
    document.getElementById('agTextPicker').value = e.target.value.trim();
  }
  renderAgeGroupPreview();
});
document.getElementById('agPreviewThemeBtn').addEventListener('click', (e) => {
  agPreviewLight = !agPreviewLight;
  e.target.textContent = agPreviewLight ? 'Preview: Light' : 'Preview: Dark';
  renderAgeGroupPreview();
});
document.getElementById('agPaletteSwatches').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-palette-hex]');
  if (!btn) return;
  const hex = btn.getAttribute('data-palette-hex');
  document.getElementById('agColorPicker').value = hex;
  document.getElementById('agColorHex').value = hex;
  renderAgeGroupPreview();
});
document.getElementById('agResetFormBtn').addEventListener('click', resetAgeGroupForm);

document.getElementById('ageGroupForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const editId = document.getElementById('agEditId').value.trim();
  const autoText = document.getElementById('agAutoTextCheck').checked;
  const payload = {
    label: document.getElementById('agLabelInput').value.trim(),
    bannerBg: document.getElementById('agColorHex').value.trim(),
    autoText,
    aliases: document.getElementById('agAliasesInput').value,
    isDefault: document.getElementById('agIsDefaultCheck').checked
  };
  if (!autoText) payload.bannerText = document.getElementById('agTextHex').value.trim();

  const url = editId ? `/api/age-groups/${encodeURIComponent(editId)}` : '/api/age-groups';
  const method = editId ? 'PATCH' : 'POST';
  const res = await apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (res.status === 401) { handleAuthExpired(); return; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not save age group.', { isError: true });
    return;
  }
  resetAgeGroupForm();
  lastStateEtag = null;
  await fetchServerState(true);
  await fetchAgeGroupsDetailed();
  showAdminToast(`${editId ? 'Updated' : 'Created'} age group "${data.ageGroup.label}".`);
});

document.getElementById('adminAgeGroupsBody').addEventListener('click', async (e) => {
  const editBtn = e.target.closest('[data-ag-edit]');
  if (editBtn) {
    const gid = editBtn.getAttribute('data-ag-edit');
    const ag = ageGroupsMap[gid];
    if (ag) populateAgeGroupFormForEdit(ag);
    return;
  }

  const moveBtn = e.target.closest('[data-ag-move]');
  if (moveBtn && !moveBtn.disabled) {
    const gid = moveBtn.getAttribute('data-ag-move');
    const dir = Number(moveBtn.getAttribute('data-ag-dir'));
    const ids = ageGroups.map(g => g.id);
    const idx = ids.indexOf(gid);
    const target = idx + dir;
    if (idx < 0 || target < 0 || target >= ids.length) return;
    [ids[idx], ids[target]] = [ids[target], ids[idx]];
    const res = await apiFetch('/api/age-groups/reorder', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order: ids })
    });
    if (res.ok) {
      lastStateEtag = null;
      await fetchServerState(true);
      await fetchAgeGroupsDetailed();
      showAdminToast('Updated age group order.');
    }
    return;
  }

  const delBtn = e.target.closest('[data-ag-delete]');
  if (delBtn) {
    const gid = delBtn.getAttribute('data-ag-delete');
    const ag = ageGroupsMap[gid];
    if (!ag) return;
    const res = await apiFetch(`/api/age-groups/${encodeURIComponent(gid)}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.code === 'REASSIGN_REQUIRED') {
      openDeleteAgeModal(gid, data.classCount, data.roomCount);
      return;
    }
    if (!res.ok) {
      showAdminToast(data.error || 'Could not delete age group.', { isError: true });
      return;
    }
    lastStateEtag = null;
    await fetchServerState(true);
    await fetchAgeGroupsDetailed();
    showAdminToast(`Deleted age group "${ag.label}".`);
  }
});

document.getElementById('closeDeleteAgeModalBtn').addEventListener('click', () => document.getElementById('deleteAgeModal').classList.add('hidden'));
document.getElementById('cancelDeleteAgeBtn').addEventListener('click', () => document.getElementById('deleteAgeModal').classList.add('hidden'));
document.getElementById('deleteAgeForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!pendingDeleteAgeGroupId) return;
  const reassignTo = document.getElementById('deleteAgeReassignSelect').value;
  const res = await apiFetch(`/api/age-groups/${encodeURIComponent(pendingDeleteAgeGroupId)}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reassignTo })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showAdminToast(data.error || 'Could not reassign and delete age group.', { isError: true });
    return;
  }
  document.getElementById('deleteAgeModal').classList.add('hidden');
  pendingDeleteAgeGroupId = null;
  lastStateEtag = null;
  await fetchServerState(true);
  await fetchAgeGroupsDetailed();
  showAdminToast(`Reassigned ${data.reassignedClasses} class(es) and deleted age group.`);
});

document.getElementById('adminRoomsGrid').addEventListener('change', async (e) => {
  const roomId = e.target.getAttribute('data-age-room');
  if (roomId && roomsMap[roomId]) {
    const prevOverride = roomsMap[roomId].overrideAgeGroupId;
    const nextOverride = e.target.value || null;
    const updated = await patchRoom(roomId, { overrideAgeGroupId: nextOverride });
    if (updated) {
      renderAdminPanel();
      renderScheduleTable();
      renderDisplay();
      fetchAuditLog();
      showAdminToast(`Updated color override for ${updated.name}.`, {
        onUndo: async () => {
          const reverted = await patchRoom(roomId, { overrideAgeGroupId: prevOverride });
          if (reverted) { renderAdminPanel(); renderScheduleTable(); renderDisplay(); fetchAuditLog(); }
        }
      });
    }
    return;
  }
  const nameRoomId = e.target.getAttribute('data-name-room');
  if (nameRoomId && roomsMap[nameRoomId]) {
    const val = e.target.value.trim();
    if (val) {
      flushDebounced(`room-name:${nameRoomId}`, async () => {
        const updated = await patchRoom(nameRoomId, { name: val });
        if (updated) { populateSelectors(); renderScheduleTable(); renderDisplay(); }
      });
    }
    return;
  }
  const noteRoomId = e.target.getAttribute('data-note-room');
  if (noteRoomId && roomsMap[noteRoomId]) {
    const val = e.target.value;
    flushDebounced(`room-note:${noteRoomId}`, async () => {
      const updated = await patchRoom(noteRoomId, { note: val });
      if (updated) { renderDisplay(); fetchAuditLog(); }
    });
  }
});

document.getElementById('adminRoomsGrid').addEventListener('input', (e) => {
  const nameRoomId = e.target.getAttribute('data-name-room');
  if (nameRoomId && roomsMap[nameRoomId]) {
    const val = e.target.value;
    roomsMap[nameRoomId].name = val;
    renderDisplay();
    scheduleDebounced(`room-name:${nameRoomId}`, async () => {
      if (!val.trim()) return;
      const updated = await patchRoom(nameRoomId, { name: val.trim() });
      if (updated) { populateSelectors(); renderScheduleTable(); renderDisplay(); }
    });
    return;
  }
  const roomId = e.target.getAttribute('data-note-room');
  if (roomId && roomsMap[roomId]) {
    const val = e.target.value;
    roomsMap[roomId].note = val;
    renderDisplay();
    scheduleDebounced(`room-note:${roomId}`, async () => {
      const updated = await patchRoom(roomId, { note: val });
      if (updated) { renderDisplay(); fetchAuditLog(); }
    });
  }
});

document.getElementById('adminRoomsGrid').addEventListener('click', async (e) => {
  const previewBtn = e.target.closest('[data-preview-room]');
  if (previewBtn) {
    document.getElementById('roomSelect').value = previewBtn.getAttribute('data-preview-room');
    renderDisplay();
    switchTab('display');
    return;
  }
  const copyBtn = e.target.closest('[data-copy-link]');
  if (copyBtn) {
    const roomId = copyBtn.getAttribute('data-copy-link');
    const base = window.location.href.split('?')[0];
    copyTextToClipboard(`${base}?room=${encodeURIComponent(roomId)}&kiosk=1`);
    showAdminToast(`Copied iPad kiosk link for Room ${roomId}: ?room=${encodeURIComponent(roomId)}&kiosk=1`);
    return;
  }
  const delBtn = e.target.closest('[data-delete-room]');
  if (delBtn) {
    const roomId = delBtn.getAttribute('data-delete-room');
    const room = roomsMap[roomId];
    if (!room) return;
    if (!window.confirm(`Delete "${room.name}"? Any classes assigned to this room will be preserved as Unassigned.`)) return;
    const res = await apiFetch(`/api/rooms/${encodeURIComponent(roomId)}`, { method: 'DELETE' });
    if (res.status === 401) { handleAuthExpired(); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showAdminToast(data.error || 'Could not delete room.', { isError: true }); return; }
    lastStateEtag = null;
    await fetchServerState(true);
    showAdminToast(`Deleted room "${room.name}" (${data.unassignedClassCount} class(es) moved to Unassigned).`);
  }
});

document.getElementById('adminClassesBody').addEventListener('click', async (e) => {
  const swapBtn = e.target.closest('[data-open-swap]');
  if (swapBtn) {
    openSwapModal(Number(swapBtn.getAttribute('data-open-swap')));
    return;
  }
  const delBtn = e.target.closest('[data-delete-class]');
  if (delBtn) {
    const classId = Number(delBtn.getAttribute('data-delete-class'));
    const item = scheduleList.find(c => c.id === classId);
    if (!item) return;
    if (!window.confirm(`Delete class "${item.title}"?`)) return;
    const res = await apiFetch(`/api/classes/${classId}`, { method: 'DELETE' });
    if (res.status === 401) { handleAuthExpired(); return; }
    if (!res.ok) { showAdminToast('Could not delete class.', { isError: true }); return; }
    lastStateEtag = null;
    await fetchServerState(true);
    showAdminToast(`Deleted class "${item.title}".`);
    return;
  }
  const btn = e.target.closest('[data-toggle-cancel]');
  if (!btn) return;
  const classId = Number(btn.getAttribute('data-toggle-cancel'));
  const item = scheduleList.find(c => c.id === classId);
  if (!item) return;
  const prevCancelled = item.cancelled;
  const nextCancelled = !prevCancelled;
  const updated = await patchClass(classId, { cancelled: nextCancelled });
  if (updated) {
    renderAdminPanel();
    renderDisplay();
    fetchAuditLog();
    showAdminToast(`${nextCancelled ? 'Cancelled' : 'Restored'} "${updated.title}".`, {
      onUndo: async () => {
        const reverted = await patchClass(classId, { cancelled: prevCancelled });
        if (reverted) { renderAdminPanel(); renderDisplay(); fetchAuditLog(); }
      }
    });
  }
});

document.getElementById('adminClassesBody').addEventListener('change', async (e) => {
  const roomEditId = e.target.getAttribute('data-edit-room');
  if (roomEditId) {
    const classId = Number(roomEditId);
    const item = scheduleList.find(c => c.id === classId);
    if (!item) return;
    const prevRoom = item.room;
    const nextRoom = e.target.value || null;
    const updated = await patchClass(classId, { roomId: nextRoom });
    if (updated) {
      renderAdminPanel();
      renderScheduleTable();
      renderDisplay();
      showAdminToast(`Moved "${updated.title}" to ${nextRoom ? `Room ${nextRoom}` : 'Unassigned'}.`, {
        onUndo: async () => {
          const reverted = await patchClass(classId, { roomId: prevRoom });
          if (reverted) { renderAdminPanel(); renderScheduleTable(); renderDisplay(); }
        }
      });
    }
    return;
  }
  const ageId = e.target.getAttribute('data-edit-age');
  if (ageId) {
    const classId = Number(ageId);
    const item = scheduleList.find(c => c.id === classId);
    if (!item) return;
    const prevAge = item.ageGroupId;
    const nextAge = e.target.value;
    const updated = await patchClass(classId, { ageGroupId: nextAge });
    if (updated) {
      renderScheduleTable();
      renderDisplay();
      fetchAuditLog();
      showAdminToast(`Updated age group for "${updated.title}".`, {
        onUndo: async () => {
          const reverted = await patchClass(classId, { ageGroupId: prevAge });
          if (reverted) { renderAdminPanel(); renderScheduleTable(); renderDisplay(); fetchAuditLog(); }
        }
      });
    }
    return;
  }
  const titleId = e.target.getAttribute('data-edit-title');
  if (titleId) {
    const classId = Number(titleId);
    const val = e.target.value;
    flushDebounced(`class-title:${classId}`, async () => {
      const updated = await patchClass(classId, { title: val });
      if (updated) { renderScheduleTable(); renderDisplay(); fetchAuditLog(); }
    });
    return;
  }
  const leaderId = e.target.getAttribute('data-edit-leader');
  if (leaderId) {
    const classId = Number(leaderId);
    const val = e.target.value;
    flushDebounced(`class-leader:${classId}`, async () => {
      const updated = await patchClass(classId, { leader: val });
      if (updated) { renderScheduleTable(); renderDisplay(); fetchAuditLog(); }
    });
  }
});

document.getElementById('adminClassesBody').addEventListener('input', (e) => {
  const titleId = e.target.getAttribute('data-edit-title');
  if (titleId) {
    const classId = Number(titleId);
    const item = scheduleList.find(c => c.id === classId);
    if (item) {
      const val = e.target.value;
      item.title = val;
      renderScheduleTable();
      renderDisplay();
      scheduleDebounced(`class-title:${classId}`, async () => {
        const updated = await patchClass(classId, { title: val });
        if (updated) { renderScheduleTable(); renderDisplay(); fetchAuditLog(); }
      });
    }
  }
  const leaderId = e.target.getAttribute('data-edit-leader');
  if (leaderId) {
    const classId = Number(leaderId);
    const item = scheduleList.find(c => c.id === classId);
    if (item) {
      const val = e.target.value;
      item.leader = val;
      renderScheduleTable();
      renderDisplay();
      scheduleDebounced(`class-leader:${classId}`, async () => {
        const updated = await patchClass(classId, { leader: val });
        if (updated) { renderScheduleTable(); renderDisplay(); fetchAuditLog(); }
      });
    }
  }
});

document.getElementById('fileInput').addEventListener('change', (e) => { handleSpreadsheetFile(e.target.files[0]); e.target.value = ''; });
const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('border-indigo-500'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('border-indigo-500'));
dropZone.addEventListener('drop', (e) => { e.preventDefault(); dropZone.classList.remove('border-indigo-500'); handleSpreadsheetFile(e.dataTransfer.files[0]); });

(async function init() {
  await Promise.all([checkAuthStatus(), fetchServerState(true)]);
  const params = new URLSearchParams(window.location.search);
  if (params.get('kiosk') === '1') {
    setLiveClock(true);
    setKioskMode(true);
  }
  // Tick live clock & countdowns every 5 seconds on the iPad without hitting the network
  setInterval(() => {
    if (currentTab === 'display' && useLiveClock) renderDisplay();
  }, 5000);
  // Poll server every 15 seconds using ETag (304 Not Modified when unchanged)
  setInterval(() => {
    if (currentTab === 'display') fetchServerState(false);
  }, 15000);
})();
  