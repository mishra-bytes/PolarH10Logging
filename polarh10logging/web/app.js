/* PolarH10 Logging - renderer. Polls the Python bridge and draws the UI. */
'use strict';

const POLL_MS = 250;

/* ------------------------------------------------------------------ icons */
const S = (body, o = {}) =>
  `<svg width="${o.w || 16}" height="${o.h || o.w || 16}" viewBox="0 0 24 24" fill="${o.fill || 'none'}"` +
  ` stroke="${o.stroke || 'currentColor'}" stroke-width="1.75" stroke-linecap="round"` +
  ` stroke-linejoin="round" aria-hidden="true" style="display:block;flex-shrink:0">${body}</svg>`;

const ICONS = {
  strap: o => S('<rect x="5" y="8" width="14" height="8" rx="4"></rect><path d="M1.5 12H5M19 12h3.5"></path><circle cx="12" cy="12" r="1.1"></circle>', o),
  heart: o => S('<path d="M12 20.5C7 16.9 3.5 13.9 3.5 10.2 3.5 7.3 5.8 5 8.6 5c1.7 0 3 .8 3.4 1.9C12.4 5.8 13.7 5 15.4 5c2.8 0 5.1 2.3 5.1 5.2 0 3.7-3.5 6.7-8.5 10.3z"></path>', o),
  pulse: o => S('<path d="M3 12h3.5l2.5-6 4.5 12 2.5-6H21"></path>', o),
  ecg: o => S('<path d="M2 13h5l1.2-2.5 1.3 2.5h1.3L12.5 4l2.2 15 1.3-6H22"></path>', o),
  motion: o => S('<path d="M12 12.5V4M12 12.5l7.4 4.2M12 12.5l-7.4 4.2"></path><path d="M9.6 6.3L12 4l2.4 2.3"></path>', o),
  settings: o => S('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"></path><circle cx="15" cy="7" r="2"></circle><circle cx="9" cy="17" r="2"></circle>', o),
  unlink: o => S('<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"></path><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"></path><path d="M8 3.5v2.5M3.5 8H6M16 20.5V18M20.5 16H18"></path>', o),
  link: o => S('<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"></path><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"></path>', o),
  search: o => S('<circle cx="11" cy="11" r="6.5"></circle><path d="M16 16l4.5 4.5"></path>', o),
  refresh: o => S('<path d="M20 12a8 8 0 1 1-2.34-5.66"></path><path d="M20 4.5V9h-4.5"></path>', o),
  check: o => S('<circle cx="12" cy="12" r="9"></circle><path d="M8 12.3l2.8 2.8 5.2-5.6"></path>', o),
  tick: o => S('<path d="M5 12.5l4.5 4.5L19 7.5"></path>', o),
  info: o => S('<circle cx="12" cy="12" r="9"></circle><path d="M12 11v5.5"></path><path d="M12 7.8h.01"></path>', o),
  warn: o => S('<path d="M10.3 4.3a2 2 0 0 1 3.4 0l7.6 13.2a2 2 0 0 1-1.7 3H4.4a2 2 0 0 1-1.7-3z"></path><path d="M12 9.5v4"></path><path d="M12 17h.01"></path>', o),
  cross: o => S('<circle cx="12" cy="12" r="9"></circle><path d="M9.2 9.2l5.6 5.6M14.8 9.2l-5.6 5.6"></path>', o),
  clock: o => S('<circle cx="12" cy="12" r="9"></circle><path d="M12 7.5V12l3 2"></path>', o),
  battery: (o = {}) => `<svg width="22" height="18" viewBox="0 0 26 24" fill="none" aria-hidden="true" style="display:block;flex-shrink:0">` +
    `<rect x="2" y="7" width="19" height="10" rx="2.5" stroke="${o.stroke || 'currentColor'}" stroke-width="1.6"></rect>` +
    `<path d="M23.6 10.6v2.8" stroke="${o.stroke || 'currentColor'}" stroke-width="1.8" stroke-linecap="round"></path>` +
    `<rect x="4.7" y="9.7" width="${Math.max(0.6, (o.pct || 0) / 100 * 13.6)}" height="4.6" rx="1" fill="${o.stroke || 'currentColor'}"></rect></svg>`,
  batteryPlain: o => S('<rect x="2.5" y="7.5" width="16.5" height="9" rx="2"></rect><path d="M21.5 10.8v2.4"></path><path d="M6 10.5v3M9 10.5v3M12 10.5v3"></path>', o),
  file: o => S('<path d="M6.5 3.5h7.5l4 4v13h-11.5z"></path><path d="M14 3.5v4h4"></path>', o),
  folder: o => S('<path d="M3.5 6.5h5.2l1.8 2.2h10v11.3h-17z"></path>', o),
  lock: o => S('<rect x="5" y="10.5" width="14" height="10" rx="2"></rect><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"></path>', o),
  stop: o => S('<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"></rect>', o),
  record: o => S('<circle cx="12" cy="12" r="6"></circle>', o),
  chevron: o => S('<path d="M6 14.5l6-6 6 6"></path>', o),
  chevronDown: o => S('<path d="M6 9.5l6 6 6-6"></path>', o),
  drop: o => S('<path d="M12 3.5s6 6.4 6 10.6a6 6 0 0 1-12 0C6 9.9 12 3.5 12 3.5z"></path>', o),
  phone: o => S('<rect x="7" y="3" width="10" height="18" rx="2"></rect><path d="M11 18h2"></path><path d="M3.5 3.5l17 17"></path>', o),
  bluetooth: o => S('<path d="M7 7.5l10 9-5 4.5V3l5 4.5-10 9"></path>', o),
  spinner: (o = {}) => `<svg class="spin" width="${o.w || 16}" height="${o.w || 16}" viewBox="0 0 24 24" fill="none" aria-hidden="true" style="display:block;flex-shrink:0">` +
    `<circle cx="12" cy="12" r="9" stroke="${o.stroke || 'currentColor'}" stroke-opacity="0.25" stroke-width="2.6"></circle>` +
    `<path d="M21 12a9 9 0 0 0-9-9" stroke="${o.stroke || 'currentColor'}" stroke-width="2.6" stroke-linecap="round"></path></svg>`,
  bars: (level, o = {}) => {
    const on = o.on || '#40464e', off = o.off || '#c9ced5';
    let r = `<svg width="18" height="16" viewBox="0 0 18 16" aria-hidden="true" style="display:block;flex-shrink:0">`;
    for (let i = 0; i < 4; i++) {
      r += `<rect x="${1 + i * 4.3}" y="${11 - i * 3.3}" width="3" height="${4 + i * 3.3}" rx="1" fill="${i < level ? on : off}"></rect>`;
    }
    return r + '</svg>';
  },
};

/* ------------------------------------------------------------------ utils */
const $ = sel => document.querySelector(sel);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pad = n => String(Math.floor(n)).padStart(2, '0');

function hms(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${pad(sec / 3600)}:${pad((sec % 3600) / 60)}:${pad(sec % 60)}`;
}
function mmss(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${Math.floor(sec / 60)}:${pad(sec % 60)}`;
}
function megabytes(bytes) {
  if (!bytes) return '0 MB';
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return `${Math.round(bytes / 1024)} kB`;
  return `${mb < 100 ? mb.toFixed(1) : Math.round(mb)} MB`;
}
function signal(rssi) {
  if (rssi == null) return { level: 0, label: 'Not seen yet' };
  if (rssi >= -60) return { level: 4, label: 'Strong' };
  if (rssi >= -72) return { level: 3, label: 'Good' };
  if (rssi >= -82) return { level: 2, label: 'Fair' };
  return { level: 1, label: 'Weak' };
}
function num(v, digits = 1) {
  return v == null ? '--' : Number(v).toFixed(digits);
}

/* ------------------------------------------------------------------ state */
const ui = {
  screen: 'connect',
  tab: 'hr',
  eventsOpen: true,
  confirmStop: false,
  pidError: '',
  form: { participant: '', condition: '', notes: '' },
  recMode: null,
  connectSig: null,
  settingsDirty: false,
  toastTimer: null,
};
let api = null;
let last = null;

/* ------------------------------------------------------------------ top bar */
function renderTop(s) {
  const connected = s.state === 'connected';
  const reconnecting = s.state === 'reconnecting';
  const connecting = s.state === 'connecting';
  const dev = s.device;

  $('#top-avatar').innerHTML = ICONS.strap({ w: 18, stroke: dev ? 'var(--ink-2)' : 'var(--disabled-ink)' });
  $('#top-name').textContent = dev ? 'Polar H10' : 'No strap connected';
  $('#top-name').style.color = dev ? '' : 'var(--ink-2)';
  $('#top-id').textContent = dev ? dev.deviceId : '';

  const status = $('#top-status');
  if (connected) {
    status.className = 'pill ok';
    status.innerHTML = '<span class="dot"></span>Connected';
  } else if (reconnecting) {
    status.className = 'pill warn';
    status.innerHTML = ICONS.spinner({ w: 14, stroke: 'var(--warn-icon)' }) +
      `Reconnecting &middot; ${mmss(s.reconnectLeftS)}`;
  } else if (connecting) {
    status.className = 'pill info';
    status.innerHTML = ICONS.spinner({ w: 14, stroke: 'var(--info-ink)' }) + 'Connecting&hellip;';
  } else if (s.connectError) {
    status.className = 'pill err';
    status.innerHTML = ICONS.cross({ w: 14, stroke: 'var(--err-ink)' }) + 'Connect failed';
  } else if (s.scanError) {
    status.className = 'pill';
    status.innerHTML = ICONS.bluetooth({ w: 14 }) + 'Bluetooth off';
  } else if (s.scanning) {
    status.className = 'pill info';
    status.innerHTML = ICONS.spinner({ w: 14, stroke: 'var(--info-ink)' }) + 'Searching&hellip;';
  } else if (s.devices.length) {
    status.className = 'pill info';
    status.innerHTML = ICONS.strap({ w: 14, stroke: 'var(--info-ink)' }) +
      `${s.devices.length} strap${s.devices.length === 1 ? '' : 's'} found`;
  } else {
    status.className = 'pill';
    status.innerHTML = ICONS.strap({ w: 14 }) + 'Not connected';
  }

  const live = connected || reconnecting;
  const bat = $('#top-battery');
  bat.hidden = s.battery == null;
  if (s.battery != null) {
    bat.style.color = connected ? 'var(--ink-2)' : 'var(--muted)';
    bat.innerHTML = ICONS.battery({ pct: s.battery, stroke: 'currentColor' }) +
      `${s.battery}%${connected ? '' : ' (last known)'}`;
  }
  const con = $('#top-contact');
  con.hidden = !live;
  if (live) {
    if (!connected) {
      con.style.color = 'var(--muted)';
      con.innerHTML = ICONS.info({ w: 16, stroke: 'currentColor' }) + 'Contact &mdash;';
    } else if (s.contact === true) {
      con.style.color = 'var(--ink-2)';
      con.innerHTML = ICONS.check({ w: 16, stroke: 'var(--ok)' }) + 'Skin contact';
    } else if (s.contact === false) {
      con.style.color = 'var(--warn-ink)';
      con.innerHTML = ICONS.warn({ w: 16, stroke: 'var(--warn-icon)' }) + 'No skin contact';
    } else {
      con.title = 'Contact is reported only while ECG or motion is on';
      con.style.color = 'var(--muted)';
      con.innerHTML = ICONS.info({ w: 16, stroke: 'currentColor' }) + 'Contact n/a';
    }
  }
  $('#top-sep').hidden = !live && s.battery == null;

  const settingsBtn = $('#btn-settings');
  settingsBtn.innerHTML = ICONS.settings({ w: 14, stroke: 'var(--ink-2)' }) +
    (ui.screen === 'settings' ? 'Close settings' : 'Settings');
  const disc = $('#btn-disconnect');
  disc.hidden = !(live || connecting);
  disc.innerHTML = ICONS.unlink({ w: 14, stroke: 'var(--ink)' }) + 'Disconnect';

  const recording = s.logging;
  $('#titlebar').classList.toggle('recording', recording);
  $('#title-rec').hidden = !recording;
  if (recording) {
    const who = ui.form.participant || '';
    $('#title-rec-text').textContent = `REC ${hms(s.session.elapsedS)}${who ? ' \u00b7 ' + who : ''}`;
  }
}

/* ------------------------------------------------------------------ connect screen */
const CHECKLIST = [
  ['drop', 'Wet the electrodes and put the strap on', 'The strap wakes up only when it touches skin.'],
  ['phone', 'Close phone apps connected to the strap', 'Polar Flow, Polar Beat and similar apps hold on to it.'],
  ['bluetooth', 'Bluetooth is on', 'Checked by the app.'],
];

function renderChecklist(s) {
  const alert = !s.scanning && !s.devices.length && (s.scanError || s.scannedOnce);
  const el = $('#checklist');
  el.classList.toggle('alert', !!alert);
  const btOk = !s.scanError;
  el.innerHTML =
    `<div style="display:flex;align-items:center;justify-content:space-between">
       <h2>Before you start</h2>
       ${alert ? `<span class="chip warn">${ICONS.warn({ w: 13, stroke: 'var(--warn-icon)' })}Check these first</span>` : ''}
     </div>
     <ul>` +
    CHECKLIST.map(([icon, t, d], i) =>
      `<li><span class="icon">${ICONS[icon]({ w: 16, stroke: 'var(--ink-2)' })}</span>
         <div style="flex:1;min-width:0"><div class="t">${t}</div><div class="d">${d}</div></div>
         ${i === 2 ? (btOk
        ? `<span class="chip ok">${ICONS.tick({ w: 13, stroke: 'var(--ok)' })}On</span>`
        : `<span class="chip warn">Off</span>`) : ''}
       </li>`).join('') + '</ul>';
}

function deviceCard(d, s, opts = {}) {
  const sig = signal(d.rssi);
  const isLast = d.deviceId === s.lastDeviceId;
  const selected = opts.selected;
  const connecting = opts.connecting;
  const failed = opts.failed;
  let sub = isLast ? 'Selected automatically &middot; last used' : 'Not used on this PC before';
  if (sig.level === 1) sub = 'Move closer to the PC for a steady link';
  if (connecting) sub = 'Connecting&hellip; usually 2&ndash;5 s';
  if (failed) sub = 'Last used';

  let action;
  if (connecting) {
    action = `<button type="button" class="btn ghost" data-act="cancel-connect">Cancel</button>`;
  } else if (failed) {
    action = '';
  } else {
    action = `<button type="button" class="btn ${selected ? 'primary' : ''}" data-act="connect" data-id="${esc(d.deviceId)}">Connect</button>`;
  }

  let extra = '';
  if (connecting) {
    const step = Math.min(3, Math.max(1, s.connectAttempt));
    const steps = [
      ['Strap found', `<span style="color:var(--muted);font-size:12px">&middot; signal ${d.rssi != null ? d.rssi + ' dBm' : 'n/a'}</span>`],
      ['Bluetooth link open', ''],
      ['Reading battery and firmware&hellip;', ''],
      ['Ready', ''],
    ];
    extra = '<ol class="steps">' + steps.map((row, i) => {
      const cls = i < step ? 'done' : i === step ? 'active' : '';
      const mark = i < step ? ICONS.check({ w: 15, stroke: 'var(--ok)' })
        : i === step ? ICONS.spinner({ w: 14, stroke: 'var(--ink-2)' })
          : '<span class="bullet"></span>';
      return `<li class="${cls}"><span style="width:16px;display:flex">${mark}</span>${row[0]}${row[1]}</li>`;
    }).join('') + '</ol>';
  } else if (failed) {
    const attempt = Math.min(s.connectAttempts || 3, Math.max(1, s.connectAttempt || 3));
    extra =
      `<div class="failure">
         <div class="head">${ICONS.cross({ w: 16, stroke: 'var(--err-ink)' })}Couldn&rsquo;t connect to this strap</div>
         <p>${esc(s.connectError || '')}<br>Most likely it is still connected to a phone. Close Polar Flow or
            Polar Beat on the phone, or turn off the phone&rsquo;s Bluetooth.</p>
         <div class="retryline">${ICONS.info({ w: 14, stroke: 'currentColor' })}
           <span>Tried ${attempt} time${attempt === 1 ? '' : 's'}</span></div>
         <div class="buttons">
           <button type="button" class="btn primary" data-act="connect" data-id="${esc(d.deviceId)}">${ICONS.refresh({ w: 16 })}Retry now</button>
           <button type="button" class="btn" data-act="scan">Pick another device</button>
         </div>
       </div>`;
  }

  return `<div class="device ${selected ? 'selected' : ''} ${failed ? 'failed' : ''}">
      <div class="top">
        <span class="icon">${ICONS.strap({ w: 18, stroke: 'var(--ink-2)' })}</span>
        <div style="flex:1;min-width:0">
          <div class="title"><span class="name">Polar H10</span><span class="id">${esc(d.deviceId)}</span>
            ${isLast ? `<span class="chip info">${ICONS.clock({ w: 13, stroke: 'var(--info-ink)' })}Last used</span>` : ''}
          </div>
          <div class="sub">${sub}</div>
        </div>
        <span class="signal">${ICONS.bars(sig.level)}${sig.label}</span>
        ${action}
      </div>${extra}
    </div>`;
}

function renderConnect(s) {
  const btOff = !!(s.scanError && /bluetooth/i.test(s.scanError));
  $('#connect-wrap').hidden = btOff;
  $('#bt-off-wrap').hidden = !btOff;
  if (btOff) {
    $('#bt-off-icon').innerHTML = ICONS.bluetooth({ w: 28, stroke: 'var(--ink-2)' });
    return;
  }

  const actions = $('#connect-actions');
  const body = $('#connect-body');
  const connecting = s.state === 'connecting';
  const failed = !!s.connectError && s.state === 'idle';

  if (s.scanning) {
    actions.innerHTML =
      `<button type="button" class="btn big" disabled>${ICONS.spinner({ w: 18, stroke: 'var(--ink-2)' })}Searching&hellip;</button>`;
  } else if (s.devices.length || failed) {
    actions.innerHTML =
      `<button type="button" class="btn" data-act="scan" ${connecting ? 'disabled' : ''}>${ICONS.refresh({ w: 16 })}Scan again</button>` +
      (connecting ? '' : `<span class="muted" style="font-size:13px">Scan finished &middot; ${s.devices.length} found</span>`);
  } else {
    actions.innerHTML =
      `<button type="button" class="btn primary big" data-act="scan">${ICONS.search({ w: 18 })}Find my Polar H10</button>`;
  }

  let html = '';
  if (s.scanning) {
    const pct = Math.min(100, (s.scanElapsed / 10) * 100);
    html += `<div class="scan-progress">
        <div class="labels"><span>Looking for straps nearby&hellip;</span>
          <span class="muted tnum">${Math.min(10, Math.round(s.scanElapsed))} of 10 s</span></div>
        <div class="bar"><span style="width:${pct}%"></span></div>
      </div>`;
  }

  if (s.devices.length) {
    const selectedId = connecting || failed
      ? (s.device ? s.device.deviceId : '')
      : (s.devices.find(d => d.deviceId === s.lastDeviceId) || s.devices[0]).deviceId;
    const shown = failed ? s.devices.filter(d => d.deviceId === selectedId) : s.devices;
    html += `<div class="list-head">
        <h2>${failed ? 'Selected strap' : s.scanning ? 'Found so far &middot; ' + s.devices.length : 'Straps nearby &middot; ' + s.devices.length}</h2>
        <span class="muted" style="font-size:12px">${failed && s.devices.length > 1
        ? (s.devices.length - 1) + ' other straps nearby' : 'Strongest signal first'}</span>
      </div><div class="devices">` +
      shown.map(d => deviceCard(d, s, {
        selected: d.deviceId === selectedId && !failed,
        connecting: connecting && d.deviceId === selectedId,
        failed: failed && d.deviceId === selectedId,
      })).join('') + '</div>';
    if (s.scanning) {
      html += `<div class="still-looking">${ICONS.spinner({ w: 14, stroke: 'var(--muted)' })}Still looking&hellip;</div>`;
    }
  } else if (!s.scanning && s.scannedOnce) {
    html += `<section class="card empty-state">
        <span class="icon">${ICONS.search({ w: 24, stroke: 'var(--ink-2)' })}</span>
        <h2>No Polar H10 found</h2>
        <p>We searched for 10 seconds. Go through the checklist on the right, then search again.</p>
        <div class="row">
          <button type="button" class="btn primary big" data-act="scan">${ICONS.refresh({ w: 18 })}Scan again</button>
          <div style="display:flex;align-items:center;gap:10px">
            <button type="button" class="switch" role="switch" aria-checked="${s.settings.autoRescan}" data-act="auto-rescan"><span class="knob"></span></button>
            <div style="line-height:1.3">
              <div style="font-size:13px;font-weight:600">Keep scanning automatically</div>
              <div class="muted" style="font-size:12px">Every 15 s until a strap appears</div>
            </div>
          </div>
        </div>
      </section>`;
  } else if (!s.scanning && s.lastDeviceId) {
    html += `<div class="list-head"><h2>Last used</h2>
        <span class="muted" style="font-size:12px">Remembered on this PC</span></div>
      <div class="devices"><div class="device">
        <div class="top">
          <span class="icon">${ICONS.strap({ w: 18, stroke: 'var(--ink-2)' })}</span>
          <div style="flex:1;min-width:0">
            <div class="title"><span class="name">Polar H10</span><span class="id">${esc(s.lastDeviceId)}</span>
              <span class="chip info">${ICONS.clock({ w: 13, stroke: 'var(--info-ink)' })}Last used</span></div>
            <div class="sub">Remembered from the last session</div>
          </div>
          <span class="signal">${ICONS.bars(0)}Not seen yet</span>
          <button type="button" class="btn" data-act="reconnect">${ICONS.link({ w: 16, stroke: 'var(--ink)' })}Reconnect</button>
        </div></div></div>`;
  }

  body.innerHTML = html;
  renderChecklist(s);
}

/* ------------------------------------------------------------------ charts */
function polyline(points, x, y, color, width) {
  if (!points.length) return '';
  const pts = points.map(p => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ');
  return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${width}"
      stroke-linejoin="round" stroke-linecap="round"></polyline>`;
}

function band(points, box, opts) {
  // box: {x0,x1,top,height}; returns grid lines, labels and the line itself
  const values = points.map(p => p[1]);
  let lo = opts.min != null ? opts.min : Math.min(...values);
  let hi = opts.max != null ? opts.max : Math.max(...values);
  if (!values.length) { lo = 0; hi = 1; }
  if (hi - lo < opts.minSpan) {
    const mid = (hi + lo) / 2;
    lo = mid - opts.minSpan / 2; hi = mid + opts.minSpan / 2;
  }
  const pad = (hi - lo) * 0.15;
  lo -= pad; hi += pad;
  const step = niceStep((hi - lo) / 4);
  const y = v => box.top + box.height - ((v - lo) / (hi - lo)) * box.height;
  let grid = '';
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) {
    const yy = y(t);
    if (yy < box.top || yy > box.top + box.height) continue;
    grid += `<line x1="${box.x0}" y1="${yy.toFixed(1)}" x2="${box.x1}" y2="${yy.toFixed(1)}" stroke="var(--grid)" stroke-width="1"></line>` +
      `<text x="${box.x0 - 8}" y="${(yy + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--muted)">${formatTick(t)}</text>`;
  }
  return { grid, y };
}

function niceStep(raw) {
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-6))));
  const n = raw / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}
function formatTick(v) {
  return Math.abs(v) >= 1000 ? String(Math.round(v)) : String(Math.round(v * 10) / 10);
}

function timeAxis(x0, x1, yy, span, unit) {
  const marks = 5;
  let out = '';
  for (let i = 0; i <= marks; i++) {
    const frac = i / marks;
    const x = x0 + (x1 - x0) * frac;
    const left = span * (1 - frac);
    const label = i === marks ? 'now'
      : unit === 'min' ? `\u2212${Math.round(left / 60)} min` : `\u2212${Math.round(left)} s`;
    const anchor = i === 0 ? 'start' : i === marks ? 'end' : 'middle';
    out += `<text x="${x.toFixed(1)}" y="${yy}" font-size="11" fill="var(--muted)" text-anchor="${anchor}">${label}</text>`;
  }
  return out;
}

function chartFrame(inner) {
  return `<svg viewBox="0 0 842 252" preserveAspectRatio="none" role="img">${inner}</svg>`;
}

function legendLabel(x, y, color, text) {
  return `<rect x="${x}" y="${y}" width="10" height="3" rx="1.5" fill="${color}"></rect>` +
    `<text x="${x + 16}" y="${y + 5}" font-size="11.5" font-weight="600" fill="var(--ink-2)">${text}</text>`;
}

function drawHrRr(series) {
  const x0 = 44, x1 = 832, now = series.now, span = series.span;
  const x = t => x0 + ((t - (now - span)) / span) * (x1 - x0);
  const hr = series.hr.filter(p => p[0] >= now - span);
  const rr = series.rr.filter(p => p[0] >= now - span);
  const hrBand = band(hr, { x0, x1, top: 18, height: 92 }, { minSpan: 20 });
  const rrBand = band(rr, { x0, x1, top: 140, height: 84 }, { minSpan: 200 });
  return chartFrame(
    legendLabel(x0, 7, 'var(--hr)', 'Heart rate &middot; bpm') + hrBand.grid +
    polyline(hr, x, hrBand.y, 'var(--hr)', 2) +
    legendLabel(x0, 128, 'var(--rr)', 'RR interval &middot; ms') + rrBand.grid +
    polyline(rr, x, rrBand.y, 'var(--rr)', 1.5) +
    timeAxis(x0, x1, 247, span, 'min'));
}

function drawEcg(series) {
  const x0 = 44, x1 = 832, now = series.now, span = series.span;
  const x = t => x0 + ((t - (now - span)) / span) * (x1 - x0);
  const pts = series.ecg.filter(p => p[0] >= now - span);
  const b = band(pts, { x0, x1, top: 22, height: 200 }, { minSpan: 400 });
  return chartFrame(
    legendLabel(x0, 7, 'var(--ecg)', 'ECG &middot; &micro;V') + b.grid +
    polyline(pts, x, b.y, 'var(--ecg)', 1.4) +
    timeAxis(x0, x1, 247, span, 's'));
}

function drawAcc(series) {
  const x0 = 44, x1 = 832, now = series.now, span = series.span;
  const x = t => x0 + ((t - (now - span)) / span) * (x1 - x0);
  const axes = ['x', 'y', 'z'].map(k => series[k].filter(p => p[0] >= now - span));
  const all = [].concat(...axes);
  const b = band(all, { x0, x1, top: 22, height: 200 }, { minSpan: 500 });
  const colors = ['var(--ax)', 'var(--ay)', 'var(--az)'];
  let legend = legendLabel(x0, 7, 'var(--ink-2)', 'Acceleration &middot; mg');
  ['X', 'Y', 'Z'].forEach((name, i) => {
    legend += legendLabel(x1 - 150 + i * 50, 7, colors[i], name);
  });
  return chartFrame(legend + b.grid +
    axes.map((pts, i) => polyline(pts, x, b.y, colors[i], 1.4)).join('') +
    timeAxis(x0, x1, 247, span, 's'));
}

function chartEmpty(kind, s) {
  const names = { ecg: 'ECG', acc: 'Motion' };
  const available = s.streams.available.includes(kind === 'ecg' ? 0 : 2);
  const unsupported = s.streams.available.length && !available;
  return `<div class="chart-empty">
      ${ICONS[kind === 'ecg' ? 'ecg' : 'motion']({ w: 28, stroke: 'var(--disabled-ink)' })}
      <div class="t">${names[kind]} is off</div>
      <div>${unsupported ? 'This strap does not offer it.' : 'Not streaming, so nothing is drawn or saved.'}</div>
      ${unsupported ? '' : `<button type="button" class="btn" data-act="enable-${kind}">Enable ${kind === 'ecg' ? 'ECG' : 'motion'}</button>`}
    </div>`;
}

function renderCharts(s) {
  const tabs = [['hr', 'HR &amp; RR', 'pulse'], ['ecg', 'ECG', 'ecg'], ['acc', 'Motion', 'motion']];
  $('#chart-tabs').innerHTML = tabs.map(([key, label, icon]) => {
    const on = ui.tab === key;
    const off = (key === 'ecg' && !s.streams.ecg) || (key === 'acc' && !s.streams.acc);
    return `<button type="button" role="tab" aria-selected="${on}" data-act="tab" data-tab="${key}">
        ${ICONS[icon]({ w: 14, stroke: on ? 'var(--ink)' : 'var(--muted)' })}${label}
        ${off && key !== 'hr' ? '<span style="font-weight:400;color:var(--muted)">&middot; off</span>' : ''}
      </button>`;
  }).join('');

  const spans = { hr: 'Last 5 min', ecg: `Last 5 s &middot; 130 Hz`, acc: `Last 20 s &middot; ${s.streams.accRate} Hz &middot; \u00b1${s.streams.accRange} g` };
  $('#chart-span').innerHTML = ICONS.clock({ w: 13, stroke: 'var(--muted)' }) + spans[ui.tab];

  const panel = $('#chart-panel');
  const series = s.series;
  if (ui.tab === 'ecg' && !s.streams.ecg) { panel.innerHTML = chartEmpty('ecg', s); return; }
  if (ui.tab === 'acc' && !s.streams.acc) { panel.innerHTML = chartEmpty('acc', s); return; }
  if (series.tab !== ui.tab) return;
  if (ui.tab === 'hr') panel.innerHTML = drawHrRr(series);
  else if (ui.tab === 'ecg') panel.innerHTML = drawEcg(series);
  else panel.innerHTML = drawAcc(series);
}

/* ------------------------------------------------------------------ metrics, events */
const METRICS = [
  ['hr_mean', 'Mean HR', 'bpm', 1],
  ['rr_mean', 'Mean RR', 'ms', 0],
  ['sdnn', 'SDNN', 'ms', 1],
  ['rmssd', 'RMSSD', 'ms', 1],
  ['pnn50', 'pNN50', '%', 1],
];

function renderMetrics(s) {
  $('#window-picker').innerHTML = s.windowChoices.map(w =>
    `<button type="button" aria-pressed="${w === s.windowLabel}" data-act="window" data-window="${esc(w)}">${w === 'Full session' ? 'Full' : w}</button>`).join('');
  $('#metric-tiles').innerHTML = METRICS.map(([key, name, unit, digits]) =>
    `<div class="tile"><span class="k">${name}</span>
       <span class="v"><b>${num(s.metrics[key], digits)}</b><span>${unit}</span></span></div>`).join('');
  $('#metrics-note').innerHTML = ICONS.info({ w: 13, stroke: 'var(--muted)' }) + 'Descriptive, not medical';
  const secs = s.metrics.seconds != null ? s.metrics.seconds : null;
  $('#metrics-basis').textContent = secs ? `Based on ${mmss(secs)} of data` : '';
}

function renderEvents(s) {
  $('#events-count').textContent = String(s.events.length);
  const list = $('#events-list');
  list.hidden = !ui.eventsOpen;
  $('#btn-events-toggle').innerHTML = (ui.eventsOpen ? 'Hide' : 'Show') +
    ICONS[ui.eventsOpen ? 'chevron' : 'chevronDown']({ w: 14, stroke: 'var(--ink-2)' });
  if (!ui.eventsOpen) return;
  const iconFor = k => k === 'error' ? ICONS.cross({ w: 13, stroke: 'var(--err-ink)' })
    : k === 'warning' ? ICONS.warn({ w: 13, stroke: 'var(--warn-icon)' })
      : k === 'info' ? ICONS.info({ w: 13, stroke: 'var(--info-ink)' })
        : ICONS.link({ w: 13, stroke: 'var(--ok)' });
  list.innerHTML = s.events.slice(0, 3).map(e =>
    `<li><span class="time">${esc(e.time)}</span>
       <span class="bullet ${esc(e.kind)}">${iconFor(e.kind)}</span>
       <span class="text" title="${esc(e.text)}">${esc(e.text)}</span></li>`).join('');
}

/* ------------------------------------------------------------------ recording card */
function recMode(s) {
  if (s.logging) return ui.confirmStop ? 'confirm' : 'active';
  if (s.saved) return 'saved';
  return 'idle';
}

function idleCardHtml(s) {
  const f = ui.form;
  return `<div class="idle-head"><h2>Recording</h2>
      <span class="nothing"><span class="ring"></span>Nothing is being saved</span></div>
    <div class="form">
      <div class="field">
        <div class="row"><label for="pid">Participant ID</label><span class="hint">Required</span></div>
        <input id="pid" class="input" type="text" value="${esc(f.participant)}" autocomplete="off">
        <p class="msg" id="pid-msg">Use a research code, not a name.</p>
      </div>
      <div class="field">
        <div class="row"><label for="cond">Condition</label><span class="hint">Optional</span></div>
        <input id="cond" class="input" type="text" value="${esc(f.condition)}" autocomplete="off">
      </div>
      <div class="field">
        <div class="row"><label for="notes">Notes</label><span class="hint">Optional</span></div>
        <textarea id="notes" class="textarea" placeholder="e.g. seated, eyes open">${esc(f.notes)}</textarea>
      </div>
    </div>
    <div class="bottom">
      <div class="saving-as">${ICONS.file({ w: 14, stroke: 'var(--muted)' })}Saving as <b>CSV</b>
        <span class="grow"></span>
        <button type="button" class="btn link" data-act="settings">Change</button></div>
      <button type="button" class="btn primary big block" id="btn-start" data-act="start">
        ${ICONS.record({ w: 18, fill: 'var(--rec)', stroke: 'var(--rec)' })}Start recording</button>
    </div>`;
}

function activeCardHtml(s) {
  const sess = s.session;
  const waiting = s.state === 'reconnecting';
  const badge = waiting
    ? `<span class="chip warn">${ICONS.spinner({ w: 12, stroke: 'var(--warn-icon)' })}Waiting for strap</span>`
    : `<span class="since" id="rec-since"></span>`;
  return `<div class="rec-header">
      <div class="top"><span class="rec-label"><span class="dot"></span>REC</span>${badge}</div>
      <div class="elapsed" id="rec-elapsed">00:00:00</div>
      <div class="who"><b>${esc(ui.form.participant)}</b>${ui.form.condition ? ' &middot; ' + esc(ui.form.condition) : ''}</div>
    </div>
    <div class="rec-body">
      <div class="rec-scroll">
      <div class="counters">
        <div><div class="k">Packets</div><div class="v" id="c-packets">0</div></div>
        <div><div class="k">RR intervals</div><div class="v" id="c-rr">0</div></div>
        <div><div class="k">Excluded RR<span title="RR outside 300-2000 ms is kept in the file but left out of metrics" style="display:inline-flex">${ICONS.info({ w: 12, stroke: 'var(--muted)' })}</span></div><div class="v" id="c-excl">0</div></div>
        <div><div class="k">Disconnects</div><div class="v" id="c-disc">0</div></div>
        <div><div class="k">File size</div><div class="v" id="c-size">0 MB</div></div>
        <div><div class="k">Segment</div><div class="v" id="c-seg">1</div></div>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px">
        <div class="saving-as">${ICONS.file({ w: 14, stroke: 'var(--muted)' })}Saving as <b>CSV</b></div>
        <div class="path" id="rec-path" title="">${esc(sess.folder || '')}</div>
      </div>
      </div>
      <div class="rec-actions">
        <button type="button" class="btn danger" style="flex:1" data-act="ask-stop">
          ${ICONS.stop({ w: 16, stroke: 'var(--err-ink)' })}Stop recording</button>
        <button type="button" class="btn" data-act="open-folder">${ICONS.folder({ w: 16, stroke: 'var(--ink)' })}Open folder</button>
      </div>
    </div>`;
}

function confirmCardHtml(s) {
  return `<div class="rec-header">
      <div class="top"><span class="rec-label"><span class="dot"></span>REC</span></div>
      <div class="elapsed" id="rec-elapsed">${hms(s.session.elapsedS)}</div>
      <div class="who"><b>${esc(ui.form.participant)}</b>${ui.form.condition ? ' &middot; ' + esc(ui.form.condition) : ''}</div>
    </div>
    <div style="padding:16px 20px 20px;display:flex;flex-direction:column;gap:12px">
      <div class="confirm" role="alertdialog">
        <h3>Stop this recording?</h3>
        <p>The files are closed and the summary is written. The strap stays connected, so the next
           recording can start right away.</p>
        <div class="buttons">
          <button type="button" class="btn" data-act="keep">Keep recording</button>
          <button type="button" class="btn danger-solid" data-act="stop">${ICONS.stop({ w: 16, stroke: '#fff' })}Stop and save</button>
        </div>
      </div>
      <div class="muted tnum" style="font-size:12.5px">${mmss(s.session.elapsedS)} recorded &middot;
        ${megabytes(s.session.bytes)} so far &middot; Esc keeps recording</div>
    </div>`;
}

function savedCardHtml(s) {
  const v = s.saved;
  return `<div style="padding:18px 20px 20px;display:flex;flex-direction:column;height:100%">
      <div class="saved-head">${ICONS.check({ w: 22, stroke: 'var(--ok)' })}<span class="t">Saved</span>
        <span class="grow"></span><span class="muted tnum" style="font-size:12px">${esc(v.at)}</span></div>
      <div style="font-size:13px;color:var(--ink-2);margin-top:4px">${esc(v.participant)} &middot; ${megabytes(v.bytes)}</div>
      <div style="margin-top:14px">
        <div class="saved-path"><span>${esc(v.folder)}</span>
          <button type="button" class="btn small" data-act="open-saved">${ICONS.folder({ w: 14, stroke: 'var(--ink)' })}Open</button></div>
      </div>
      <div class="files" style="margin-top:12px">
        <div class="muted" style="font-size:12px;margin-bottom:4px">Files written</div>
        <div class="row"><span class="k">CSV</span><span class="v">HR${s.streams.ecg ? ', ECG' : ''}${s.streams.acc ? ', ACC' : ''}, summary</span></div>
        <div class="row"><span class="k">Always</span><span class="v">session.json, raw.jsonl, app.log</span></div>
      </div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:6px;align-items:center">
        <button type="button" class="btn primary big block" data-act="another">
          ${ICONS.record({ w: 18, fill: 'var(--rec)', stroke: 'var(--rec)' })}Start another recording</button>
        <span class="muted" style="font-size:12px">Same strap, still connected.</span>
      </div>
    </div>`;
}

function renderRecCard(s) {
  const mode = recMode(s);
  const card = $('#rec-card');
  if (mode !== ui.recMode) {
    ui.recMode = mode;
    card.className = 'card rec-card' + (mode === 'idle' || mode === 'saved' ? ' idle' : ' rec');
    card.innerHTML = mode === 'idle' ? idleCardHtml(s)
      : mode === 'active' ? activeCardHtml(s)
        : mode === 'confirm' ? confirmCardHtml(s) : savedCardHtml(s);
    if (mode === 'idle') wireForm(s);
  }
  if (mode === 'active') {
    const sess = s.session;
    $('#rec-elapsed').textContent = hms(sess.elapsedS);
    const since = $('#rec-since');
    if (since) {
      const started = new Date(Date.now() - sess.elapsedS * 1000);
      since.textContent = `since ${pad(started.getHours())}:${pad(started.getMinutes())}:${pad(started.getSeconds())}`;
    }
    $('#c-packets').textContent = sess.packets;
    $('#c-rr').textContent = sess.rr;
    $('#c-excl').textContent = sess.rrExcluded;
    const disc = $('#c-disc');
    disc.textContent = sess.disconnects;
    disc.classList.toggle('warn', sess.disconnects > 0);
    $('#c-size').textContent = megabytes(sess.bytes);
    $('#c-seg').textContent = sess.disconnects + 1;
    const p = $('#rec-path');
    p.textContent = sess.folder || '';
    p.title = sess.folder || '';
    // the "waiting for strap" badge swaps with the connection state
    const waiting = s.state === 'reconnecting';
    const badge = card.querySelector('.rec-header .top .chip, .rec-header .top .since');
    if (badge) {
      if (waiting && !badge.classList.contains('chip')) {
        badge.outerHTML = `<span class="chip warn">${ICONS.spinner({ w: 12, stroke: 'var(--warn-icon)' })}Waiting for strap</span>`;
      } else if (!waiting && badge.classList.contains('chip')) {
        badge.outerHTML = '<span class="since" id="rec-since"></span>';
      }
    }
  }
  if (mode === 'idle') {
    const btn = $('#btn-start');
    if (btn) btn.disabled = !ui.form.participant.trim() || s.state !== 'connected';
  }
}

function wireForm(s) {
  const pid = $('#pid'), cond = $('#cond'), notes = $('#notes');
  pid.addEventListener('input', () => {
    ui.form.participant = pid.value;
    checkPid();
  });
  cond.addEventListener('input', () => { ui.form.condition = cond.value; });
  notes.addEventListener('input', () => { ui.form.notes = notes.value; });
  pid.addEventListener('keydown', e => { if (e.key === 'Enter') startRecording(); });
  if (!pid.value) pid.focus();
}

let pidTimer = null;
function checkPid() {
  clearTimeout(pidTimer);
  pidTimer = setTimeout(async () => {
    const input = $('#pid'), msg = $('#pid-msg');
    if (!input || !msg) return;
    const value = input.value.trim();
    if (!value) {
      input.removeAttribute('aria-invalid');
      msg.className = 'msg';
      msg.textContent = 'Use a research code, not a name.';
      return;
    }
    const res = await api.check_participant(value);
    if (res.ok) {
      input.removeAttribute('aria-invalid');
      msg.className = 'msg';
      msg.textContent = 'Use a research code, not a name.';
    } else {
      input.setAttribute('aria-invalid', 'true');
      msg.className = 'msg error';
      msg.innerHTML = ICONS.cross({ w: 14, stroke: 'var(--err)' }) + `<span>${esc(res.message)}</span>`;
    }
  }, 180);
}

/* ------------------------------------------------------------------ streams */
function renderStreams(s) {
  const st = s.streams;
  const rows = [
    { key: 'hr', icon: 'pulse', title: 'Heart rate &amp; RR', detail: 'About 1 value per second', rate: '\u2248 1 MB/h', locked: true, on: true },
    { key: 'ecg', icon: 'ecg', title: 'ECG', detail: '130 Hz &middot; raw \u00b5V', rate: '\u2248 35 MB/h', on: st.ecg },
    { key: 'acc', icon: 'motion', title: 'Accelerometer', detail: `${st.accRate} Hz &middot; \u00b1${st.accRange} g`, rate: `\u2248 ${accMbPerHour(st.accRate)} MB/h`, on: st.acc },
  ];
  $('#streams-list').innerHTML = rows.map(r =>
    `<div class="stream">
       <span class="icon">${ICONS[r.icon]({ w: 15, stroke: 'var(--ink-2)' })}</span>
       <div class="txt"><div class="t">${r.title}</div><div class="d">${r.detail}</div></div>
       <span class="rate">${r.rate}</span>
       ${r.locked
      ? `<span class="locked" title="Always on">${ICONS.lock({ w: 13, stroke: 'var(--muted)' })}</span>`
      : `<button type="button" class="switch" role="switch" aria-checked="${r.on}" aria-label="${r.title}"
             data-act="stream" data-stream="${r.key}"><span class="knob"></span></button>`}
     </div>`).join('');

  const opts = $('#acc-options');
  opts.hidden = !st.acc;
  if (st.acc) {
    opts.innerHTML =
      `<div><div class="k">Rate (Hz)</div><div class="segmented tiny" role="group" aria-label="Accelerometer rate">` +
      [25, 50, 100, 200].map(v => `<button type="button" aria-pressed="${v === st.accRate}" data-act="acc-rate" data-value="${v}">${v}</button>`).join('') +
      `</div></div><div><div class="k">Range (g)</div><div class="segmented tiny" role="group" aria-label="Accelerometer range">` +
      [2, 4, 8].map(v => `<button type="button" aria-pressed="${v === st.accRange}" data-act="acc-range" data-value="${v}">\u00b1${v}</button>`).join('') +
      `</div></div>`;
  }
}
function accMbPerHour(rate) {
  return Math.round(15 * rate / 50);
}

/* ------------------------------------------------------------------ banner */
function renderBanner(s) {
  const el = $('#banner');
  if (s.state === 'reconnecting') {
    const left = s.reconnectLeftS || 0;
    const total = Math.max(1, s.graceS);
    const frac = Math.max(0, Math.min(1, left / total));
    el.hidden = false;
    el.className = 'banner warn';
    el.innerHTML =
      `<span class="countdown">
         <svg width="44" height="44" viewBox="0 0 44 44"><circle cx="22" cy="22" r="18" fill="none"
           stroke="var(--warn-border)" stroke-width="3" stroke-opacity="0.45"></circle>
           <circle cx="22" cy="22" r="18" fill="none" stroke="var(--warn-icon)" stroke-width="3"
             stroke-linecap="round" stroke-dasharray="${(2 * Math.PI * 18).toFixed(1)}"
             stroke-dashoffset="${((1 - frac) * 2 * Math.PI * 18).toFixed(1)}"
             transform="rotate(-90 22 22)"></circle></svg>
         <span>${mmss(left)}</span></span>
       <div style="flex:1;min-width:0">
         <div class="title">Connection lost &mdash; reconnecting&hellip;</div>
         <div class="detail">Attempt ${s.reconnectAttempt || 1} &middot; ${mmss(left)} left.
           ${s.logging ? 'The recording stays open; keep' : 'Keep'} the strap on and within a few metres of the PC.</div>
       </div>
       <div class="buttons">
         <button type="button" class="btn" data-act="retry-now">${ICONS.refresh({ w: 16, stroke: 'var(--ink)' })}Retry now</button>
         <button type="button" class="btn" data-act="extend">${ICONS.clock({ w: 16, stroke: 'var(--ink)' })}Extend +2 min</button>
         ${s.logging ? `<button type="button" class="btn danger" data-act="stop">${ICONS.stop({ w: 16, stroke: 'var(--err-ink)' })}Stop and save now</button>` : ''}
       </div>`;
    return;
  }
  if (s.lostNotice) {
    el.hidden = false;
    el.className = 'banner err';
    el.innerHTML =
      `<span class="round-icon">${ICONS.cross({ w: 20, stroke: 'var(--err-ink)' })}</span>
       <div style="flex:1;min-width:0">
         <div class="title">Could not reconnect.${s.lostNotice.saved ? ' Recording was saved.' : ''}</div>
         <div class="detail">The strap was out of reach for the full grace period.
           ${s.lostNotice.folder ? 'Data is in ' + esc(s.lostNotice.name) + '.' : ''}</div>
       </div>
       <div class="buttons">
         ${s.lostNotice.folder ? `<button type="button" class="btn" data-act="open-saved">${ICONS.folder({ w: 16, stroke: 'var(--ink)' })}Open folder</button>` : ''}
         <button type="button" class="btn primary" data-act="reconnect">${ICONS.link({ w: 16 })}Reconnect</button>
       </div>`;
    return;
  }
  el.hidden = true;
}

/* ------------------------------------------------------------------ settings */
const FORMATS = [
  { key: 'csv', name: 'CSV', ext: '.csv', chip: 'Default', chipClass: 'chip',
    desc: 'Opens in Excel. One file per stream (HR, ECG, ACC) plus a summary.', size: '\u2248 51 MB', ready: true },
  { key: 'jsonl', name: 'JSON Lines', ext: '.jsonl', chip: 'Coming next', chipClass: 'chip',
    desc: 'One JSON object per record. Easy for scripts and web tools.', size: '\u2248 92 MB', ready: false },
  { key: 'parquet', name: 'Parquet', ext: '.parquet', chip: 'Coming next', chipClass: 'chip',
    desc: 'Compressed, typed, columnar. 5-10\u00d7 smaller than CSV; loads fast in Python, R and MATLAB.',
    size: '\u2248 7 MB', ready: false },
];

function renderSettings(s) {
  if (ui.settingsDirty) return;
  const cfg = s.settings;
  $('#formats-list').innerHTML = FORMATS.map(f => {
    const on = cfg.formats.includes(f.key);
    return `<label class="format ${on ? 'on' : ''} ${f.ready ? '' : 'off'}">
        <input type="checkbox" ${on ? 'checked' : ''} ${f.ready ? '' : 'disabled'} data-act="format" data-format="${f.key}">
        <div style="flex:1;min-width:0">
          <div class="title"><span class="n">${f.name}</span><span class="ext">${f.ext}</span>
            <span class="${f.chipClass}">${f.chip}</span></div>
          <div class="d">${f.desc}</div>
        </div>
        <div class="size"><div class="a">${f.size}</div><div class="b">per hour</div></div>
      </label>`;
  }).join('');
  $('#formats-chip').innerHTML = ICONS.file({ w: 13, stroke: 'var(--ink-2)' }) + 'Saving as CSV';
  $('#formats-basis').textContent =
    `Sizes are estimates for the current streams: HR${s.streams.ecg ? ', ECG' : ''}${s.streams.acc ? ', motion at ' + s.streams.accRate + ' Hz' : ''}.`;
  $('#folder-path').textContent = cfg.outputRoot;
  $('#folder-box').title = cfg.outputRoot;
  $('#grace-value').value = String(Math.round(cfg.graceS / 60));
  $('#auto-rescan').setAttribute('aria-checked', String(!!cfg.autoRescan));
  $('#theme-picker').innerHTML = ['system', 'light', 'dark'].map(t =>
    `<button type="button" aria-pressed="${cfg.theme === t}" data-act="theme" data-theme="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('');
  $('#about-version').textContent = `Version ${s.version}`;
}

function applyTheme(theme) {
  const dark = theme === 'dark' ||
    (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

/* ------------------------------------------------------------------ recovery + toast */
function renderRecovery(s) {
  const open = s.recovered.length > 0;
  $('#recovery-overlay').hidden = !open;
  if (!open) return;
  const r = s.recovered[0];
  $('#recovery-icon').innerHTML = ICONS.warn({ w: 20, stroke: 'var(--warn-icon)' });
  $('#recovery-text').textContent =
    'A recording was interrupted, probably because the PC shut down or the app closed. ' +
    'Its summary was rebuilt from the saved data.';
  $('#recovery-details').innerHTML =
    `<dt>Folder</dt><dd class="mono" style="font-size:12px">${esc(r.name)}</dd>`;
  $('#recovery-open').onclick = () => { api.open_folder(r.folder); api.dismiss_recovered(); };
  $('#recovery-dismiss').onclick = () => api.dismiss_recovered();
}

function toast(kind, text) {
  const el = $('#toast');
  el.className = 'toast ' + kind;
  el.innerHTML = (kind === 'err' ? ICONS.cross({ w: 16, stroke: 'currentColor' })
    : ICONS.check({ w: 16, stroke: 'currentColor' })) + `<span>${esc(text)}</span>`;
  el.hidden = false;
  clearTimeout(ui.toastTimer);
  ui.toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

/* ------------------------------------------------------------------ render */
function render(s) {
  const dashboard = ['connected', 'reconnecting'].includes(s.state) || s.logging || !!s.lostNotice;
  if (ui.screen !== 'settings') ui.screen = dashboard ? 'dashboard' : 'connect';
  $('#screen-connect').hidden = ui.screen !== 'connect';
  $('#screen-dashboard').hidden = ui.screen !== 'dashboard';
  $('#screen-settings').hidden = ui.screen !== 'settings';

  renderTop(s);
  if (ui.screen === 'connect') renderConnect(s);
  if (ui.screen === 'dashboard') {
    renderBanner(s);
    renderHr(s);
    renderMetrics(s);
    renderCharts(s);
    renderEvents(s);
    renderRecCard(s);
    renderStreams(s);
  }
  if (ui.screen === 'settings') renderSettings(s);
  renderRecovery(s);
}

function renderHr(s) {
  const live = s.state === 'connected';
  const card = $('#hr-card');
  card.classList.toggle('stale', !live);
  $('#hr-icon').innerHTML = ICONS.heart({ w: 18, fill: live ? 'var(--hr)' : 'var(--disabled-ink)', stroke: live ? 'var(--hr)' : 'var(--disabled-ink)' });
  $('#hr-icon').className = live ? 'beat' : '';
  $('#hr-value').textContent = live && s.hr ? String(s.hr) : '--';
  const foot = $('#hr-foot');
  if (live) {
    foot.innerHTML = `<span class="muted">Last RR</span><span class="rr">${s.lastRr ? Math.round(s.lastRr) + ' ms' : '--'}</span>`;
  } else if (s.lastSeenS != null) {
    foot.innerHTML = ICONS.clock({ w: 14, stroke: 'var(--muted)' }) +
      `<span style="color:var(--ink-2)">Last seen ${mmss(s.lastSeenS)} ago</span>`;
  } else {
    foot.innerHTML = '<span class="muted">Waiting for the strap</span>';
  }
}

/* ------------------------------------------------------------------ actions */
async function startRecording() {
  const res = await api.start_log(ui.form.participant, ui.form.condition, ui.form.notes);
  if (!res.ok) {
    toast('err', res.message);
    const input = $('#pid');
    if (input) input.focus();
  }
}

const ACTIONS = {
  scan: () => { ui.scannedOnce = true; api.scan(); },
  connect: el => api.connect(el.dataset.id || ''),
  reconnect: () => api.connect(''),
  'cancel-connect': () => api.disconnect(),
  'auto-rescan': el => {
    const on = el.getAttribute('aria-checked') !== 'true';
    el.setAttribute('aria-checked', String(on));
    api.save_settings({ autoRescan: on });
  },
  tab: el => { ui.tab = el.dataset.tab; },
  window: el => api.set_window(el.dataset.window),
  stream: el => {
    const on = el.getAttribute('aria-checked') !== 'true';
    const cur = last.streams;
    api.set_streams(el.dataset.stream === 'ecg' ? on : cur.ecg,
      el.dataset.stream === 'acc' ? on : cur.acc, cur.accRate, cur.accRange);
  },
  'enable-ecg': () => api.set_streams(true, last.streams.acc, last.streams.accRate, last.streams.accRange),
  'enable-acc': () => api.set_streams(last.streams.ecg, true, last.streams.accRate, last.streams.accRange),
  'acc-rate': el => api.set_streams(last.streams.ecg, last.streams.acc, Number(el.dataset.value), last.streams.accRange),
  'acc-range': el => api.set_streams(last.streams.ecg, last.streams.acc, last.streams.accRate, Number(el.dataset.value)),
  start: () => startRecording(),
  'ask-stop': () => { ui.confirmStop = true; ui.recMode = null; },
  keep: () => { ui.confirmStop = false; ui.recMode = null; },
  stop: () => { ui.confirmStop = false; ui.recMode = null; api.stop_log(); },
  another: () => { api.dismiss_saved(); ui.recMode = null; ui.form.notes = ''; },
  'open-folder': () => api.open_folder(''),
  'open-saved': () => api.open_folder(last.saved ? last.saved.folder : ''),
  'retry-now': () => api.retry_now(),
  extend: () => api.extend_grace(120),
  settings: () => { ui.screen = 'settings'; },
  theme: el => {
    applyTheme(el.dataset.theme);
    api.save_settings({ theme: el.dataset.theme });
    ui.settingsDirty = false;
  },
};

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el || el.disabled) return;
  const fn = ACTIONS[el.dataset.act];
  if (fn) { fn(el); if (last) render(last); }
});

$('#btn-settings').onclick = () => { ui.screen = ui.screen === 'settings' ? 'dashboard' : 'settings'; if (last) render(last); };
$('#btn-settings-back').onclick = () => { ui.screen = 'dashboard'; if (last) render(last); };
$('#btn-disconnect').onclick = () => api.disconnect();
$('#btn-events-toggle').onclick = () => { ui.eventsOpen = !ui.eventsOpen; if (last) render(last); };
$('#btn-bt-settings').onclick = () => api.open_bluetooth_settings();
$('#btn-browse').onclick = async () => { await api.browse_output(); ui.settingsDirty = false; };
$('#win-min').onclick = () => api.minimize();
$('#win-max').onclick = () => api.toggle_maximize();
$('#win-close').onclick = () => api.close();
$('#grace-minus').onclick = () => nudgeGrace(-1);
$('#grace-plus').onclick = () => nudgeGrace(1);
$('#grace-value').addEventListener('change', () => nudgeGrace(0));
$('#auto-rescan').onclick = () => {
  const on = $('#auto-rescan').getAttribute('aria-checked') !== 'true';
  $('#auto-rescan').setAttribute('aria-checked', String(on));
  api.save_settings({ autoRescan: on });
};

function nudgeGrace(delta) {
  const input = $('#grace-value');
  let minutes = Math.round(Number(input.value) || 0) + delta;
  minutes = Math.max(0, Math.min(1440, minutes));
  input.value = String(minutes);
  api.save_settings({ graceS: minutes * 60 });
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && ui.confirmStop) { ui.confirmStop = false; ui.recMode = null; if (last) render(last); }
});

/* ------------------------------------------------------------------ poll loop */
async function tick() {
  try {
    const s = await api.poll(ui.tab);
    s.scannedOnce = !!ui.scannedOnce;
    // keep the participant shown while recording even though the form is gone
    if (s.logging && !ui.form.participant && s.session.folder) {
      ui.form.participant = s.session.folder.split('\\').pop().split('/').pop().replace(/_\d{8}_\d{6}.*$/, '');
    }
    if (last && !last.saved && s.saved) toast('ok', 'Recording saved');
    if (s.failure && (!last || last.failure !== s.failure)) toast('err', s.failure);
    if (!last) applyTheme(s.settings.theme);
    last = s;
    render(s);
  } catch (err) {
    console.error('poll failed', err);
  }
  setTimeout(tick, POLL_MS);
}

window.addEventListener('pywebviewready', () => {
  api = window.pywebview.api;
  tick();
});
