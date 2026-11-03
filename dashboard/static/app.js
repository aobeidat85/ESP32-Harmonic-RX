'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = null;
let unit = 'F';
try { unit = localStorage.getItem('vevor-unit') === 'C' ? 'C' : 'F'; } catch {}
let series = 'temperature';
let currentView = 'overview';
let requestPending = false;
let tableKey = '';
let historyKey = '';
let traceKey = '';
let toastTimer;

function number(value, digits = 1) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, {minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
}
function timeLabel(value, seconds = true) {
  if (!value) return 'Time not recorded';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', ...(seconds ? {second:'2-digit'} : {})}) : 'Time not recorded';
}
function dateLabel(value) {
  return value ? new Date(value).toLocaleDateString([], {month:'short', day:'numeric'}) : 'Saved experiment';
}
function ageLabel(value) {
  if (!value) return 'Saved experiment';
  const age = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (age < 5) return 'Just received';
  if (age < 60) return `${Math.floor(age)}s ago`;
  if (age < 3600) return `${Math.floor(age / 60)}m ago`;
  if (age < 86400) return `${Math.floor(age / 3600)}h ago`;
  return dateLabel(value);
}
function showToast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').hidden = true, 4500);
}
function setView(view) {
  currentView = view;
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== `view-${view}`);
  document.querySelectorAll('[data-view]').forEach(el => {
    const active = el.dataset.view === view;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  const copy = {
    overview:['Your outdoor station.', 'Weather packets, decoded directly from your ESP32.'],
    packets:['Every packet, accounted for.', 'Inspect accepted readings and export the weather log.'],
    receiver:['Connected to the outdoors.', 'USB receiver settings, validation, and connection activity.']
  }[view];
  $('pageTitle').textContent = copy[0]; $('pageSubtitle').textContent = copy[1];
  window.scrollTo({top:0, behavior:'auto'});
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
$('openReceiver').addEventListener('click', () => setView('receiver'));

function setUnit(value) {
  unit = value;
  try { localStorage.setItem('vevor-unit', unit); } catch {}
  document.querySelectorAll('[data-unit]').forEach(button => {
    const active = button.dataset.unit === unit;
    button.classList.toggle('selected', active); button.setAttribute('aria-pressed', String(active));
  });
  historyKey = ''; tableKey = '';
  if (state) render(state);
}
document.querySelectorAll('[data-unit]').forEach(button => button.addEventListener('click', () => setUnit(button.dataset.unit)));
document.querySelectorAll('[data-series]').forEach(button => button.addEventListener('click', () => {
  series = button.dataset.series;
  document.querySelectorAll('[data-series]').forEach(el => { el.classList.toggle('selected', el === button); el.setAttribute('aria-pressed', String(el === button)); });
  historyKey = ''; if (state) renderHistory(state.readings);
}));
$('packetFilter').addEventListener('change', () => { tableKey = ''; if (state) renderPackets(state.readings); });
$('receiverToggle').addEventListener('click', async () => {
  if (!state || requestPending) return;
  requestPending = true;
  $('receiverToggle').disabled = true;
  const action = state.running ? 'stop' : 'start';
  try {
    const response = await fetch(`/api/${action}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not update the receiver.');
    await poll();
  } catch (error) { showToast(error.message); }
  finally { requestPending = false; $('receiverToggle').disabled = state?.phase === 'stopping'; }
});

function chart(points, {height=165, id='chart', xLabel, yLabel, color='#13877d', minY, maxY, label='Chart'} = {}) {
  if (!points.length) return '<div class="chart-empty">Start receiving to build a reading history.</div>';
  const width = 760, pad = {left:42, right:14, top:12, bottom:29};
  const xMin = Math.min(...points.map(p => p[0])), xMax = Math.max(...points.map(p => p[0]));
  const yValues = points.map(p => p[1]);
  let lo = minY ?? Math.min(...yValues), hi = maxY ?? Math.max(...yValues);
  if (hi - lo < 1) { lo -= .5; hi += .5; } else if (minY == null) { const margin = (hi - lo) * .14; lo -= margin; hi += margin; }
  const x = value => pad.left + (xMax === xMin ? .5 : (value - xMin) / (xMax - xMin)) * (width - pad.left - pad.right);
  const y = value => pad.top + (hi - value) / (hi - lo) * (height - pad.top - pad.bottom);
  const bottom = height - pad.bottom;
  const line = points.map((p,i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join(' ');
  const area = `${line} L${x(points.at(-1)[0]).toFixed(2)},${bottom} L${x(points[0][0]).toFixed(2)},${bottom} Z`;
  const grid = [0, .5, 1].map(frac => {
    const val = lo + (hi-lo)*frac, pos = y(val);
    return `<line x1="${pad.left}" y1="${pos}" x2="${width-pad.right}" y2="${pos}" stroke="#edf1ec" stroke-width="1"/><text x="${pad.left-10}" y="${pos+3}" text-anchor="end" fill="#9ba99e" font-size="9" font-family="Segoe UI,Arial">${escapeHtml(yLabel ? yLabel(val) : number(val))}</text>`;
  }).join('');
  const xTicks = [0, .5, 1].map(frac => {
    const val = xMin + (xMax-xMin)*frac;
    return `<text x="${x(val)}" y="${height-6}" text-anchor="${frac === 0 ? 'start' : frac === 1 ? 'end' : 'middle'}" fill="#9ba99e" font-size="9" font-family="Segoe UI,Arial">${escapeHtml(xLabel ? xLabel(val) : number(val,0))}</text>`;
  }).join('');
  const dots = points.length <= 40 ? points.map(p => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="3.1" fill="white" stroke="${color}" stroke-width="1.7"/>`).join('') : '';
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(label)}"><defs><linearGradient id="${id}-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${color}" stop-opacity=".10"/><stop offset="100%" stop-color="${color}" stop-opacity=".01"/></linearGradient></defs>${grid}<path d="${area}" fill="url(#${id}-fill)"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>${dots}${xTicks}</svg>`;
}
function renderHistory(readings) {
  const key = `${readings.length}-${readings.at(-1)?.received_at}-${unit}-${series}`;
  if (key === historyKey) return;
  historyKey = key;
  const metric = series === 'humidity' ? 'humidity_percent' : `temperature_${unit}`;
  const usable = readings.slice(-40).filter(r => typeof r[metric] === 'number');
  const dated = usable.every(r => r.received_at);
  const points = usable.map((r,i) => [dated ? new Date(r.received_at).getTime() : i, r[metric]]);
  $('historyChart').innerHTML = chart(points, {id:'history', height:175,
    xLabel: val => dated ? timeLabel(new Date(val).toISOString(), false) : 'Saved',
    yLabel: val => series === 'humidity' ? `${number(val,0)}%` : `${number(val,1)}°`,
    label: `${series === 'humidity' ? 'Humidity' : 'Temperature'} history from ${points.length} accepted packets`,
    color: series === 'humidity' ? '#7b9fae' : '#13877d'});
  $('historyLegend').textContent = series === 'humidity' ? 'Relative humidity · %' : `Temperature · °${unit}`;
  $('historyCount').textContent = `${readings.length} ${readings.length === 1 ? 'reading' : 'readings'}`;
}
function renderTrace(trace) {
  if (!trace) { $('signalChart').innerHTML = '<div class="chart-empty">No signal capture available yet.</div>'; return; }
  const key = `${trace.source}-${trace.received_at}-${trace.observations}-${trace.valid_packets}`;
  if (key === traceKey) return;
  traceKey = key;
  $('signalChart').innerHTML = chart(trace.points.map(p => [p[0], p[1]]), {id:'signal', height:150,
    xLabel: val => `${number(val,0)} ms`, yLabel: val => number(val,0), label:'Measured two-tone energy in the last capture window'});
  $('traceBadge').textContent = trace.source === 'saved' ? 'Saved capture' : trace.valid_packets ? 'Packet decoded' : 'No packet decoded';
  $('traceBadge').className = `badge ${trace.source === 'saved' ? 'saved' : trace.valid_packets ? 'live' : ''}`;
  $('signalDetails').textContent = `${number(trace.observations,0)} measurements · ${number(trace.spacing_us,0)} µs median spacing`;
}
function renderPackets(readings) {
  const filter = $('packetFilter').value;
  const key = `${readings.length}-${readings.at(-1)?.received_at}-${unit}-${filter}`;
  if (key === tableKey) return;
  tableKey = key;
  const visible = readings.filter(r => filter === 'all' || r.source === filter).slice().reverse();
  $('packetEmpty').hidden = visible.length !== 0;
  $('packetRows').innerHTML = visible.map(r => `<tr><td>${escapeHtml(timeLabel(r.received_at))}<span class="time-date">${escapeHtml(dateLabel(r.received_at))}</span></td><td><span class="badge ${r.source === 'live' ? 'live' : 'saved'}">${r.source === 'live' ? 'Live' : 'Saved'}</span></td><td><span class="mono">${escapeHtml(r.sensor_id)}</span></td><td>${number(r[`temperature_${unit}`])} °${unit}</td><td>${number(r.humidity_percent,0)}%</td><td><span class="checks-pass"><svg><use href="#i-check"/></svg>Checks passed</span></td><td><details class="packet-details"><summary>Inspect</summary><pre>Payload: ${escapeHtml(r.payload_hex)}\nCounter: ${escapeHtml(r.counter)}\nSync errors: ${escapeHtml(r.preamble_bit_errors ?? '—')}/40\nChecksum: passed\nCounter relationship: passed</pre></details></td></tr>`).join('');
}
function eventHtml(event) {
  return `<div class="activity-event"><span class="small-dot ${escapeHtml(event.kind)}"></span><span>${escapeHtml(event.message)}</span><time class="event-time">${escapeHtml(timeLabel(event.time,false))}</time></div>`;
}
function updateCountdown() {
  if (!state) return;
  if (state.phase === 'waiting' && state.next_capture_at) {
    const remaining = Math.max(0, Math.ceil((new Date(state.next_capture_at) - Date.now()) / 1000));
    $('nextLabel').textContent = remaining > 0 ? `Next capture in ${remaining}s` : 'Next capture any moment';
  } else {
    $('nextLabel').textContent = ({idle:'Start reception for new readings', connecting:'Connecting to USB…',
      reconnecting:'Resetting and reconnecting the USB receiver…', searching:'Searching for a sensor burst…', capturing:'Capturing and validating…', stopping:'Releasing the USB port…',
      error:'Check connection and try again'})[state.phase] || state.message;
  }
}
function render(value) {
  state = value;
  const last = value.readings.at(-1);
  const port = value.config.port || 'Not selected';
  $('sidePort').textContent = port + ' / USB';
  $('stripPort').textContent = port;
  $('serialPort').textContent = port;
  $('serialSensor').textContent = value.config.sensor_id || 'Any';
  $('sensorHeading').textContent = last?.sensor_id || value.config.sensor_id || 'ANY';
  $('stationChip').textContent = 'Sensor ' + (last?.sensor_id || '-');
  const fresh = value.running && last?.source === 'live' && last.received_at && Date.now() - new Date(last.received_at).getTime() < 60000;
  $('temperature').textContent = last ? number(last[`temperature_${unit}`]) : '—';
  $('temperatureUnit').textContent = `°${unit}`;
  $('humidity').textContent = last ? number(last.humidity_percent,0) : '—';
  $('humidityFill').style.width = last ? `${Math.max(0, Math.min(100,last.humidity_percent))}%` : '0%';
  $('wind').textContent = last ? number(last.wind_avg_km_h) : '—';
  $('gust').textContent = last ? `${number(last.wind_gust_km_h)} km/h` : '—';
  const degrees = last?.wind_direction_degrees;
  const compass = typeof degrees === 'number' ? ['N','NE','E','SE','S','SW','W','NW'][Math.round(degrees / 45) % 8] : '—';
  $('direction').textContent = typeof degrees === 'number' ? `${compass} ${degrees}°` : '—';
  $('windArrow').style.transform = `rotate(${degrees ?? 0}deg)`;
  $('rain').innerHTML = last ? `${number(last.total_rain_mm)} <small>mm</small>` : '—';
  $('uv').textContent = last ? number(last.uv_index,0) : '—';
  $('light').innerHTML = last ? `${number(last.light_lux,0)} <small>lx</small>` : '—';
  $('battery').textContent = !last ? '—' : last.battery_ok ? 'Normal' : 'Low';
  $('readingBadge').textContent = !last ? 'No reading' : last.source === 'saved' ? 'Saved reading' : fresh ? 'Live reading' : 'Last received';
  $('readingBadge').className = `badge ${fresh ? 'live' : 'saved'}`;
  $('readingAge').textContent = !last ? 'Start receiving to get weather data' : `${last.source === 'saved' ? 'Saved capture' : 'Received'} · ${ageLabel(last.received_at)}`;
  const connected = value.connected && value.running;
  const labels = {idle:'Receiver stopped', connecting:'Connecting', reconnecting:'Reconnecting USB', waiting:'Receiver connected', capturing:'Capturing', searching:'Searching', stopping:'Stopping', error:'Connection error'};
  $('connectionLabel').textContent = labels[value.phase] || value.phase;
  $('sideStatus').textContent = connected ? 'Connected' : ['connecting','reconnecting'].includes(value.phase) ? 'Connecting…' : 'Not connected';
  $('receiverStateBadge').textContent = labels[value.phase] || value.phase;
  $('receiverStateBadge').className = `badge ${connected ? 'live' : value.error ? 'error' : ''}`;
  ['sideDot','connectionDot','activityDot'].forEach(id => $(id).classList.toggle('online', connected));
  $('errorBanner').hidden = !value.error;
  $('errorBanner').textContent = value.error ? `Receiver connection: ${value.error}. Close other serial monitors or reconnect the board, then try Start receiving. For multiple USB boards, launch with --serial-port.` : '';
  $('receiverToggle').classList.toggle('stop', value.running);
  $('receiverToggle').innerHTML = `<svg><use href="#${value.running ? 'i-stop' : 'i-play'}"/></svg><span>${value.running ? 'Stop receiving' : 'Start receiving'}</span>`;
  $('receiverToggle').disabled = requestPending || value.phase === 'stopping';
  $('sessionAccepted').textContent = number(value.stats.accepted,0);
  $('sessionWindows').textContent = number(value.stats.windows,0);
  $('navCount').textContent = number(value.readings.length,0);
  $('activityList').innerHTML = value.events.slice(0,2).map(eventHtml).join('');
  $('receiverEvents').innerHTML = value.events.slice(0,15).map(eventHtml).join('') || '<div class="empty-state">No connection events yet.</div>';
  renderHistory(value.readings); renderPackets(value.readings); renderTrace(value.trace); updateCountdown();
}
async function poll() {
  try {
    const response = await fetch('/api/status', {cache:'no-store'});
    if (!response.ok) throw new Error('Dashboard server is not responding.');
    render(await response.json());
  } catch (error) {
    $('connectionLabel').textContent = 'Dashboard offline';
    $('errorBanner').hidden = false;
    $('errorBanner').textContent = 'The local dashboard server is unavailable. Restart it using Start Dashboard.ps1.';
    $('receiverToggle').disabled = true;
  }
}
setUnit(unit);
setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); updateCountdown(); }, 250);
poll();
setInterval(poll, 1200);
