/* app.js — ORAM dashboard controller */

(function () {
  'use strict';

  // ── websocket ──
  let ws = null;
  let state = {};
  let masterRecording = false;
  const _urlParams = new URLSearchParams(location.search);
  const _authToken = _urlParams.get('token')
    || (document.querySelector('meta[name="oram-token"]') || {}).content
    || '';

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const tokenQuery = _authToken ? `?token=${encodeURIComponent(_authToken)}` : '';
    ws = new WebSocket(`${proto}//${location.host}/ws${tokenQuery}`);

    ws.onopen = () => {
      addLog('connected', 'system', '⚡');
    };

    ws.onclose = (evt) => {
      if (evt.code === 4001) {
        addLog('auth failed — set ?token= in URL', 'error', '✕');
        return;
      }
      addLog('disconnected — reconnecting…', 'error', '✕');
      setTimeout(connect, 2000);
    };

    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === 'command_result') {
        addLog(data.message, 'system', '→');
      } else {
        state = data;
        render(state);
      }
    };
  }

  function sendCommand(text) {
    if (!text.trim()) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'command', text: text }));
      addLog(text, 'system', '›');
    }
  }

  async function apiPost(endpoint, body = {}) {
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (_authToken) headers['Authorization'] = 'Bearer ' + _authToken;
      const res = await fetch(endpoint, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });
      if (res.status === 401) {
        addLog('auth failed — set ?token= in URL', 'error', '✕');
        return null;
      }
      return await res.json();
    } catch (e) {
      addLog('api error: ' + e.message, 'error', '✕');
      return null;
    }
  }

  async function apiGet(endpoint) {
    try {
      const res = await fetch(endpoint);
      return await res.json();
    } catch (e) {
      return null;
    }
  }

  async function apiUploadLayer(target, file) {
    try {
      const headers = {};
      if (_authToken) headers['Authorization'] = 'Bearer ' + _authToken;
      if (file.type) headers['Content-Type'] = file.type;
      const qs = new URLSearchParams({
        target: String(target),
        filename: file.name || 'uploaded.wav',
      });
      const res = await fetch(`/api/upload-layer?${qs.toString()}`, {
        method: 'POST',
        headers,
        body: file,
      });
      if (res.status === 401) {
        addLog('auth failed — set ?token= in URL', 'error', '✕');
        return null;
      }
      return await res.json();
    } catch (e) {
      addLog('upload error: ' + e.message, 'error', '✕');
      return null;
    }
  }

  function hardSilenceOnPageExit() {
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (_authToken) headers['Authorization'] = 'Bearer ' + _authToken;
      fetch('/api/kill', {
        method: 'POST',
        headers,
        body: '{}',
        keepalive: true,
      });
    } catch (_) {}
  }

  window.addEventListener('pagehide', hardSilenceOnPageExit);

  // ── time formatting ──
  function timeNow() {
    const d = new Date();
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  // ── hint bar ──
  const hintText = document.getElementById('hint-text');
  const defaultHint = 'hover over a button for details';

  function setupHints() {
    document.addEventListener('mouseover', (e) => {
      const el = e.target.closest('[data-hint]');
      if (el) {
        hintText.textContent = el.dataset.hint;
        hintText.classList.add('active');
      }
    });

    document.addEventListener('mouseout', (e) => {
      const el = e.target.closest('[data-hint]');
      if (el) {
        // only reset if we're not entering another hinted element
        const related = e.relatedTarget ? e.relatedTarget.closest('[data-hint]') : null;
        if (!related) {
          hintText.textContent = defaultHint;
          hintText.classList.remove('active');
        }
      }
    });
  }

  // ── rendering ──
  const meterDotIn = document.getElementById('meter-dot-in');
  const meterDotOut = document.getElementById('meter-dot-out');
  const btnRecord = document.getElementById('btn-record');
  const btnModeCycle = document.getElementById('btn-mode-cycle');

  let smoothedIn = 0;
  let smoothedOut = 0;
  const waveformCache = new Map();
  const waveformPending = new Map();
  const WAVEFORM_CACHE_CAP = 32;

  function localStableAudioEnabled() {
    const modeSel = document.getElementById('runtime-mode-selector');
    return modeSel && modeSel.value === 'local';
  }

  function selectedEngineOption() {
    const engineSel = document.getElementById('engine-selector');
    if (!engineSel) return null;
    return engineSel.options[engineSel.selectedIndex] || null;
  }

  function selectedApiEngineReady() {
    const opt = selectedEngineOption();
    if (!opt || opt.value === 'auto') return true;
    if (opt.dataset.available === 'true') return true;
    const label = opt.textContent.replace(/\s*\[key needed\]\s*$/, '');
    addLog(`${label} needs an API key before it can generate`, 'error', '✕');
    return false;
  }

  function stableAudioModeRequiresSource(mode) {
    return mode === 'morph' || mode === 'continue' || mode === 'inpaint' || mode === 'latent';
  }

  function stableAudioSourceMode(mode) {
    return stableAudioModeRequiresSource(mode) ? mode : 'morph';
  }

  function fieldValue(id, fallback = '') {
    const el = document.getElementById(id);
    return el && el.value !== '' ? el.value : fallback;
  }

  function numericField(id, fallback) {
    const raw = fieldValue(id, '');
    const value = Number(raw);
    return Number.isFinite(value) ? value : fallback;
  }

  function stableAudioPrompt(fallbackLayer) {
    const text = (document.getElementById('cmd-input')?.value || '').trim();
    if (text) return text;
    return fallbackLayer
      ? `transform layer ${fallbackLayer} into a complementary texture`
      : 'detailed experimental sound texture, no speech';
  }

  function stableAudioPayload(layerNum, promptOverride, options = {}) {
    const requestedMode = options.mode || fieldValue('sa3-mode', 'generate');
    const mode = options.forceSource ? stableAudioSourceMode(requestedMode) : requestedMode;
    const sourceLayer = options.forceSource || stableAudioModeRequiresSource(mode) ? layerNum : null;
    const seedRaw = fieldValue('sa3-seed', '');
    const seed = seedRaw === '' ? null : Number(seedRaw);
    return {
      prompt: promptOverride || stableAudioPrompt(sourceLayer),
      mode,
      duration: numericField('sa3-duration', 8),
      provider: 'local',
      model: 'stable-audio-3-local',
      decoder: 'same-s',
      local_provider: fieldValue('sa3-provider', 'stable_audio_mlx'),
      local_model: fieldValue('sa3-model', 'sm-music'),
      service_url: fieldValue('sa3-service-url', 'http://127.0.0.1:8765'),
      chunked_decode: true,
      source_layer: sourceLayer,
      target_layer: 'first_empty',
      assign_layer: true,
      tags: ['stable-audio', `mode:${mode}`, sourceLayer ? 'workflow:audio-to-audio' : 'workflow:text-to-audio'],
      negative_prompt: 'voice, speech, vocals',
      seed: Number.isFinite(seed) ? seed : null,
      steps: numericField('sa3-steps', 8),
      cfg_scale: numericField('sa3-cfg', 1),
      noise_depth: mode === 'generate' ? null : numericField('sa3-noise', 0.55),
      variation_count: 1,
    };
  }

  async function renderLocalStableAudio(layerNum, promptOverride, options = {}) {
    const payload = stableAudioPayload(layerNum, promptOverride, options);
    const workflow = payload.source_layer ? 'audio-to-audio' : 'text-to-audio';
    const origin = payload.source_layer ? ` from layer ${payload.source_layer}` : '';
    addLog(`local render ${workflow} ${payload.mode}${origin}…`, 'generated', '✦');
    const res = await apiPost('/api/stable-audio/render', payload);
    if (res && res.status === 'ok') {
      addLog(`local render → layer ${res.layer || 'library'} via ${res.engine || 'stable-audio-3-local'}`, 'generated', '✦');
      return true;
    }
    addLog('local render failed: ' + (res?.message || 'unknown error'), 'error', '✕');
    return false;
  }

  function updateLayerBadge(layerIndex) {
    const badge = document.getElementById('header-layer-indicator');
    if (!badge) return;
    const idx = Number.isFinite(layerIndex) ? Math.max(0, Math.min(3, layerIndex)) : 0;
    badge.textContent = String(idx + 1);
    badge.dataset.layer = String(idx);
  }

  function linearToDb(linear) {
    if (linear <= 0.00001) return -Infinity;
    return 20 * Math.log10(linear);
  }

  function formatDb(db) {
    if (!isFinite(db)) return '-∞';
    return db.toFixed(1);
  }

  function render(s) {
    // update sa3 service url setting from server config
    const sa3ServiceUrlInput = document.getElementById('sa3-service-url');
    if (sa3ServiceUrlInput && s.stable_audio_service_url && !sa3ServiceUrlInput._userEdited) {
      if (sa3ServiceUrlInput.value !== s.stable_audio_service_url) {
        sa3ServiceUrlInput.value = s.stable_audio_service_url;
      }
    }

    // auto-mode indicator — sync with server state
    const btnAutoMode = document.getElementById('btn-auto-mode');
    if (btnAutoMode) {
      btnAutoMode.classList.toggle('active', !!s.auto_listen);
      btnAutoMode.setAttribute('aria-pressed', String(!!s.auto_listen));
    }

    // record button
    btnRecord.classList.toggle('active', !!s.recording);

    // prompt module
    const promptFrame = document.getElementById('prompt-frame');
    if (promptFrame) promptFrame.classList.toggle('recording', !!s.recording);
    const selIdx = s.selected_layer != null ? s.selected_layer : 0;
    updateLayerBadge(selIdx);

    // meters — smoothed with pro ballistics
    const rawIn = s.input_level || 0;
    const rawOut = s.output_level || 0;

    smoothedIn = rawIn > smoothedIn ? rawIn * 0.8 + smoothedIn * 0.2 : rawIn * 0.25 + smoothedIn * 0.75;
    smoothedOut = rawOut > smoothedOut ? rawOut * 0.8 + smoothedOut * 0.2 : rawOut * 0.25 + smoothedOut * 0.75;

    const inDb = linearToDb(smoothedIn);
    const outDb = linearToDb(smoothedOut);

    const inPct = Math.max(0, Math.min(100, ((inDb + 60) / 60) * 100));
    const outPct = Math.max(0, Math.min(100, ((outDb + 60) / 60) * 100));

    // meter dots — classify level into off/low/mid/hot
    function dotClass(db) {
      if (db <= -60) return 'meter-dot';
      if (db > -3) return 'meter-dot level-hot';
      if (db > -18) return 'meter-dot level-mid';
      return 'meter-dot level-low';
    }
    if (meterDotIn) meterDotIn.className = dotClass(inDb) + ' meter-dot-in';
    if (meterDotOut) meterDotOut.className = dotClass(outDb) + ' meter-dot-out';

    // layers
    if (s.layers) {
      s.layers.forEach((layer, i) => {
        renderLayer(i, layer, s.selected_layer);
      });

      // auto-reveal layer 4 if it has content
      if (s.layers.length > 3 && s.layers[3].state !== 'empty') {
        const extraLayer = document.getElementById('layer-3');
        const addBtn = document.getElementById('btn-add-layer');
        if (extraLayer && !extraLayer.classList.contains('layer-revealed')) {
          extraLayer.style.display = '';
          extraLayer.classList.add('layer-revealed');
          if (addBtn) addBtn.classList.add('layer-added');
        }
      }
    }

    // server log sync
    if (s.log && s.log.length > 0) {
      syncServerLog(s.log);
    }
  }

  // log dedup — §3.4: LRU Map capped at 200
  const _seenServerMsgs = new Map();
  const _SEEN_CAP = 200;

  function syncServerLog(serverLog) {
    serverLog.forEach(msg => {
      const key = msg.substring(0, 80);
      if (_seenServerMsgs.has(key)) return;
      _seenServerMsgs.set(key, true);
      // evict oldest when cap reached
      if (_seenServerMsgs.size > _SEEN_CAP) {
        const oldest = _seenServerMsgs.keys().next().value;
        _seenServerMsgs.delete(oldest);
      }

      const cls = classifyLog(msg);
      addLogDirect(msg, cls.type, cls.icon);
    });
  }

  function classifyLog(msg) {
    const m = msg.toLowerCase();
    if (m.includes('oram hears:')) return { type: 'listen', icon: '👂' };
    if (m.includes('engine:') || m.includes('prompt:')) return { type: 'agent', icon: '✎' };
    if (m.includes('generated') && m.includes('→')) return { type: 'generated', icon: '✦' };
    if (m.includes('exported')) return { type: 'export', icon: '↓' };
    if (m.includes('error') || m.includes('failed')) return { type: 'error', icon: '✕' };
    if (m.includes('listening') || m.includes('analyzing')) return { type: 'listen', icon: '◉' };
    if (m.includes('recording started') || m.includes('recorded layer')) return { type: 'record', icon: '●' };
    if (m.includes('cleared')) return { type: 'system', icon: '⌫' };
    if (m.includes('gateway:') || m.includes('audio:') || m.includes('llm:')) return { type: 'system', icon: '◆' };
    if (m.includes('ready')) return { type: 'system', icon: '▸' };
    if (m.includes('settings:') || m.includes('found')) return { type: 'system', icon: '⚙' };
    if (m.includes('auto-generate') || m.includes('generating')) return { type: 'generated', icon: '✦' };
    return { type: 'system', icon: '·' };
  }

  function renderLayer(index, layer, selectedIndex) {
    const row = document.getElementById('layer-' + index);
    if (!row) return;

    row.classList.toggle('selected', index === selectedIndex);
    row.classList.toggle('recording', layer.state === 'recording');

    // (mode tag and mode-btn removed in v3.1 — per-layer mode controls dropped)

    // duration
    const durEl = row.querySelector('.layer-dur');
    durEl.textContent = layer.duration > 0 ? layer.duration.toFixed(1) + 's' : '';

    // state tag — only show non-default states (mute/solo are shown on the number button)
    const tagEl = row.querySelector('.layer-state-tag');
    let stateText = '';
    if (layer.state === 'recording') stateText = 'rec';
    else if (layer.is_generated) stateText = 'gen d' + (layer.generation_depth || 0);
    tagEl.textContent = stateText;
    tagEl.className = 'layer-state-tag ' + (layer.is_generated ? 'generated' : (stateText || 'idle'));

    // loop readout
    const loopReadout = row.querySelector('.loop-readout-inline');
    if (loopReadout) {
      if (layer.loop_enabled && layer.state !== 'empty') {
        const s = (layer.loop_start_seconds || 0).toFixed(2);
        const e = (layer.loop_end_seconds || 0).toFixed(2);
        loopReadout.textContent = s + '–' + e;
      } else {
        loopReadout.textContent = '';
      }
    }

    // effect chips
    const chipContainer = row.querySelector('.effect-chips');
    if (chipContainer && layer.effects) {
      chipContainer.innerHTML = '';
      layer.effects.forEach(fx => {
        const chip = document.createElement('span');
        chip.className = 'effect-chip';
        chip.textContent = fx;
        chipContainer.appendChild(chip);
      });
    }

    // tooltip
    const infoParts = [];
    if (layer.parent_layer_id) infoParts.push('← ' + layer.parent_layer_id.slice(-4));
    if (layer.effects && layer.effects.length > 0) infoParts.push(layer.effects.join(' · '));
    row.title = infoParts.join('  ') || '';

    // mute/solo/empty states reflected on the top-left corner (layer identity)
    const cornerTl = row.querySelector('.corner-tl');
    if (cornerTl) {
      cornerTl.classList.toggle('muted', !!layer.muted);
      cornerTl.classList.toggle('solo', !!layer.solo);
      cornerTl.classList.toggle('is-empty', layer.state === 'empty');
      cornerTl.setAttribute('aria-pressed', !!layer.muted);
    }

    // volume strip — vertical fill column (skip updates while dragging OR during post-drag cooldown)
    const volStrip = row.querySelector('.vol-strip');
    if (volStrip && !volStrip._dragging && !volStrip._cooldown) {
      const rawVol = Math.round(volumeToStripPos(layerVolume(layer)));
      volStrip.dataset.value = rawVol;
      updateVolStripVisual(volStrip);
    }

    // loop selection overlay — non-destructive, persists when loop is active
    const shell = row.querySelector('.waveform-shell');
    if (shell) {
      const isEmpty = layer.state === 'empty';
      shell.classList.toggle('empty', isEmpty);

      // Check optimistic loop state
      let loopEnabled = !!layer.loop_enabled && !isEmpty;
      let startPct = layer.loop_start_pct || 0;
      let endPct = layer.loop_end_pct || 100;

      if (shell._optimisticLoop) {
        if (Date.now() - shell._optimisticLoop.timestamp < 1000) {
          // If the server state has updated and now matches the optimistic state, clear the lock
          const matches = (!!layer.loop_enabled === shell._optimisticLoop.enabled) &&
            (!shell._optimisticLoop.enabled || (
              Math.abs((layer.loop_start_pct || 0) - shell._optimisticLoop.startPct) < 1 &&
              Math.abs((layer.loop_end_pct || 100) - shell._optimisticLoop.endPct) < 1
            ));
          if (matches) {
            delete shell._optimisticLoop;
          } else {
            // Use optimistic state
            loopEnabled = shell._optimisticLoop.enabled && !isEmpty;
            startPct = shell._optimisticLoop.startPct;
            endPct = shell._optimisticLoop.endPct;
          }
        } else {
          // Timeout reached, discard optimistic lock
          delete shell._optimisticLoop;
        }
      }

      shell.classList.toggle('loop-active', loopEnabled);

      // skip while user is dragging — UI handles its own overlay
      if (!shell._selecting) {
        const overlay = shell.querySelector('.loop-selection');
        if (overlay) {
          if (!isEmpty && loopEnabled) {
            const left = Math.max(0, Math.min(100, startPct));
            const right = Math.max(0, Math.min(100, endPct));
            overlay.style.left = left + '%';
            overlay.style.width = Math.max(0, right - left) + '%';
          } else {
            overlay.style.width = '0';
          }
        }
      }
      const readout = shell.querySelector('.loop-readout');
      if (readout) {
        if (!isEmpty && loopEnabled) {
          const dur = layer.duration || 0;
          const sSec = (startPct / 100) * dur;
          const eSec = (endPct / 100) * dur;
          readout.textContent = sSec.toFixed(2) + '–' + eSec.toFixed(2);
        } else {
          readout.textContent = '';
        }
      }

      // DOM playhead — smooth interpolation, skips transition on backwards jumps
      const playhead = shell.querySelector('.playhead');
      if (playhead) {
        const newPct = Math.max(0, Math.min(100, layer.playhead_pct || 0));
        const isPlaying = !isEmpty && newPct > 0;
        shell.classList.toggle('has-audio', isPlaying);
        const oldPct = parseFloat(playhead.dataset.pct || '0');
        // a backwards jump (loop wrap / seek / reset) shouldn't animate
        if (Math.abs(newPct - oldPct) > 8 || newPct < oldPct - 1.5) {
          shell.classList.add('playhead-jump');
          playhead.style.left = newPct + '%';
          // force reflow so the transition reset takes effect before next tick
          void playhead.offsetWidth;
          shell.classList.remove('playhead-jump');
        } else {
          playhead.style.left = newPct + '%';
        }
        playhead.dataset.pct = String(newPct);
      }
    }

    // waveform — playhead is now a DOM overlay, so canvas redraws only on real changes
    const canvas = row.querySelector('.waveform-canvas');
    const cachedWaveform = getCachedWaveform(layer, canvas);
    drawWaveform(canvas, layer, cachedWaveform);
    ensureWaveform(layer, canvas);
  }

  function desiredWaveformPoints(canvas) {
    const rect = canvas?.parentElement?.getBoundingClientRect();
    const cssW = rect?.width || 320;
    const dpr = window.devicePixelRatio || 1;
    return Math.max(256, Math.min(1024, Math.round(cssW * dpr)));
  }

  function waveformCacheKey(layer, points) {
    return `${layer.id}:${layer.waveform_revision}:${points}`;
  }

  function trimWaveformCache() {
    while (waveformCache.size > WAVEFORM_CACHE_CAP) {
      waveformCache.delete(waveformCache.keys().next().value);
    }
  }

  function getCachedWaveform(layer, canvas) {
    if (!layer || layer.state === 'empty' || !canvas) return null;
    const points = desiredWaveformPoints(canvas);
    return waveformCache.get(waveformCacheKey(layer, points)) || null;
  }

  async function ensureWaveform(layer, canvas) {
    if (!layer || layer.state === 'empty' || !canvas) return;
    const points = desiredWaveformPoints(canvas);
    const key = waveformCacheKey(layer, points);
    if (waveformCache.has(key) || waveformPending.has(key)) return;

    const request = apiGet(`/api/waveform/${layer.slot}?points=${points}`)
      .then(data => {
        waveformPending.delete(key);
        if (!data || data.error || data.revision !== layer.waveform_revision) return;
        waveformCache.set(key, data);
        trimWaveformCache();
        drawWaveform(canvas, layer, data);
      })
      .catch(() => {
        waveformPending.delete(key);
      });
    waveformPending.set(key, request);
  }

  function invalidateWaveforms() {
    document.querySelectorAll('.waveform-canvas').forEach(canvas => {
      canvas._waveformCache = '';
    });
  }

  function drawWaveform(canvas, layer, hdData) {
    if (!canvas) return;
    const data = layer.waveform || [];
    const layerState = layer.state;
    const isGenerated = layer.is_generated;
    const isMuted = layer.muted;
    const loopEnabled = layer.loop_enabled;
    const loopStartPct = layer.loop_start_pct;
    const loopEndPct = layer.loop_end_pct;

    const ctx = canvas.getContext('2d');
    const rect = canvas.parentElement ? canvas.parentElement.getBoundingClientRect() : canvas.getBoundingClientRect();
    const cssW = Math.round(rect.width) || 320;
    const cssH = Math.round(rect.height) || 48;
    const dpr = window.devicePixelRatio || 1;
    const themeKey = document.documentElement.getAttribute('data-theme') || 'dark';

    // playhead is rendered as a DOM overlay (not the canvas), so the cache key
    // depends only on waveform appearance — we skip redraws unless a real change happened.
    const dataKey = (data && data.length > 0) ? data.join(',') : '';
    const hdKey = hdData ? `${hdData.revision}:${hdData.points}` : 'preview';
    const stateKey = `${layerState}|${isGenerated}|${isMuted}|${loopEnabled}|${loopStartPct}|${loopEndPct}`;
    const cacheKey = `${cssW}x${cssH}|${themeKey}|${hdKey}|${dataKey}|${stateKey}`;
    if (canvas._waveformCache === cacheKey) {
      return;
    }
    canvas._waveformCache = cacheKey;

    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
      canvas.style.width = cssW + 'px';
      canvas.style.height = cssH + 'px';
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const w = cssW;
    const h = cssH;
    const cs = getComputedStyle(document.documentElement);

    if (!data || data.length === 0 || data.every(v => v === 0)) {
      ctx.strokeStyle = cs.getPropertyValue('--border').trim() || '#273036';
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(0, h / 2);
      ctx.lineTo(w, h / 2);
      ctx.stroke();
      ctx.setLineDash([]);
      return;
    }

    // loop region shading
    if (loopEnabled && loopStartPct != null && loopEndPct != null) {
      const lx1 = (loopStartPct / 100) * w;
      const lx2 = (loopEndPct / 100) * w;
      ctx.fillStyle = cs.getPropertyValue('--loop-region').trim() || 'rgba(0,229,255,0.12)';
      ctx.fillRect(lx1, 0, lx2 - lx1, h);
    }

    let color = cs.getPropertyValue('--waveform').trim() || '#4f6b78';
    if (layerState === 'active' && !isMuted) color = cs.getPropertyValue('--waveform-active').trim() || '#78dcff';
    if (layerState === 'muted' || isMuted) color = cs.getPropertyValue('--waveform-muted').trim() || '#2a3135';
    if (isGenerated) color = cs.getPropertyValue('--waveform-generated').trim() || '#26e6a2';

    ctx.strokeStyle = cs.getPropertyValue('--border').trim() || '#273036';
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();

    for (let i = 1; i < 4; i++) {
      const x = (i / 4) * w;
      ctx.globalAlpha = 0.18;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }

    if (hdData && hdData.peaks && hdData.peaks.length > 0) {
      const peaks = hdData.peaks;
      const maxAbs = Math.max(0.001, ...peaks.flat().map(v => Math.abs(v)));
      const amp = (h / 2 - 3) / maxAbs;
      const step = w / Math.max(1, peaks.length - 1);
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.92;
      ctx.lineWidth = Math.max(1, 1 / dpr);
      ctx.beginPath();
      peaks.forEach((pair, i) => {
        const x = i * step;
        const yMin = h / 2 - pair[1] * amp;
        const yMax = h / 2 - pair[0] * amp;
        ctx.moveTo(x, yMin);
        ctx.lineTo(x, yMax);
      });
      ctx.stroke();
    } else {
      const barW = w / data.length;
      const maxVal = Math.max(...data, 0.001);
      data.forEach((val, i) => {
        const norm = (val / maxVal);
        const barH = Math.max(1, norm * (h - 4));
        const x = i * barW;
        const yTop = (h - barH) / 2;

        ctx.fillStyle = color;
        ctx.globalAlpha = 0.3 + norm * 0.7;
        ctx.fillRect(x + 0.5, yTop, Math.max(1, barW - 1), barH);
      });
    }

    ctx.globalAlpha = 1;
    // playhead lives in a separate DOM element (.playhead) for smooth CSS interpolation
  }

  // ── log ──
  function addLog(msg, type, icon) {
    addLogDirect(msg, type || 'system', icon || '·');
  }

  function addLogDirect(msg, type, icon) {
    const logEl = document.getElementById('log-lines');
    const div = document.createElement('div');
    div.className = 'log-line ' + (type || 'system');

    const timeSpan = document.createElement('span');
    timeSpan.className = 'log-time';
    const ts = timeNow();
    timeSpan.textContent = ts;

    const iconSpan = document.createElement('span');
    iconSpan.className = 'log-icon';
    iconSpan.textContent = icon || '·';

    const textSpan = document.createElement('span');
    textSpan.className = 'log-text';
    textSpan.textContent = msg;

    div.appendChild(timeSpan);
    div.appendChild(iconSpan);
    div.appendChild(textSpan);
    logEl.appendChild(div);

    while (logEl.children.length > 40) logEl.removeChild(logEl.firstChild);
    logEl.scrollTop = logEl.scrollHeight;

    // mirror the latest event onto the collapsed status line
    updateLogStatus(ts, icon || '·', msg, type || 'system');
  }

  function updateLogStatus(ts, icon, msg, type) {
    const sBtn = document.getElementById('log-status');
    const sTime = document.getElementById('log-status-time');
    const sIcon = document.getElementById('log-status-icon');
    const sText = document.getElementById('log-status-text');
    if (!sBtn || !sText) return;
    if (sTime) sTime.textContent = ts;
    if (sIcon) sIcon.textContent = icon;
    sText.textContent = msg;
    // reset type classes, add the current one
    sBtn.classList.remove('type-system', 'type-agent', 'type-listen', 'type-generated', 'type-error', 'type-record', 'type-export');
    sBtn.classList.add('type-' + type);
  }

  // ── events ──

  // command input
  const cmdInput = document.getElementById('cmd-input');
  cmdInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      if (localStableAudioEnabled()) {
        const sel = (state.selected_layer || 0) + 1;
        renderLocalStableAudio(sel, cmdInput.value.trim(), { mode: 'generate' });
      } else {
        sendCommand(cmdInput.value);
      }
      cmdInput.value = '';
    }
  });

  // auto-mode toggle button
  const btnAutoMode = document.getElementById('btn-auto-mode');
  if (btnAutoMode) {
    btnAutoMode.addEventListener('click', async () => {
      const res = await apiPost('/api/auto-listen');
      if (res && res.status === 'ok') {
        const isOn = res.auto_listen;
        btnAutoMode.classList.toggle('active', isOn);
        btnAutoMode.setAttribute('aria-pressed', String(isOn));
        addLog('auto mode: ' + (isOn ? 'on — recording will auto-generate' : 'off'), 'system', '⚙');
      }
    });
  }

  // theme icon toggle — single button cycles dark ↔ light
  const btnThemeToggle = document.getElementById('btn-theme-toggle');
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    if (btnThemeToggle) {
      btnThemeToggle.textContent = theme === 'light' ? '☼' : '☾';
      btnThemeToggle.setAttribute('aria-pressed', theme === 'light');
    }
    try { localStorage.setItem('oram-theme', theme); } catch (_) {}
    invalidateWaveforms();
  }
  // restore on load
  try {
    const savedTheme = localStorage.getItem('oram-theme');
    if (savedTheme === 'light' || savedTheme === 'dark') applyTheme(savedTheme);
    else applyTheme('dark');
  } catch (_) { applyTheme('dark'); }
  if (btnThemeToggle) {
    btnThemeToggle.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      applyTheme(cur === 'dark' ? 'light' : 'dark');
    });
  }

  // ORAM title → about modal
  const aboutOverlay = document.getElementById('about-overlay');
  const aboutClose = document.getElementById('about-close');
  const oramTitle = document.getElementById('oram-title');
  function openAbout() {
    if (!aboutOverlay) return;
    aboutOverlay.classList.remove('hidden');
    aboutOverlay.setAttribute('aria-hidden', 'false');
  }
  function closeAbout() {
    if (!aboutOverlay) return;
    aboutOverlay.classList.add('hidden');
    aboutOverlay.setAttribute('aria-hidden', 'true');
  }
  if (oramTitle) oramTitle.addEventListener('click', openAbout);
  if (aboutClose) aboutClose.addEventListener('click', closeAbout);
  if (aboutOverlay) {
    aboutOverlay.addEventListener('click', (e) => {
      if (e.target === aboutOverlay) closeAbout();
    });
  }

  // ── settings panel ──
  const settingsPanel = document.getElementById('settings-panel');
  const btnSettings = document.getElementById('btn-settings');
  const settingsClose = document.getElementById('settings-close');

  if (btnSettings) {
    btnSettings.addEventListener('click', async () => {
      const isHidden = settingsPanel.classList.contains('hidden');
      settingsPanel.classList.toggle('hidden');
      if (isHidden) {
        await loadDevices();
      }
    });
  }
  if (settingsClose) {
    settingsClose.addEventListener('click', () => {
      settingsPanel.classList.add('hidden');
    });
  }

  async function loadDevices() {
    const data = await apiGet('/api/devices');
    if (!data) return;

    const inputDevices = data.devices.filter(d => d.is_input);
    const outputDevices = data.devices.filter(d => d.is_output);
    populateDeviceSelect({
      select: document.getElementById('sel-input-device'),
      devices: inputDevices,
      currentId: data.current_input,
      defaultId: data.default_input,
      emptyLabel: 'system default input',
      direction: 'input',
    });
    populateDeviceSelect({
      select: document.getElementById('sel-output-device'),
      devices: outputDevices,
      currentId: data.current_output,
      defaultId: data.default_output,
      emptyLabel: 'system default output',
      direction: 'output',
    });

    const srSel = document.getElementById('sel-sample-rate');
    if (srSel && data.current_sample_rate) {
      srSel.value = data.current_sample_rate.toString();
    }

    addLog(`${inputDevices.length} input / ${outputDevices.length} output device(s) available`, 'system', '⚙');
  }

  function populateDeviceSelect({ select, devices, currentId, defaultId, emptyLabel, direction }) {
    if (!select) return;
    select.innerHTML = '';

    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = emptyLabel;
    select.appendChild(defaultOpt);

    devices.forEach(dev => {
      const opt = document.createElement('option');
      opt.value = dev.id;
      const channels = direction === 'input' ? dev.max_input_channels : dev.max_output_channels;
      const defaultMarker = dev.id === defaultId ? ' · default' : '';
      opt.textContent = `${dev.name} (${channels} ch · ${Math.round(dev.default_samplerate)} Hz${defaultMarker})`;
      if (dev.id === currentId || (currentId === null && dev.id === defaultId)) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  }

  function selectedDeviceValue(id) {
    const el = document.getElementById(id);
    if (!el || el.value === '') return null;
    const value = parseInt(el.value, 10);
    return Number.isNaN(value) ? null : value;
  }

  // apply settings
  const btnApply = document.getElementById('btn-apply-settings');
  if (btnApply) {
    btnApply.addEventListener('click', async () => {
      const settings = {
        input_device: selectedDeviceValue('sel-input-device'),
        output_device: selectedDeviceValue('sel-output-device'),
        sample_rate: parseInt(document.getElementById('sel-sample-rate').value),
        bit_depth: parseInt(document.getElementById('sel-bit-depth').value),
        rec_format: document.getElementById('sel-format').value,
      };
      const res = await apiPost('/api/settings', settings);
      if (res && res.changes) {
        res.changes.forEach(c => addLog(c, 'system', '⚙'));
      }
    });
  }

  // optimistic layer selection — update UI instantly before server confirms
  function _optimisticSelectLayer(layerIndex) {
    // update header badge
    updateLayerBadge(layerIndex);

    // update row highlights
    document.querySelectorAll('.layer-row').forEach((row, i) => {
      row.classList.toggle('selected', i === layerIndex);
    });

    // update local state so subsequent clicks reference the right layer
    if (state) state.selected_layer = layerIndex;
  }

  // layer controls (delegation)
  document.getElementById('layers').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (btn) {
      const action = btn.dataset.action;
      const target = parseInt(btn.dataset.target);

      switch (action) {
        case 'select': {
          // shift+click solos (alt-path to right-click)
          if (e.shiftKey) {
            apiPost('/api/command', { text: 'solo layer ' + target });
            break;
          }
          // click cycles: not-selected → select; already-selected → toggle mute
          // (only mute once we've received initial state and the layer has audio)
          const hasState = Array.isArray(state.layers);
          const currentSelected = hasState ? (state.selected_layer || 0) + 1 : null;
          const tgtLayer = hasState ? state.layers[target - 1] : null;
          const isAlreadySelected = currentSelected === target;
          const hasAudio = tgtLayer && tgtLayer.state !== 'empty';
          if (isAlreadySelected && hasAudio) {
            apiPost('/api/command', { text: 'mute layer ' + target });
          } else {
            // optimistic UI update for header badge
            _optimisticSelectLayer(target - 1);
            apiPost('/api/command', { text: 'select layer ' + target });
          }
          break;
        }
        case 'export':
          exportLayer(target);
          break;
        case 'clear':
          clearLayer(target);
          break;
        case 'auto-gen':
          autoGenerate(target);
          break;
        case 'upload':
          openLayerUpload(target);
          break;
      }
      return;
    }

    // click on empty space of a layer row should select it
    const row = e.target.closest('.layer-row');
    const isVolStrip = e.target.closest('.vol-strip');
    const isCorner = e.target.closest('.corner');
    if (row && !isVolStrip && !isCorner) {
      const layerIndex = parseInt(row.dataset.layer);
      const targetNum = layerIndex + 1;
      // optimistic UI update for header badge
      _optimisticSelectLayer(layerIndex);
      apiPost('/api/command', { text: 'select layer ' + targetNum });
    }
  });

  const uploadInput = document.getElementById('layer-upload-input');
  let pendingUploadTarget = 1;

  function openLayerUpload(target) {
    pendingUploadTarget = target;
    if (!uploadInput) return;
    uploadInput.value = '';
    uploadInput.click();
  }

  async function uploadFileToLayer(target, file) {
    if (!file) return;
    addLog(`uploading ${file.name || 'audio'} → layer ${target}…`, 'system', '↑');
    const res = await apiUploadLayer(target, file);
    if (res && res.status === 'ok') {
      _optimisticSelectLayer(target - 1);
      addLog(`layer ${target} ← ${res.filename || file.name} (${res.duration || '?'}s)`, 'system', '↑');
      return;
    }
    addLog('upload failed: ' + (res?.message || 'unknown error'), 'error', '✕');
  }

  if (uploadInput) {
    uploadInput.addEventListener('change', () => {
      const file = uploadInput.files && uploadInput.files[0];
      uploadFileToLayer(pendingUploadTarget, file);
    });
  }

  const layersEl = document.getElementById('layers');
  if (layersEl) {
    layersEl.addEventListener('dragover', (e) => {
      const row = e.target.closest('.layer-row');
      if (!row) return;
      e.preventDefault();
      row.classList.add('drag-over');
      e.dataTransfer.dropEffect = 'copy';
    });

    layersEl.addEventListener('dragleave', (e) => {
      const row = e.target.closest('.layer-row');
      if (!row || row.contains(e.relatedTarget)) return;
      row.classList.remove('drag-over');
    });

    layersEl.addEventListener('drop', (e) => {
      const row = e.target.closest('.layer-row');
      if (!row) return;
      e.preventDefault();
      row.classList.remove('drag-over');
      const file = e.dataTransfer.files && e.dataTransfer.files[0];
      const target = parseInt(row.dataset.layer) + 1;
      uploadFileToLayer(target, file);
    });
  }

  // right-click on TL corner → toggle solo
  document.getElementById('layers').addEventListener('contextmenu', (e) => {
    const btn = e.target.closest('.corner-tl[data-action="select"]');
    if (!btn) return;
    e.preventDefault();
    const target = parseInt(btn.dataset.target);
    apiPost('/api/command', { text: 'solo layer ' + target });
  });

  // ── auto-generate: listen to what's sounding → generate into this layer ──
  async function autoGenerate(layerNum) {
    const btn = document.querySelector(`[data-action="auto-gen"][data-target="${layerNum}"]`);
    if (btn) btn.classList.add('generating');

    if (localStableAudioEnabled()) {
      await renderLocalStableAudio(layerNum, undefined, { forceSource: true });
      if (btn) btn.classList.remove('generating');
      return;
    }

    const engineSel = document.getElementById('engine-selector');
    const selectedEngine = engineSel ? engineSel.value : 'auto';
    if (!selectedApiEngineReady()) {
      if (btn) btn.classList.remove('generating');
      return;
    }

    addLog(`generating into layer ${layerNum} via ${selectedEngine}…`, 'generated', '✦');

    const res = await apiPost('/api/generate', {
      target: layerNum,
      route: 'hybrid',
      engine: selectedEngine,
    });

    if (btn) btn.classList.remove('generating');

    if (res && res.status === 'ok') {
      const engineUsed = res.engine_used || selectedEngine;
      addLog(`layer ${layerNum} ← generated via ${engineUsed}`, 'generated', '✦');
    } else {
      addLog('generate failed: ' + (res?.message || 'unknown error'), 'error', '✕');
    }
  }

  // ── clear layer (confirmed) ──
  async function clearLayer(layerNum) {
    addLog('clearing layer ' + layerNum + '…', 'system', '⌫');
    const res = await apiPost('/api/clear-layer', { target: layerNum });
    if (res && res.status === 'ok') {
      addLog('layer ' + layerNum + ' cleared', 'system', '⌫');
    } else {
      addLog('clear failed: ' + (res?.message || 'unknown'), 'error', '✕');
    }
  }

  // ── export layer ──
  async function exportLayer(layerNum) {
    addLog('exporting layer ' + layerNum + '…', 'export', '↓');
    const res = await apiPost('/api/export-layer', { target: layerNum });
    if (res && res.status === 'ok') {
      addLog('layer ' + layerNum + ' → ' + (res.filename || 'exported'), 'export', '↓');
    } else {
      addLog('export: ' + (res?.message || 'failed'), 'error', '✕');
    }
  }

  // submit button
  const btnSubmit = document.getElementById('btn-submit-command');
  if (btnSubmit) {
    btnSubmit.addEventListener('click', () => {
      if (localStableAudioEnabled()) {
        const sel = (state.selected_layer || 0) + 1;
        renderLocalStableAudio(sel, cmdInput.value.trim(), { mode: 'generate' });
      } else {
        sendCommand(cmdInput.value);
      }
      cmdInput.value = '';
    });
  }

  // ── volume strips — vertical column with exponential curve ──

  // exponential curve: knob position 0-200 → volume 0-200%
  // gives much more resolution at low volumes for quiet layers
  // pos 0 → vol 0 (true mute), pos ~136 → vol 1.0 (unity), pos 200 → vol 2.0
  function stripPosToVolume(pos) {
    if (pos <= 0) return 0;              // true mute at zero
    const norm = pos / 200;              // 0..1
    const curved = Math.pow(norm, 1.8);  // exponential — low end gets ~3× more resolution
    return curved * 2;                   // 0..2 range
  }

  // inverse: volume (0-2) → strip position (0-200)
  function volumeToStripPos(vol) {
    if (vol <= 0) return 0;
    const norm = vol / 2;                // 0..1
    const uncurved = Math.pow(norm, 1 / 1.8);
    return Math.round(uncurved * 200);
  }

  function layerVolume(layer) {
    const vol = Number(layer?.volume);
    return Number.isFinite(vol) ? Math.max(0, Math.min(2, vol)) : 1;
  }

  function stripValue(strip, fallback = 100) {
    const val = parseInt(strip?.dataset?.value ?? '', 10);
    return Number.isFinite(val) ? Math.max(0, Math.min(200, val)) : fallback;
  }

  function formatVolume(vol) {
    const clamped = Math.max(0, Math.min(2, vol));
    return clamped < 0.1 ? clamped.toFixed(3) : clamped.toFixed(2);
  }

  function volumeLabel(vol) {
    if (vol <= 0) return 'mute';
    if (vol < 0.01) return Math.max(0.1, Math.round(vol * 1000) / 10) + '%';
    return Math.round(vol * 100) + '%';
  }

  function updateVolStripVisual(strip) {
    const val = stripValue(strip);
    const pct = Math.max(0, Math.min(100, (val / 200) * 100));
    const fill = strip.querySelector('.vol-strip-fill');
    if (fill) fill.style.height = pct + '%';
    const vol = stripPosToVolume(val);
    strip.classList.toggle('hot', vol > 1.1);
    strip.classList.toggle('silent', vol <= 0);
    strip.setAttribute('aria-valuenow', String(val));
    strip.setAttribute('aria-valuetext', formatVolume(vol) + '×');
    strip.dataset.display = volumeLabel(vol);
  }

  function commitStripValue(strip) {
    const val = stripValue(strip);
    const vol = formatVolume(stripPosToVolume(val));
    sendCommand('set volume layer ' + strip.dataset.target + ' ' + vol);
  }

  document.querySelectorAll('.vol-strip').forEach(strip => {
    updateVolStripVisual(strip);

    let startY = 0;
    let startVal = 100;
    let showValueTimer = null;

    function showFloatingValue() {
      strip.dataset.showValue = '1';
      clearTimeout(showValueTimer);
      showValueTimer = setTimeout(() => { delete strip.dataset.showValue; }, 900);
    }

    let longPressTimer = null;
    let didLongPress = false;

    function startCooldown() {
      strip._cooldown = true;
      setTimeout(() => { strip._cooldown = false; }, 500);
    }

    strip.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      strip._dragging = true;
      didLongPress = false;
      strip.classList.add('dragging');
      startY = e.clientY;
      startVal = stripValue(strip);
      strip.setPointerCapture(e.pointerId);
      showFloatingValue();

      // long-press → expanded volume overlay
      longPressTimer = setTimeout(() => {
        didLongPress = true;
        strip._dragging = false;
        strip.classList.remove('dragging');
        try { strip.releasePointerCapture(e.pointerId); } catch (_) {}
        openExpandedVolume(strip);
      }, 400);
    });

    strip.addEventListener('pointermove', (e) => {
      if (!strip._dragging) return;
      // cancel long-press if user moves significantly
      if (longPressTimer && Math.abs(e.clientY - startY) > 4) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
      const dy = startY - e.clientY;                   // up = increase
      const sensitivity = e.shiftKey ? 0.5 : 1.6;
      const newVal = Math.max(0, Math.min(200, Math.round(startVal + dy * sensitivity)));
      strip.dataset.value = newVal;
      updateVolStripVisual(strip);
      strip.dataset.showValue = '1';
    });

    strip.addEventListener('pointerup', () => {
      clearTimeout(longPressTimer);
      longPressTimer = null;
      if (didLongPress) { didLongPress = false; return; }
      if (!strip._dragging) return;
      strip._dragging = false;
      strip.classList.remove('dragging');
      commitStripValue(strip);
      startCooldown();  // prevent server bounce-back
      showFloatingValue();
    });

    strip.addEventListener('lostpointercapture', () => {
      clearTimeout(longPressTimer);
      longPressTimer = null;
      if (strip._dragging) {
        strip._dragging = false;
        strip.classList.remove('dragging');
        commitStripValue(strip);
        startCooldown();
      }
    });

    strip.addEventListener('wheel', (e) => {
      e.preventDefault();
      const step = e.shiftKey ? 1 : 4;
      const delta = e.deltaY < 0 ? step : -step;
      const cur = stripValue(strip);
      const newVal = Math.max(0, Math.min(200, cur + delta));
      strip.dataset.value = newVal;
      updateVolStripVisual(strip);
      commitStripValue(strip);
      showFloatingValue();
    }, { passive: false });

    strip.addEventListener('dblclick', () => {
      strip.dataset.value = 100;
      updateVolStripVisual(strip);
      commitStripValue(strip);
      showFloatingValue();
    });

    strip.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 1 : 5;
      const cur = stripValue(strip);
      let newVal = cur;
      if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { newVal = Math.min(200, cur + step); e.preventDefault(); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { newVal = Math.max(0, cur - step); e.preventDefault(); }
      else if (e.key === 'Home') { newVal = 0; e.preventDefault(); }
      else if (e.key === 'End') { newVal = 200; e.preventDefault(); }
      else return;
      strip.dataset.value = newVal;
      updateVolStripVisual(strip);
      commitStripValue(strip);
      showFloatingValue();
    });
  });

  // ── loop handle drag ──
  async function commitLoopRegion(target, startPct, endPct, enabled) {
    const shell = document.querySelector(`.waveform-shell[data-target="${target}"]`);
    if (shell) {
      shell._optimisticLoop = {
        startPct: startPct !== null ? startPct : 0,
        endPct: endPct !== null ? endPct : 100,
        enabled,
        timestamp: Date.now()
      };
    }
    const body = { target: parseInt(target), enabled };
    if (startPct != null) body.start_pct = startPct;
    if (endPct != null) body.end_pct = endPct;
    const res = await apiPost('/api/loop-region', body);
    if (res && res.status === 'error') {
      addLog(res.message || 'loop region rejected', 'error', '✕');
      if (shell) {
        delete shell._optimisticLoop;
      }
    }
    return res;
  }

  // ── loop region: drag-to-select on the waveform (non-destructive) ──
  // drag horizontally to set a loop region; quick tap clears the loop.
  // the underlying audio is never modified — only the playback window changes.
  const DRAG_THRESHOLD_PX = 4;
  document.querySelectorAll('.waveform-shell').forEach(shell => {
    const target = shell.dataset.target;
    const overlay = shell.querySelector('.loop-selection');

    function pctFromEvent(e) {
      const rect = shell.getBoundingClientRect();
      return Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100));
    }
    function setOverlay(startPct, endPct) {
      if (!overlay) return;
      const lo = Math.min(startPct, endPct);
      const hi = Math.max(startPct, endPct);
      overlay.style.left = lo + '%';
      overlay.style.width = Math.max(0, hi - lo) + '%';
    }

    let startX = 0;
    let startPct = 0;
    let currentPct = 0;
    let didDrag = false;

    const row = shell.closest('.layer-row');
    shell.addEventListener('pointerdown', (e) => {
      // skip if the drag started on a corner button (those are their own actions)
      if (e.target.closest && e.target.closest('.corner')) return;
      // ignore if layer is empty
      const layer = state.layers ? state.layers[parseInt(target) - 1] : null;
      if (!layer || layer.state === 'empty') return;
      e.preventDefault();

      // auto-select this layer if not already selected (drag-to-loop implies focus)
      const targetNum = parseInt(target);
      const currentSelected = (state.selected_layer || 0) + 1;
      if (targetNum !== currentSelected) {
        apiPost('/api/command', { text: 'select layer ' + targetNum });
      }

      shell._selecting = true;
      didDrag = false;
      startX = e.clientX;
      startPct = pctFromEvent(e);
      currentPct = startPct;
      shell.setPointerCapture(e.pointerId);
    });

    shell.addEventListener('pointermove', (e) => {
      if (!shell._selecting) return;
      const dx = Math.abs(e.clientX - startX);
      if (!didDrag && dx > DRAG_THRESHOLD_PX) {
        didDrag = true;
        shell.classList.add('selecting');
        row?.classList.add('dragging-loop');
      }
      if (didDrag) {
        currentPct = pctFromEvent(e);
        setOverlay(startPct, currentPct);
      }
    });

    function finalize() {
      if (!shell._selecting) return;
      shell._selecting = false;
      shell.classList.remove('selecting');
      row?.classList.remove('dragging-loop');
      if (didDrag) {
        const lo = Math.min(startPct, currentPct);
        const hi = Math.max(startPct, currentPct);
        if (hi - lo >= 1) {
          // optimistic UI — keep the overlay visible while the server confirms
          shell.classList.add('loop-active');
          commitLoopRegion(target, lo, hi, true);
        } else {
          // negligible drag — treat as tap
          const layer = state.layers ? state.layers[parseInt(target) - 1] : null;
          if (layer && layer.loop_enabled) {
            shell.classList.remove('loop-active');
            commitLoopRegion(target, 0, 100, false);
            setOverlay(0, 0);
          }
        }
      } else {
        // tap on the waveform — if a loop is active, clear it; full audio plays
        const layer = state.layers ? state.layers[parseInt(target) - 1] : null;
        if (layer && layer.loop_enabled) {
          shell.classList.remove('loop-active');
          commitLoopRegion(target, 0, 100, false);
          setOverlay(0, 0);
        }
      }
    }

    shell.addEventListener('pointerup', finalize);
    shell.addEventListener('lostpointercapture', () => {
      if (shell._selecting) {
        shell._selecting = false;
        shell.classList.remove('selecting');
        row?.classList.remove('dragging-loop');
      }
    });
  });

  async function startRecording() {
    if (state.recording) return;
    state.recording = true;
    btnRecord?.classList.add('active');
    const res = await apiPost('/api/record', { duration: null });
    if (res && res.status === 'ok' && res.recording) {
      addLog(res.message || 'recording started', 'record', '●');
      return;
    }
    state.recording = false;
    btnRecord?.classList.remove('active');
    addLog('record failed: ' + (res?.message || 'unknown'), 'error', '✕');
  }

  async function stopRecording() {
    const res = await apiPost('/api/stop');
    state.recording = false;
    btnRecord?.classList.remove('active');
    if (res && res.status === 'ok') {
      addLog(res.message || 'recording stopped', 'system', '⏹');
    } else {
      addLog('stop failed: ' + (res?.message || 'unknown'), 'error', '✕');
    }
  }

  // transport buttons
  document.getElementById('btn-record').addEventListener('click', async () => {
    if (state.recording) await stopRecording();
    else await startRecording();
  });



  // kill all sound
  document.getElementById('btn-kill').addEventListener('click', async () => {
    addLog('killing all audio…', 'error', '⊘');
    const res = await apiPost('/api/kill');
    if (res) addLog(res.message, 'error', '⊘');
  });

  // master record — Option B: export current mix (no start/stop recording)
  const btnMasterRec = document.getElementById('btn-master-rec');
  btnMasterRec.addEventListener('click', async () => {
    addLog('exporting current mix…', 'export', '◉');
    const res = await apiPost('/api/export-master');
    if (res && res.status === 'ok') {
      addLog('mix exported → ' + (res.path || 'mix.wav'), 'generated', '✦');
    } else {
      addLog('export failed: ' + (res?.message || 'unknown'), 'error', '✕');
    }
  });

  // master export
  const btnMasterExport = document.getElementById('btn-master-export');
  if (btnMasterExport) {
    btnMasterExport.addEventListener('click', async () => {
      addLog('exporting master mix…', 'export', '⬡');
      const res = await apiPost('/api/export-master');
      if (res && res.status === 'ok') {
        addLog('master exported → ' + (res.path || 'mix.wav'), 'generated', '✦');
      } else {
        addLog('export failed: ' + (res?.message || 'unknown'), 'error', '✕');
      }
    });
  }

  // add layer (+) — reveals the hidden 4th layer
  const btnAddLayer = document.getElementById('btn-add-layer');
  if (btnAddLayer) {
    btnAddLayer.addEventListener('click', () => {
      const extraLayer = document.getElementById('layer-3');
      if (extraLayer && !extraLayer.classList.contains('layer-revealed')) {
        extraLayer.style.display = '';
        extraLayer.classList.add('layer-revealed');
        btnAddLayer.classList.add('layer-added');
        addLog('layer 4 added', 'system', '+');
      }
    });
  }

  // other transport — DSP effects moved to fx-palette popover
  const transportCommands = {
    'btn-overdub': 'overdub',
  };

  Object.entries(transportCommands).forEach(([id, cmd]) => {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener('click', () => sendCommand(cmd));
  });

  const fxPalette = document.getElementById('fx-palette');
  const btnFx = document.getElementById('btn-fx');

  const mixerPanel = document.getElementById('mixer-panel');
  const btnMixer = document.getElementById('btn-mixer');

  function closeAllPalettes() {
    fxPalette?.classList.add('hidden');
    mixerPanel?.classList.add('hidden');
    btnFx?.setAttribute('aria-expanded', 'false');
    btnMixer?.setAttribute('aria-expanded', 'false');
  }

  // summon button → directly listen to what's sounding and generate
  document.getElementById('btn-summon').addEventListener('click', async () => {
    const sel = (state.selected_layer || 0) + 1;
    addLog(`summoning: listening to layer ${sel} and generating…`, 'generated', '✦');
    const btn = document.getElementById('btn-summon');
    if (btn) btn.classList.add('generating');

    if (localStableAudioEnabled()) {
      await renderLocalStableAudio(sel, undefined, { forceSource: true });
      if (btn) btn.classList.remove('generating');
      return;
    }

    const engineSel = document.getElementById('engine-selector');
    const selectedEngine = engineSel ? engineSel.value : 'auto';
    if (!selectedApiEngineReady()) {
      if (btn) btn.classList.remove('generating');
      return;
    }

    const res = await apiPost('/api/generate', {
      target: sel,
      route: 'hybrid',
      engine: selectedEngine,
    });

    if (btn) btn.classList.remove('generating');
    if (res && res.status === 'ok') {
      addLog(`summoned → generated from layer ${sel}`, 'generated', '✦');
    } else {
      addLog('summon failed: ' + (res?.message || 'unknown error'), 'error', '✕');
    }
  });

  // fx palette — DSP effects popover
  if (btnFx && fxPalette) {
    btnFx.addEventListener('click', () => {
      const willOpen = fxPalette.classList.contains('hidden');
      closeAllPalettes();
      if (willOpen) {
        fxPalette.classList.remove('hidden');
        btnFx.setAttribute('aria-expanded', 'true');
      }
    });
    document.getElementById('fx-palette-close')?.addEventListener('click', () => {
      fxPalette.classList.add('hidden');
      btnFx.setAttribute('aria-expanded', 'false');
    });
    fxPalette.querySelectorAll('.fx-chip-btn').forEach(chip => {
      chip.addEventListener('click', () => {
        const cmd = chip.dataset.cmd;
        if (cmd) sendCommand(cmd);
        fxPalette.classList.add('hidden');
        btnFx.setAttribute('aria-expanded', 'false');
      });
    });
    // click outside to close
    document.addEventListener('click', (e) => {
      if (fxPalette.classList.contains('hidden')) return;
      if (e.target.closest('#fx-palette') || e.target.closest('#btn-fx')) return;
      fxPalette.classList.add('hidden');
      btnFx.setAttribute('aria-expanded', 'false');
    });
  }

  // ── mixer panel — slide toggle ──
  if (btnMixer && mixerPanel) {
    btnMixer.addEventListener('click', () => {
      const willOpen = mixerPanel.classList.contains('hidden');
      closeAllPalettes();
      if (willOpen) {
        mixerPanel.classList.remove('hidden');
        btnMixer.setAttribute('aria-expanded', 'true');
        renderMixerChannels();
      }
    });
    document.getElementById('mixer-panel-close')?.addEventListener('click', () => {
      mixerPanel.classList.add('hidden');
      btnMixer.setAttribute('aria-expanded', 'false');
    });
    document.addEventListener('click', (e) => {
      if (mixerPanel.classList.contains('hidden')) return;
      if (e.target.closest('#mixer-panel') || e.target.closest('#btn-mixer')) return;
      mixerPanel.classList.add('hidden');
      btnMixer.setAttribute('aria-expanded', 'false');
    });
  }

  // ── mixer channel rendering ──
  let _mixerUpdateTimer = null;

  function renderMixerChannels() {
    const container = document.getElementById('mixer-channels');
    if (!container || !state.layers) return;

    container.innerHTML = '';
    state.layers.forEach((layer, i) => {
      const ch = document.createElement('div');
      ch.className = 'mixer-channel' +
        (i === state.selected_layer ? ' selected' : '') +
        (layer.muted ? ' muted' : '');
      ch.dataset.index = i;

      // label
      const label = document.createElement('div');
      label.className = 'mixer-ch-label';
      label.textContent = String(i + 1);
      ch.appendChild(label);

      // fader + meter row
      const faderRow = document.createElement('div');
      faderRow.className = 'mixer-fader-row';

      // meter
      const meter = document.createElement('div');
      meter.className = 'mixer-meter';
      const meterFill = document.createElement('div');
      meterFill.className = 'mixer-meter-fill';
      meter.appendChild(meterFill);
      meter.dataset.layerIndex = i;
      faderRow.appendChild(meter);

      // fader
      const fader = document.createElement('div');
      fader.className = 'mixer-fader-wrap';
      fader.dataset.target = String(i + 1);
      const fill = document.createElement('div');
      fill.className = 'mixer-fader-fill';
      const unity = document.createElement('div');
      unity.className = 'mixer-fader-unity';
      fader.appendChild(fill);
      fader.appendChild(unity);
      const vol = layerVolume(layer);
      const pos = volumeToStripPos(vol);
      const pct = Math.max(0, Math.min(100, (pos / 200) * 100));
      fill.style.height = pct + '%';
      fader.classList.toggle('hot', vol > 1.1);
      faderRow.appendChild(fader);

      ch.appendChild(faderRow);

      // dB readout
      const dbLabel = document.createElement('div');
      dbLabel.className = 'mixer-fader-db';
      dbLabel.textContent = volumeLabel(vol);
      ch.appendChild(dbLabel);

      // pan track
      const panWrap = document.createElement('div');
      panWrap.className = 'mixer-pan-wrap';
      const panLbl = document.createElement('span');
      panLbl.className = 'mixer-pan-label';
      panLbl.textContent = 'P';
      const panTrack = document.createElement('div');
      panTrack.className = 'mixer-pan-track';
      panTrack.dataset.target = String(i + 1);
      const panThumb = document.createElement('div');
      panThumb.className = 'mixer-pan-thumb';
      const pan = layer.pan != null ? layer.pan : 0;
      panThumb.style.left = ((pan + 1) / 2 * 100) + '%';
      panTrack.appendChild(panThumb);
      panWrap.appendChild(panLbl);
      panWrap.appendChild(panTrack);
      ch.appendChild(panWrap);

      // mute / solo buttons
      const btnRow = document.createElement('div');
      btnRow.className = 'mixer-btn-row';
      const muteBtn = document.createElement('button');
      muteBtn.className = 'mixer-btn mixer-mute-btn' + (layer.muted ? ' on' : '');
      muteBtn.textContent = 'M';
      muteBtn.dataset.target = String(i + 1);
      const soloBtn = document.createElement('button');
      soloBtn.className = 'mixer-btn mixer-solo-btn' + (layer.solo ? ' on' : '');
      soloBtn.textContent = 'S';
      soloBtn.dataset.target = String(i + 1);
      btnRow.appendChild(muteBtn);
      btnRow.appendChild(soloBtn);
      ch.appendChild(btnRow);

      container.appendChild(ch);

      // fader drag
      setupMixerFader(fader, fill, dbLabel, i + 1);

      // pan drag
      setupMixerPan(panTrack, panThumb, i + 1);

      // mute/solo clicks
      muteBtn.addEventListener('click', () => {
        sendCommand('mute layer ' + muteBtn.dataset.target);
      });
      soloBtn.addEventListener('click', () => {
        sendCommand('solo layer ' + soloBtn.dataset.target);
      });
    });

    // start live update
    clearInterval(_mixerUpdateTimer);
    _mixerUpdateTimer = setInterval(updateMixerLive, 120);
  }

  function updateMixerLive() {
    if (!mixerPanel || mixerPanel.classList.contains('hidden')) {
      clearInterval(_mixerUpdateTimer);
      _mixerUpdateTimer = null;
      return;
    }
    if (!state.layers) return;

    const channels = document.querySelectorAll('.mixer-channel');
    state.layers.forEach((layer, i) => {
      const ch = channels[i];
      if (!ch) return;
      ch.classList.toggle('selected', i === state.selected_layer);
      ch.classList.toggle('muted', !!layer.muted);

      // update meter
      const meterFill = ch.querySelector('.mixer-meter-fill');
      if (meterFill) {
        const hasSignal = layer.state !== 'empty' && !layer.muted && (layer.playhead_pct || 0) > 0;
        const level = hasSignal ? Math.max(0, Math.min(1, state.output_level || 0)) : 0;
        const db = level > 0.00001 ? 20 * Math.log10(level) : -Infinity;
        const meterPct = Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
        meterFill.style.height = meterPct + '%';
        meterFill.className = 'mixer-meter-fill' +
          (db > -3 ? ' clip' : db > -18 ? ' hot' : '');
      }

      // update fader (if not dragging)
      const fader = ch.querySelector('.mixer-fader-wrap');
      if (fader && !fader._dragging && !fader._cooldown) {
        const vol = layerVolume(layer);
        const pos = volumeToStripPos(vol);
        const pct = Math.max(0, Math.min(100, (pos / 200) * 100));
        const fill = fader.querySelector('.mixer-fader-fill');
        if (fill) fill.style.height = pct + '%';
        fader.classList.toggle('hot', vol > 1.1);
        const dbLabel = ch.querySelector('.mixer-fader-db');
        if (dbLabel) dbLabel.textContent = volumeLabel(vol);
      }

      // update mute/solo states
      const muteBtn = ch.querySelector('.mixer-mute-btn');
      const soloBtn = ch.querySelector('.mixer-solo-btn');
      if (muteBtn) muteBtn.classList.toggle('on', !!layer.muted);
      if (soloBtn) soloBtn.classList.toggle('on', !!layer.solo);

      // update pan
      const panThumb = ch.querySelector('.mixer-pan-thumb');
      const panTrack = ch.querySelector('.mixer-pan-track');
      if (panThumb && panTrack && !panTrack._dragging) {
        const pan = layer.pan != null ? layer.pan : 0;
        panThumb.style.left = ((pan + 1) / 2 * 100) + '%';
      }
    });
  }

  function setupMixerFader(fader, fill, dbLabel, target) {
    let startY = 0, startVal = 0;

    function startCooldown() {
      fader._cooldown = true;
      setTimeout(() => { fader._cooldown = false; }, 500);
    }

    fader.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      fader._dragging = true;
      startY = e.clientY;
      const layerData = state.layers?.[target - 1];
      startVal = volumeToStripPos(layerVolume(layerData));
      fader.setPointerCapture(e.pointerId);
    });

    fader.addEventListener('pointermove', (e) => {
      if (!fader._dragging) return;
      const dy = startY - e.clientY;
      const sensitivity = e.shiftKey ? 0.5 : 1.2;
      const newVal = Math.max(0, Math.min(200, Math.round(startVal + dy * sensitivity)));
      const pct = Math.max(0, Math.min(100, (newVal / 200) * 100));
      fill.style.height = pct + '%';
      fader.classList.toggle('hot', stripPosToVolume(newVal) > 1.1);
      const vol = stripPosToVolume(newVal);
      dbLabel.textContent = volumeLabel(vol);
      fader._currentVal = newVal;
    });

    fader.addEventListener('pointerup', () => {
      if (!fader._dragging) return;
      fader._dragging = false;
      if (fader._currentVal != null) {
        const vol = formatVolume(stripPosToVolume(fader._currentVal));
        sendCommand('set volume layer ' + target + ' ' + vol);
        startCooldown();
        // sync the corresponding layer vol-strip
        const strip = document.querySelector(`.vol-strip[data-target="${target}"]`);
        if (strip) {
          strip.dataset.value = fader._currentVal;
          strip._cooldown = true;
          setTimeout(() => { strip._cooldown = false; }, 500);
          updateVolStripVisual(strip);
        }
      }
    });

    fader.addEventListener('lostpointercapture', () => {
      if (fader._dragging) {
        fader._dragging = false;
        if (fader._currentVal != null) {
          const vol = formatVolume(stripPosToVolume(fader._currentVal));
          sendCommand('set volume layer ' + target + ' ' + vol);
          startCooldown();
        }
      }
    });
  }

  function setupMixerPan(track, thumb, target) {
    let dragging = false;

    function updatePan(clientX) {
      const rect = track.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const pan = (pct * 2 - 1).toFixed(2);
      thumb.style.left = (pct * 100) + '%';
      return pan;
    }

    track.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragging = true;
      track._dragging = true;
      track.setPointerCapture(e.pointerId);
      updatePan(e.clientX);
    });

    track.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      updatePan(e.clientX);
    });

    track.addEventListener('pointerup', (e) => {
      if (!dragging) return;
      dragging = false;
      track._dragging = false;
      const pan = updatePan(e.clientX);
      sendCommand('set pan layer ' + target + ' ' + pan);
    });

    track.addEventListener('lostpointercapture', () => {
      dragging = false;
      track._dragging = false;
    });
  }

  // ── expanded volume overlay ──
  function openExpandedVolume(strip) {
    const target = strip.dataset.target;
    const layerIndex = parseInt(target) - 1;
    const layer = state.layers?.[layerIndex];
    if (!layer) return;

    const overlay = document.createElement('div');
    overlay.className = 'vol-expanded-overlay';
    overlay.id = 'vol-expanded-overlay';

    const card = document.createElement('div');
    card.className = 'vol-expanded-card';

    // label
    const label = document.createElement('div');
    label.className = 'vol-expanded-label';
    label.textContent = 'layer ' + target;
    card.appendChild(label);

    // fader
    const fader = document.createElement('div');
    fader.className = 'vol-expanded-fader';
    const fill = document.createElement('div');
    fill.className = 'vol-strip-fill';
    const unityLine = document.createElement('div');
    unityLine.className = 'vol-strip-unity';
    fader.appendChild(fill);
    fader.appendChild(unityLine);
    card.appendChild(fader);

    // value display
    const valueEl = document.createElement('div');
    valueEl.className = 'vol-expanded-value';
    card.appendChild(valueEl);

    // mute button
    const muteBtn = document.createElement('button');
    muteBtn.className = 'vol-expanded-mute' + (layer.muted ? ' on' : '');
    muteBtn.textContent = layer.muted ? 'unmute' : 'mute';
    muteBtn.addEventListener('click', () => {
      sendCommand('mute layer ' + target);
      muteBtn.classList.toggle('on');
      muteBtn.textContent = muteBtn.classList.contains('on') ? 'unmute' : 'mute';
    });
    card.appendChild(muteBtn);

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    // sync initial value from strip
    let currentVal = stripValue(strip);

    function updateVisual() {
      const pct = Math.max(0, Math.min(100, (currentVal / 200) * 100));
      fill.style.height = pct + '%';
      const vol = stripPosToVolume(currentVal);
      valueEl.textContent = volumeLabel(vol);
    }
    updateVisual();

    // fader drag
    let startY = 0, startFaderVal = 0;
    let isDragging = false;

    fader.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      isDragging = true;
      startY = e.clientY;
      startFaderVal = currentVal;
      fader.setPointerCapture(e.pointerId);
    });

    fader.addEventListener('pointermove', (e) => {
      if (!isDragging) return;
      const dy = startY - e.clientY;
      const sensitivity = e.shiftKey ? 0.3 : 0.8;
      currentVal = Math.max(0, Math.min(200, Math.round(startFaderVal + dy * sensitivity)));
      updateVisual();
    });

    fader.addEventListener('pointerup', () => {
      if (!isDragging) return;
      isDragging = false;
      const vol = formatVolume(stripPosToVolume(currentVal));
      sendCommand('set volume layer ' + target + ' ' + vol);
      // sync back to layer strip
      strip.dataset.value = currentVal;
      strip._cooldown = true;
      setTimeout(() => { strip._cooldown = false; }, 500);
      updateVolStripVisual(strip);
    });

    // close on overlay click
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        overlay.remove();
      }
    });

    // close on escape
    function onEsc(e) {
      if (e.key === 'Escape') {
        overlay.remove();
        document.removeEventListener('keydown', onEsc);
      }
    }
    document.addEventListener('keydown', onEsc);
  }


  const logPanel = document.getElementById('log-panel');
  const logStatusBtn = document.getElementById('log-status');
  if (logStatusBtn && logPanel) {
    logStatusBtn.addEventListener('click', () => {
      const isCollapsed = logPanel.classList.contains('collapsed');
      logPanel.classList.toggle('collapsed', !isCollapsed);
      logPanel.classList.toggle('expanded', isCollapsed);
      logStatusBtn.setAttribute('aria-expanded', String(isCollapsed));
      if (isCollapsed) {
        // scroll to bottom on open
        const lines = document.getElementById('log-lines');
        if (lines) lines.scrollTop = lines.scrollHeight;
      }
    });
  }

  // keyboard shortcuts — §4.3 extended
  document.addEventListener('keydown', (e) => {
    if (document.activeElement === cmdInput) return;
    // don't intercept when palette is open
    if (!paletteOverlay.classList.contains('hidden') && document.activeElement === paletteInput) return;

    // cmd/ctrl combos
    if (e.metaKey || e.ctrlKey) {
      if (e.key === 's') { e.preventDefault(); sendCommand('save session'); return; }
      if (e.key === 'e') { e.preventDefault(); apiPost('/api/export-master'); addLog('exporting mix…', 'export', '◉'); return; }
      return; // don't process other cmd combos
    }

    switch (e.key) {
      case 'r':
        if (state.recording) stopRecording();
        else startRecording();
        break;
      case 'R': sendCommand('reverse'); break; // shift-R = reverse
      case 'o': sendCommand('overdub'); break;
      case '1': sendCommand('select layer 1'); break;
      case '2': sendCommand('select layer 2'); break;
      case '3': sendCommand('select layer 3'); break;
      case '4': sendCommand('select layer 4'); break;
      case '[': {
        const cur = state.selected_layer || 0;
        const prev = Math.max(0, cur - 1) + 1;
        sendCommand('select layer ' + prev);
        break;
      }
      case ']': {
        const cur = state.selected_layer || 0;
        const next = Math.min((state.layers?.length || 4) - 1, cur + 1) + 1;
        sendCommand('select layer ' + next);
        break;
      }
      case 'm': sendCommand('mute'); break;
      case 'u':
        // unmute all
        if (state.layers) {
          state.layers.forEach((layer, i) => {
            if (layer.muted) sendCommand('mute layer ' + (i + 1));
          });
          addLog('unmuted all layers', 'system', '♪');
        }
        break;
      case 'k':
        apiPost('/api/kill');
        addLog('killed all audio', 'error', '⊘');
        break;
      case 'g': {
        const sel = (state.selected_layer || 0) + 1;
        autoGenerate(sel);
        break;
      }
      case 'l': {
        // toggle auto-mode with keyboard
        const autoBtn = document.getElementById('btn-auto-mode');
        if (autoBtn) autoBtn.click();
        break;
      }
      case '?':
      case 'h':
        openPalette();
        break;
      case 'Escape':
        if (state.recording) stopRecording();
        settingsPanel.classList.add('hidden');
        if (fxPalette) {
          fxPalette.classList.add('hidden');
          btnFx?.setAttribute('aria-expanded', 'false');
        }
        if (aboutOverlay && !aboutOverlay.classList.contains('hidden')) closeAbout();
        closePalette();
        break;
      case '/': e.preventDefault(); cmdInput.focus(); break;
    }
  });

  // ── init ──
  connect();
  setupHints();
  addLog('ORAM dashboard initialized', 'system', '▸');

  // pre-load devices
  loadDevices();

  // pre-load engines
  loadEngines();

  // ── engine selector ──
  const engineSelector = document.getElementById('engine-selector');
  const runtimeModeSelector = document.getElementById('runtime-mode-selector');
  const stableAudioPanel = document.getElementById('stable-audio-panel');
  const engineChip = document.getElementById('prompt-engine-chip');

  function syncRuntimeModeControls() {
    const local = localStableAudioEnabled();
    if (stableAudioPanel) stableAudioPanel.classList.toggle('hidden', !local);
    if (engineSelector) engineSelector.disabled = local;
  }

  if (runtimeModeSelector) {
    runtimeModeSelector.addEventListener('change', () => {
      syncRuntimeModeControls();
      addLog(`runtime → ${runtimeModeSelector.value === 'local' ? 'local' : 'API / auto'}`, 'system', '⚙');
    });
    syncRuntimeModeControls();
  }

  async function loadEngines() {
    const data = await apiGet('/api/engines');
    if (!data || !data.engines) return;

    engineSelector.innerHTML = '';
    const selectableEngines = data.engines.filter(e =>
      e.capabilities.includes('text_to_sound_effect') ||
      e.capabilities.includes('text_to_music') ||
      e.capabilities.includes('audio_to_audio')
    );
    const selectableAvailable = selectableEngines.filter(e => e.available).length;

    // auto option
    const autoOpt = document.createElement('option');
    autoOpt.value = 'auto';
    autoOpt.textContent = '⚡ auto (' + selectableAvailable + ' engines)';
    engineSelector.appendChild(autoOpt);

    // group engines by provider
    const byProvider = {};
    selectableEngines.forEach(e => {
      if (!byProvider[e.provider]) byProvider[e.provider] = [];
      byProvider[e.provider].push(e);
    });

    const providerIcons = {
      elevenlabs: '◈',
      fal: '◉',
      stability: '◆',
      huggingface: '◇',
      replicate: '▣',
      local: '▸',
    };

    Object.entries(byProvider).forEach(([provider, engines]) => {
      const group = document.createElement('optgroup');
      const icon = providerIcons[provider] || '·';
      group.label = icon + ' ' + provider;
      engines.forEach(engine => {
        const opt = document.createElement('option');
        opt.value = engine.id;
        const needsKey = engine.requires_api_key && !engine.available;
        const avail = needsKey ? ' [key needed]' : (engine.available ? '' : ' [offline]');
        const caps = engine.capabilities.map(c => c.replace('text_to_', '').replace('audio_', '')).join(', ');
        opt.textContent = engine.label + avail;
        opt.title = caps + ' · ' + engine.latency_profile + ' · $' + engine.cost_per_second + '/s';
        opt.dataset.available = engine.available ? 'true' : 'false';
        opt.dataset.requiresApiKey = engine.requires_api_key ? 'true' : 'false';
        opt.disabled = !engine.available && !needsKey;
        group.appendChild(opt);
      });
      engineSelector.appendChild(group);
    });

    addLog(`${selectableAvailable}/${selectableEngines.length} generation engines available`, 'system', '⚙');
  }

  if (engineSelector) {
    engineSelector.addEventListener('change', () => {
      const val = engineSelector.value;
      const opt = engineSelector.options[engineSelector.selectedIndex];
      const label = opt ? opt.textContent : val;
      if (engineChip) engineChip.textContent = label;
      addLog(`engine → ${label}`, 'system', '⚙');
    });
  }

  const sa3ServiceUrlInputForListen = document.getElementById('sa3-service-url');
  if (sa3ServiceUrlInputForListen) {
    sa3ServiceUrlInputForListen.addEventListener('input', () => {
      sa3ServiceUrlInputForListen._userEdited = true;
    });
  }

  // ═══════════════════════════════════════════════════════════════
  // resize → re-render waveforms with fresh DPR/size
  // ═══════════════════════════════════════════════════════════════
  window.addEventListener('resize', () => {
    invalidateWaveforms();
    if (state.layers) render(state);
  });

  // ═══════════════════════════════════════════════════════════════
  // COMMAND PALETTE (§4.1.3) — ⌘K / Ctrl+K
  // ═══════════════════════════════════════════════════════════════

  const PALETTE_COMMANDS = [
    // transport
    { group: 'transport', label: 'Record', icon: '⏺', shortcut: 'r', action: () => startRecording() },
    { group: 'transport', label: 'Stop', icon: '⏹', action: () => stopRecording() },
    { group: 'transport', label: 'Overdub', icon: '⊕', shortcut: 'o', action: () => sendCommand('overdub') },
    { group: 'transport', label: 'Kill All Sound', icon: '⊘', shortcut: 'k', action: () => { apiPost('/api/kill'); addLog('killed all audio', 'error', '⊘'); } },
    // layers
    { group: 'layers', label: 'Select Layer 1', icon: 'L1', shortcut: '1', action: () => sendCommand('select layer 1') },
    { group: 'layers', label: 'Select Layer 2', icon: 'L2', shortcut: '2', action: () => sendCommand('select layer 2') },
    { group: 'layers', label: 'Select Layer 3', icon: 'L3', shortcut: '3', action: () => sendCommand('select layer 3') },
    { group: 'layers', label: 'Select Layer 4', icon: 'L4', shortcut: '4', action: () => sendCommand('select layer 4') },
    { group: 'layers', label: 'Mute Selected', icon: 'M', shortcut: 'm', action: () => sendCommand('mute') },
    { group: 'layers', label: 'Clear Selected', icon: '⌫', action: () => { const t = (state.selected_layer || 0) + 1; clearLayer(t); } },
    // dsp
    { group: 'dsp', label: 'Reverse', icon: '↺', action: () => sendCommand('reverse') },
    { group: 'dsp', label: 'Slower (Half Speed)', icon: '½', action: () => sendCommand('make it slower') },
    { group: 'dsp', label: 'Faster (Double Speed)', icon: '2×', action: () => sendCommand('make it faster') },
    { group: 'dsp', label: 'Darken (Low-pass)', icon: '◐', action: () => sendCommand('make it darker') },
    { group: 'dsp', label: 'Granulate', icon: '░', action: () => sendCommand('granulate softly') },
    { group: 'dsp', label: 'Reverb Wash', icon: '≋', action: () => sendCommand('wash it in reverb') },
    { group: 'dsp', label: 'Spatial Far', icon: '↠', action: () => sendCommand('make it far away') },
    { group: 'dsp', label: 'Stretch Breathe', icon: '≈', action: () => sendCommand('stretch until it breathes') },
    // agent
    { group: 'agent', label: 'Toggle Auto Mode', icon: '⚙', shortcut: 'l', action: () => { const autoBtn = document.getElementById('btn-auto-mode'); if (autoBtn) autoBtn.click(); } },
    { group: 'agent', label: 'Auto-Generate', icon: '✦', shortcut: 'g', action: () => { const sel = (state.selected_layer || 0) + 1; autoGenerate(sel); } },
    { group: 'agent', label: 'Summon (Listen + Generate)', icon: '✦', action: () => { document.getElementById('btn-summon')?.click(); } },
    // session
    { group: 'session', label: 'Save Session', icon: '⬡', action: () => sendCommand('save session') },
    { group: 'session', label: 'Export Mix', icon: '◉', action: () => apiPost('/api/export-master') },
    // loop
    { group: 'loop', label: 'Enable Loop Selected', icon: '⟳', action: () => commitLoopRegion((state.selected_layer || 0) + 1, null, null, true) },
    { group: 'loop', label: 'Reset Loop Selected', icon: '⟲', action: () => commitLoopRegion((state.selected_layer || 0) + 1, 0, 100, false) },
    { group: 'loop', label: 'Set Looper Mode', icon: 'L', action: () => apiPost('/api/set-layer-mode', { mode: 'looper' }) },
    // themes
    { group: 'theme', label: 'Theme: Dark', icon: '◆', action: () => applyTheme('dark') },
    { group: 'theme', label: 'Theme: Light', icon: '◇', action: () => applyTheme('light') },
    // focus
    { group: 'nav', label: 'Focus Prompt', icon: '/', shortcut: '/', action: () => cmdInput.focus() },
  ];

  const paletteOverlay = document.getElementById('cmd-palette-overlay');
  const paletteInput = document.getElementById('palette-input');
  const paletteResults = document.getElementById('palette-results');
  const paletteMatchCount = document.getElementById('palette-match-count');
  let paletteActive = -1;

  function openPalette() {
    paletteOverlay.classList.remove('hidden');
    paletteInput.value = '';
    paletteActive = -1;
    renderPalette('');
    setTimeout(() => paletteInput.focus(), 50);
  }

  function closePalette() {
    paletteOverlay.classList.add('hidden');
    paletteInput.blur();
  }

  function fuzzyMatch(query, label) {
    const q = query.toLowerCase();
    const l = label.toLowerCase();
    if (!q) return true;
    let qi = 0;
    for (let li = 0; li < l.length && qi < q.length; li++) {
      if (l[li] === q[qi]) qi++;
    }
    return qi === q.length;
  }

  function renderPalette(query) {
    const filtered = PALETTE_COMMANDS.filter(c => fuzzyMatch(query, c.label));
    paletteResults.innerHTML = '';
    paletteMatchCount.textContent = filtered.length + ' command' + (filtered.length !== 1 ? 's' : '');

    let lastGroup = '';
    filtered.forEach((cmd, i) => {
      if (cmd.group !== lastGroup) {
        lastGroup = cmd.group;
        const groupEl = document.createElement('div');
        groupEl.className = 'palette-group-label';
        groupEl.textContent = cmd.group;
        paletteResults.appendChild(groupEl);
      }

      const item = document.createElement('div');
      item.className = 'palette-item' + (i === paletteActive ? ' active' : '');
      item.dataset.index = i;

      const iconEl = document.createElement('span');
      iconEl.className = 'pi-icon';
      iconEl.textContent = cmd.icon;

      const labelEl = document.createElement('span');
      labelEl.className = 'pi-label';
      labelEl.textContent = cmd.label;

      item.appendChild(iconEl);
      item.appendChild(labelEl);

      if (cmd.shortcut) {
        const shortcutEl = document.createElement('span');
        shortcutEl.className = 'pi-shortcut';
        shortcutEl.textContent = cmd.shortcut;
        item.appendChild(shortcutEl);
      }

      item.addEventListener('click', () => {
        cmd.action();
        closePalette();
      });
      item.addEventListener('mouseenter', () => {
        paletteActive = i;
        updatePaletteActive(filtered);
      });

      paletteResults.appendChild(item);
    });
  }

  function updatePaletteActive(filtered) {
    const items = paletteResults.querySelectorAll('.palette-item');
    items.forEach((el, i) => {
      el.classList.toggle('active', i === paletteActive);
    });
    // scroll active into view
    const active = paletteResults.querySelector('.palette-item.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  paletteInput.addEventListener('input', () => {
    paletteActive = -1;
    renderPalette(paletteInput.value);
  });

  paletteInput.addEventListener('keydown', (e) => {
    const filtered = PALETTE_COMMANDS.filter(c => fuzzyMatch(paletteInput.value, c.label));

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      paletteActive = Math.min(paletteActive + 1, filtered.length - 1);
      updatePaletteActive(filtered);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      paletteActive = Math.max(paletteActive - 1, 0);
      updatePaletteActive(filtered);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (paletteActive >= 0 && paletteActive < filtered.length) {
        filtered[paletteActive].action();
        closePalette();
      } else if (filtered.length === 1) {
        filtered[0].action();
        closePalette();
      } else if (paletteInput.value.trim()) {
        // free-form command
        sendCommand(paletteInput.value.trim());
        closePalette();
      }
    } else if (e.key === 'Escape') {
      closePalette();
    }
  });

  paletteOverlay.addEventListener('click', (e) => {
    if (e.target === paletteOverlay) closePalette();
  });

  // ⌘K / Ctrl+K to open palette
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      if (paletteOverlay.classList.contains('hidden')) {
        openPalette();
      } else {
        closePalette();
      }
    }
  });

})();
