(() => {
  "use strict";

  const state = {
    freqHz: 14200000,
    mode: "USB",
    grdb: 40,
    grdbActual: 40,
    lnaState: 4,
    agc: true,
    overload: false,
    signalDbfs: -120,
    squelchEnabled: false,
    squelchThresholdDbfs: -90,
    squelched: false,
    sampleRateHz: 2000000,
    spanHz: 2000000,
    stepHz: 1000,
    fftSize: 2048,
    audioRateHz: 12500,
    bands: [],
    modes: [],
    // Display dB range for both the spectrum trace's vertical scale and
    // the waterfall's color mapping -- narrowing this is what "increases
    // sensitivity/contrast" so weaker signals stand out against the floor.
    dbMin: -120,
    dbMax: -20,
  };

  // S-meter reference: this SDR reports raw dBFS (digital full-scale level),
  // which has no fixed relationship to actual RF dBm -- it shifts with
  // gain reduction/LNA state, unlike a calibrated radio S-meter. There's no
  // real calibration data to anchor this, so S9_REF_DBFS is a placeholder
  // assumption (numerically mirrors the classic S9 = -73 dBm convention).
  // Adjust it against a known signal source if true calibration matters;
  // until then this is "S-units, approximately."
  const S9_REF_DBFS = -73;
  const S_UNIT_DB = 6; // dB per S-unit below S9, ham convention
  const S9_PLUS_MAX_DB = 60; // clamp ceiling, e.g. "S9+60dB"

  function formatSUnits(dbfs) {
    const rel = dbfs - S9_REF_DBFS; // dB relative to S9
    if (rel >= 0) {
      const plus = Math.min(S9_PLUS_MAX_DB, Math.round(rel));
      return plus === 0 ? "S9" : `S9+${plus}dB`;
    }
    const s = Math.max(0, Math.round(9 + rel / S_UNIT_DB));
    return `S${s}`;
  }

  const MIN_DB_RANGE = 10; // floor/ceiling sliders can't be pushed closer than this
  const AXIS_HEIGHT_CSS = 18; // frequency axis strip, in CSS px, at the bottom of the spectrum canvas
  const SPAN_OPTIONS = [2000000, 1000000, 500000, 200000, 100000, 50000, 20000, 10000];
  const STEP_OPTIONS = [10, 100, 500, 1000, 5000, 10000];

  let lastSpectrum = null;

  const dotEl = document.getElementById("conn-dot");
  const freqInput = document.getElementById("freq-input");
  const modeButtonsEl = document.getElementById("mode-buttons");
  const bandRowEl = document.getElementById("band-row");
  const spanRowEl = document.getElementById("span-row");
  const stepRowEl = document.getElementById("step-row");
  const memRowEl = document.getElementById("mem-row");
  const memSaveBtn = document.getElementById("mem-save-btn");
  const floorSlider = document.getElementById("floor-slider");
  const floorVal = document.getElementById("floor-val");
  const ceilSlider = document.getElementById("ceil-slider");
  const ceilVal = document.getElementById("ceil-val");
  const smeterFill = document.getElementById("smeter-fill");
  const smeterVal = document.getElementById("smeter-val");
  const overloadBadge = document.getElementById("overload-badge");
  const sqlBadge = document.getElementById("sql-badge");
  const sqlToggle = document.getElementById("sql-toggle");
  const sqlSlider = document.getElementById("sql-slider");
  const sqlVal = document.getElementById("sql-val");
  const grdbSlider = document.getElementById("grdb-slider");
  const grdbVal = document.getElementById("grdb-val");
  const lnaSlider = document.getElementById("lna-slider");
  const lnaVal = document.getElementById("lna-val");
  const agcToggle = document.getElementById("agc-toggle");
  const volSlider = document.getElementById("vol-slider");
  const volVal = document.getElementById("vol-val");
  const audioBtn = document.getElementById("audio-btn");
  const exitBtn = document.getElementById("exit-btn");
  const shutdownOverlay = document.getElementById("shutdown-overlay");

  const specCanvas = document.getElementById("spectrum");
  const wfCanvas = document.getElementById("waterfall");
  const specCtx = specCanvas.getContext("2d");
  const wfCtx = wfCanvas.getContext("2d");
  // Each waterfall row is rendered at native FFT-bin width, then stretched
  // to the full canvas width -- default bilinear smoothing on that stretch
  // blurs narrow signals into the noise floor. Nearest-neighbor keeps each
  // bin a crisp block instead.
  wfCtx.imageSmoothingEnabled = false;
  let wfRowCanvas = document.createElement("canvas");

  // ---------------- WebSocket ----------------
  let ws = null;
  let wsReconnectTimer = null;
  // Set right before requesting shutdown, so onclose doesn't spend the
  // rest of the tab's life quietly retrying a server that was stopped on
  // purpose -- see exitBtn's click handler below.
  let intentionalShutdown = false;

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { dotEl.className = "dot dot-on"; };
    ws.onclose = () => {
      dotEl.className = "dot dot-off";
      if (intentionalShutdown) return;
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(connect, 1000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = onMessage;
  }

  function send(cmd) {
    if (cmd.cmd === "set_freq") {
      // Retuning changes what the server is decoding immediately, but any
      // audio already scheduled from the OLD frequency keeps playing out
      // on its own Web Audio timeline regardless -- without flushing it
      // here, the new station's incoming audio gets scheduled right on
      // top, and both are audible at once for as long as the old queue
      // takes to drain. Same flush the mute button already does.
      stopAllScheduled();
      if (audioCtx) nextStartTime = audioCtx.currentTime + 0.1;
    }
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
  }

  function onMessage(ev) {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      if (msg.type === "init") applyInit(msg);
      else if (msg.type === "state") applyState(msg);
      return;
    }
    const buf = ev.data;
    const tag = new Uint8Array(buf, 0, 1)[0];
    // slice() copies the payload into a fresh, zero-offset ArrayBuffer --
    // required because Float32Array/Int16Array views need a byte offset
    // that's a multiple of their element size, and the 1-byte tag prefix
    // isn't a multiple of 4 (or 2).
    if (tag === 0x01) {
      lastSpectrum = new Float32Array(buf.slice(1));
      drawSpectrum(lastSpectrum);
    } else if (tag === 0x02) {
      playAudio(new Int16Array(buf.slice(1)));
    }
  }

  // ---------------- state -> UI ----------------
  function applyInit(msg) {
    state.bands = msg.bands;
    state.modes = msg.modes;
    state.fftSize = msg.fftSize;
    state.audioRateHz = msg.audioRateHz;
    buildModeButtons();
    buildBandButtons();
    applyState(msg);
  }

  function applyState(msg) {
    state.freqHz = msg.freqHz;
    state.mode = msg.mode;
    state.grdb = msg.grdb;
    state.grdbActual = msg.grdbActual != null ? msg.grdbActual : msg.grdb;
    state.lnaState = msg.lnaState;
    state.agc = msg.agc;
    state.overload = msg.overload;
    state.signalDbfs = msg.signalDbfs;
    state.sampleRateHz = msg.sampleRateHz;
    state.squelchEnabled = msg.squelchEnabled;
    state.squelchThresholdDbfs = msg.squelchThresholdDbfs;
    state.squelched = msg.squelched;

    if (document.activeElement !== freqInput) freqInput.value = formatFreq(state.freqHz);
    updateModeButtons();
    updateBandButtons();
    if (document.activeElement !== grdbSlider) {
      // Always show what the tuner is actually running, never what we
      // last commanded. Those differ whenever the AGC is on (it drives
      // gRdB itself), and they stay differing after AGC is switched off,
      // because switching it off leaves the gain wherever the AGC last
      // put it rather than snapping back to the commanded value. The
      // "(AGC)" marker says who is driving the number, not which number
      // to show. Skipped while the slider has focus so this can't fight
      // a drag in progress.
      grdbSlider.value = state.grdbActual;
      grdbVal.textContent = state.agc ? state.grdbActual + " (AGC)" : state.grdbActual;
    }
    if (document.activeElement !== lnaSlider) {
      lnaSlider.value = state.lnaState;
      lnaVal.textContent = state.lnaState;
    }
    if (document.activeElement !== agcToggle) agcToggle.checked = state.agc;
    if (document.activeElement !== sqlToggle) sqlToggle.checked = state.squelchEnabled;
    if (document.activeElement !== sqlSlider) {
      sqlSlider.value = state.squelchThresholdDbfs;
      sqlVal.textContent = state.squelchThresholdDbfs;
    }
    overloadBadge.classList.toggle("hidden", !state.overload);
    sqlBadge.classList.toggle("hidden", !state.squelched);

    const pct = Math.max(0, Math.min(100, ((state.signalDbfs + 120) / 120) * 100));
    smeterFill.style.width = pct + "%";
    smeterVal.textContent = formatSUnits(state.signalDbfs);
    smeterVal.title = state.signalDbfs.toFixed(1) + " dBFS (raw, uncalibrated)";
  }

  function buildModeButtons() {
    modeButtonsEl.innerHTML = "";
    state.modes.forEach((m) => {
      const b = document.createElement("button");
      b.textContent = m;
      b.dataset.mode = m;
      b.onclick = () => send({ cmd: "set_mode", mode: m });
      modeButtonsEl.appendChild(b);
    });
  }

  function updateModeButtons() {
    [...modeButtonsEl.children].forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === state.mode)
    );
  }

  function buildBandButtons() {
    bandRowEl.innerHTML = "";
    state.bands.forEach((b) => {
      const btn = document.createElement("button");
      btn.textContent = b.name;
      btn.dataset.band = b.name;
      btn.onclick = () => send({ cmd: "set_band", band: b.name });
      bandRowEl.appendChild(btn);
    });
  }

  function updateBandButtons() {
    const active = state.bands.find(
      (b) => state.freqHz >= b.lowHz && state.freqHz <= b.highHz
    );
    [...bandRowEl.children].forEach((btn) =>
      btn.classList.toggle("active", !!active && btn.dataset.band === active.name)
    );
  }

  function formatSpanLabel(hz) {
    return hz >= 1000000 ? hz / 1000000 + "M" : hz / 1000 + "k";
  }

  // Span/step used to reset to their defaults on every page reload (only
  // memory presets were persisted) -- span in particular being silently
  // back at 2M after a refresh, with no visual cue the user hadn't
  // reselected it, made a click near a peak jump by up to +-1MHz instead
  // of the expected tens-of-kHz. Persist both, same mechanism as memories.
  const SPAN_STORAGE_KEY = "sdrduo_span_hz";
  const STEP_STORAGE_KEY = "sdrduo_step_hz";

  function loadPersistedTuningPref(key, options, fallback) {
    const raw = Number(localStorage.getItem(key));
    return options.includes(raw) ? raw : fallback;
  }

  state.spanHz = loadPersistedTuningPref(SPAN_STORAGE_KEY, SPAN_OPTIONS, state.spanHz);
  state.stepHz = loadPersistedTuningPref(STEP_STORAGE_KEY, STEP_OPTIONS, state.stepHz);

  function buildSpanButtons() {
    SPAN_OPTIONS.forEach((hz) => {
      const btn = document.createElement("button");
      btn.textContent = formatSpanLabel(hz);
      btn.dataset.span = String(hz);
      btn.onclick = () => {
        state.spanHz = hz;
        localStorage.setItem(SPAN_STORAGE_KEY, String(hz));
        updateSpanButtons();
        if (lastSpectrum) drawSpectrum(lastSpectrum);
      };
      spanRowEl.appendChild(btn);
    });
    updateSpanButtons();
  }

  function updateSpanButtons() {
    [...spanRowEl.querySelectorAll("button")].forEach((b) =>
      b.classList.toggle("active", Number(b.dataset.span) === state.spanHz)
    );
  }

  function formatStepLabel(hz) {
    return hz >= 1000 ? hz / 1000 + "k" : hz + "Hz";
  }

  function buildStepButtons() {
    STEP_OPTIONS.forEach((hz) => {
      const btn = document.createElement("button");
      btn.textContent = formatStepLabel(hz);
      btn.dataset.step = String(hz);
      btn.onclick = () => {
        state.stepHz = hz;
        localStorage.setItem(STEP_STORAGE_KEY, String(hz));
        updateStepButtons();
      };
      stepRowEl.appendChild(btn);
    });
    updateStepButtons();
  }

  function updateStepButtons() {
    [...stepRowEl.querySelectorAll("button")].forEach((b) =>
      b.classList.toggle("active", Number(b.dataset.step) === state.stepHz)
    );
  }

  // ---------------- memory / preset slots ----------------
  // Pure client-side (localStorage), like span/step/floor/ceiling -- these
  // just replay existing set_freq/set_mode commands, so there's no need
  // for server-side storage.
  const MEM_SLOTS = 8;
  const MEM_STORAGE_KEY = "sdrduo_memories";

  function loadMemories() {
    let arr = [];
    try {
      arr = JSON.parse(localStorage.getItem(MEM_STORAGE_KEY) || "[]");
      if (!Array.isArray(arr)) arr = [];
    } catch {
      arr = [];
    }
    while (arr.length < MEM_SLOTS) arr.push(null);
    return arr.slice(0, MEM_SLOTS);
  }

  function saveMemories() {
    try {
      localStorage.setItem(MEM_STORAGE_KEY, JSON.stringify(memories));
    } catch {
      // storage unavailable/full -- memories just won't persist this session
    }
  }

  let memories = loadMemories();
  let memSaveArmed = false;

  function setMemSaveArmed(armed) {
    memSaveArmed = armed;
    memRowEl.classList.toggle("save-armed", armed);
    memSaveBtn.textContent = armed ? "Click a slot…" : "Save…";
  }

  function storeIntoSlot(i) {
    memories[i] = { freqHz: state.freqHz, mode: state.mode };
    saveMemories();
    buildMemButtons();
  }

  function onMemSlotClick(i) {
    if (memSaveArmed) {
      storeIntoSlot(i);
      setMemSaveArmed(false);
    } else if (memories[i]) {
      send({ cmd: "set_freq", hz: memories[i].freqHz });
      send({ cmd: "set_mode", mode: memories[i].mode });
    }
  }

  function buildMemButtons() {
    memRowEl.querySelectorAll("button.mem-slot").forEach((b) => b.remove());
    for (let i = 0; i < MEM_SLOTS; i++) {
      const m = memories[i];
      const btn = document.createElement("button");
      btn.className = "mem-slot" + (m ? " filled" : "");
      btn.dataset.slot = String(i);
      btn.textContent = m ? (m.freqHz / 1e6).toFixed(3) : "M" + (i + 1);
      btn.title = m
        ? `${formatFreq(m.freqHz)} Hz ${m.mode} -- click to recall, right-click to overwrite`
        : "empty -- click Save then this slot, or right-click to save here directly";
      btn.onclick = () => onMemSlotClick(i);
      btn.oncontextmenu = (e) => {
        e.preventDefault();
        storeIntoSlot(i);
      };
      memRowEl.appendChild(btn);
    }
  }

  memSaveBtn.addEventListener("click", () => setMemSaveArmed(!memSaveArmed));

  // ---------------- frequency input ----------------
  function formatFreq(hz) {
    const s = Math.max(0, Math.round(hz)).toString();
    let out = "";
    for (let i = 0; i < s.length; i++) {
      if (i > 0 && (s.length - i) % 3 === 0) out += ".";
      out += s[i];
    }
    return out;
  }

  // ---------------- on-screen keypad ----------------
  const MAX_KEYPAD_DIGITS = 10; // up to 9.999...GHz, well past the RSPduo's 2GHz ceiling
  const keypadOverlay = document.getElementById("keypad-overlay");
  const keypadDisplay = document.getElementById("keypad-display");
  const keypadGrid = document.getElementById("keypad-grid");
  const keypadCancelBtn = document.getElementById("keypad-cancel");
  const keypadOkBtn = document.getElementById("keypad-ok");
  const keypadBackBtn = document.getElementById("keypad-back");
  const keypadMhzPreview = document.getElementById("keypad-mhz-preview");
  const keypadUnitMhzBtn = document.getElementById("keypad-unit-mhz");
  const keypadUnitKhzBtn = document.getElementById("keypad-unit-khz");
  const KEYPAD_UNIT_STORAGE_KEY = "sdrduo_keypad_unit";
  const KEYPAD_UNIT_MULTIPLIER = { MHz: 1e6, kHz: 1e3 };
  let keypadDigits = "";
  // Typing raw Hz digits (e.g. "100898" meaning 100.898 MHz) with no
  // decimal point was the exact trap that silently mistuned 1000x low
  // earlier -- entry is unit-aware now, defaulting to MHz, so the digits
  // typed are always in an explicit, visible unit rather than an assumed
  // one.
  let keypadUnit = ["MHz", "kHz"].includes(localStorage.getItem(KEYPAD_UNIT_STORAGE_KEY))
    ? localStorage.getItem(KEYPAD_UNIT_STORAGE_KEY)
    : "MHz";

  function keypadIsOpen() {
    return !keypadOverlay.classList.contains("hidden");
  }

  function keypadDigitsToHz() {
    const value = keypadDigits === "" || keypadDigits === "." ? 0 : parseFloat(keypadDigits);
    return value * KEYPAD_UNIT_MULTIPLIER[keypadUnit];
  }

  function updateKeypadUnitButtons() {
    keypadUnitMhzBtn.classList.toggle("active", keypadUnit === "MHz");
    keypadUnitKhzBtn.classList.toggle("active", keypadUnit === "kHz");
  }

  function updateKeypadDisplay() {
    keypadDisplay.textContent = (keypadDigits || "0") + " " + keypadUnit;
    keypadMhzPreview.textContent = "= " + (keypadDigitsToHz() / 1e6).toFixed(6) + " MHz";
  }

  function setKeypadUnit(unit) {
    keypadUnit = unit;
    localStorage.setItem(KEYPAD_UNIT_STORAGE_KEY, unit);
    updateKeypadUnitButtons();
    updateKeypadDisplay();
  }

  function openKeypad() {
    keypadDigits = "";
    updateKeypadUnitButtons();
    updateKeypadDisplay();
    keypadOverlay.classList.remove("hidden");
  }

  function closeKeypad() {
    keypadOverlay.classList.add("hidden");
  }

  function keypadPressDigit(d) {
    const digitCount = (keypadDigits.match(/\d/g) || []).length;
    if (digitCount >= MAX_KEYPAD_DIGITS) return;
    // No leading zeros ahead of the decimal point (avoids a "0"-prefixed
    // digit string reading oddly), but "0." for a sub-1 value is fine.
    keypadDigits = keypadDigits === "0" ? d : keypadDigits + d;
    updateKeypadDisplay();
  }

  function keypadPressDecimal() {
    if (keypadDigits.includes(".")) return; // only one decimal point
    keypadDigits = (keypadDigits || "0") + ".";
    updateKeypadDisplay();
  }

  function keypadBackspace() {
    keypadDigits = keypadDigits.slice(0, -1);
    updateKeypadDisplay();
  }

  function keypadClear() {
    keypadDigits = "";
    updateKeypadDisplay();
  }

  function keypadCommit() {
    const hz = Math.round(keypadDigitsToHz());
    if (hz > 0) send({ cmd: "set_freq", hz });
    closeKeypad();
  }

  // freqInput is inside #freq-block, so one listener on the block catches
  // clicks on the input, the unit label, and the surrounding padding.
  document.getElementById("freq-block").addEventListener("click", openKeypad);

  keypadUnitMhzBtn.addEventListener("click", () => {
    keypadUnitMhzBtn.blur();
    setKeypadUnit("MHz");
  });
  keypadUnitKhzBtn.addEventListener("click", () => {
    keypadUnitKhzBtn.blur();
    setKeypadUnit("kHz");
  });

  keypadGrid.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    // A clicked <button> keeps keyboard focus afterward; if the user then
    // presses Enter, the browser's native "Enter activates the focused
    // button" behavior would re-fire *this* button (re-clicking whatever
    // digit was pressed last) on top of -- or instead of -- our own Enter
    // handler below. Blurring removes that focus so Enter only ever goes
    // through the document-level handler.
    btn.blur();
    const key = btn.dataset.key;
    if (key === "clear") keypadClear();
    else if (key === ".") keypadPressDecimal();
    else keypadPressDigit(key);
  });
  keypadBackBtn.addEventListener("click", () => {
    keypadBackBtn.blur();
    keypadBackspace();
  });
  keypadCancelBtn.addEventListener("click", () => {
    keypadCancelBtn.blur();
    closeKeypad();
  });
  keypadOkBtn.addEventListener("click", () => {
    keypadOkBtn.blur();
    keypadCommit();
  });
  keypadOverlay.addEventListener("click", (e) => {
    if (e.target === keypadOverlay) closeKeypad(); // click on the backdrop, not the panel
  });

  document.addEventListener("keydown", (e) => {
    if (!keypadIsOpen()) return;
    if (e.key >= "0" && e.key <= "9") keypadPressDigit(e.key);
    else if (e.key === ".") keypadPressDecimal();
    else if (e.key === "Backspace") keypadBackspace();
    else if (e.key === "Enter") keypadCommit();
    else if (e.key === "Escape") closeKeypad();
    else return;
    e.preventDefault();
  });

  // ---------------- controls ----------------
  grdbSlider.addEventListener("input", () => {
    grdbVal.textContent = grdbSlider.value;
    send({ cmd: "set_gain", grdb: parseInt(grdbSlider.value, 10) });
  });
  lnaSlider.addEventListener("input", () => {
    lnaVal.textContent = lnaSlider.value;
    send({ cmd: "set_gain", lnaState: parseInt(lnaSlider.value, 10) });
  });
  agcToggle.addEventListener("change", () => {
    send({ cmd: "set_agc", enabled: agcToggle.checked });
  });
  sqlToggle.addEventListener("change", () => {
    send({ cmd: "set_squelch", enabled: sqlToggle.checked, thresholdDbfs: parseFloat(sqlSlider.value) });
  });
  sqlSlider.addEventListener("input", () => {
    sqlVal.textContent = sqlSlider.value;
    send({ cmd: "set_squelch", enabled: sqlToggle.checked, thresholdDbfs: parseFloat(sqlSlider.value) });
  });
  volSlider.addEventListener("input", () => {
    volVal.textContent = volSlider.value + "%";
    if (gainNode) gainNode.gain.value = volSlider.value / 100;
  });

  // Floor/ceiling control both the spectrum trace's vertical scale and the
  // waterfall's color mapping -- narrowing the range is what makes weak
  // signals stand out (more contrast/"gain" in the display, not the RF).
  floorSlider.addEventListener("input", () => {
    let v = parseInt(floorSlider.value, 10);
    if (v > state.dbMax - MIN_DB_RANGE) v = state.dbMax - MIN_DB_RANGE;
    state.dbMin = v;
    floorSlider.value = v;
    floorVal.textContent = v;
    if (lastSpectrum) drawSpectrum(lastSpectrum);
  });
  ceilSlider.addEventListener("input", () => {
    let v = parseInt(ceilSlider.value, 10);
    if (v < state.dbMin + MIN_DB_RANGE) v = state.dbMin + MIN_DB_RANGE;
    state.dbMax = v;
    ceilSlider.value = v;
    ceilVal.textContent = v;
    if (lastSpectrum) drawSpectrum(lastSpectrum);
  });

  // ---------------- spectrum / waterfall ----------------
  function resizeCanvases() {
    const dpr = window.devicePixelRatio || 1;
    for (const c of [specCanvas, wfCanvas]) {
      const cssW = c.clientWidth || 1;
      const cssH = c.clientHeight || 1;
      c.width = Math.round(cssW * dpr);
      c.height = Math.round(cssH * dpr);
    }
    // Resizing a canvas's width/height attribute resets its 2D context
    // state, including imageSmoothingEnabled -- reapply after every resize.
    wfCtx.imageSmoothingEnabled = false;
    if (lastSpectrum) drawSpectrum(lastSpectrum);
  }
  window.addEventListener("resize", resizeCanvases);

  // "Nice" 1-2-5 tick spacing so grid lines land on round numbers
  // regardless of the selected span.
  function niceTickSpacing(spanHz, targetTicks) {
    const rough = spanHz / targetTicks;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    for (const c of [1, 2, 5, 10]) {
      if (rough <= c * mag) return c * mag;
    }
    return 10 * mag;
  }

  function formatTickLabel(freqHz, spacingHz) {
    const decimals =
      spacingHz >= 1e6 ? 0 :
      spacingHz >= 1e5 ? 1 :
      spacingHz >= 1e4 ? 2 :
      spacingHz >= 1e3 ? 3 :
      spacingHz >= 1e2 ? 4 : 6;
    return (freqHz / 1e6).toFixed(decimals);
  }

  function drawFreqAxis(ctx, w, plotH, axisH, loFreq, span, dpr) {
    const hiFreq = loFreq + span;
    const spacing = niceTickSpacing(span, 6);
    const firstTick = Math.ceil(loFreq / spacing) * spacing;

    ctx.fillStyle = "#0a0f16";
    ctx.fillRect(0, plotH, w, axisH);
    ctx.strokeStyle = "#23303f";
    ctx.beginPath();
    ctx.moveTo(0, plotH);
    ctx.lineTo(w, plotH);
    ctx.stroke();

    ctx.fillStyle = "#9fb0c0";
    ctx.font = `${Math.round(11 * dpr)}px Consolas, monospace`;
    ctx.textBaseline = "top";

    for (let f = firstTick; f <= hiFreq; f += spacing) {
      const x = ((f - loFreq) / span) * w;

      ctx.strokeStyle = "#1c2733";
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, plotH);
      ctx.stroke();

      ctx.strokeStyle = "#3a4a5c";
      ctx.beginPath();
      ctx.moveTo(x, plotH);
      ctx.lineTo(x, plotH + 4 * dpr);
      ctx.stroke();

      const label = formatTickLabel(f, spacing) + " MHz";
      const tw = ctx.measureText(label).width;
      const lx = Math.max(2, Math.min(w - tw - 2, x - tw / 2));
      ctx.fillText(label, lx, plotH + 5 * dpr);
    }
  }

  function drawSpectrum(fullSpec) {
    const w = specCanvas.width, hTotal = specCanvas.height;
    if (w === 0 || hTotal === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const axisH = Math.round(AXIS_HEIGHT_CSS * dpr);
    const h = Math.max(1, hTotal - axisH);
    const ctx = specCtx;

    ctx.fillStyle = "#05080b";
    ctx.fillRect(0, 0, w, hTotal);

    // Crop the full-bandwidth spectrum down to the selected span, centered
    // on the tuned frequency (the acquisition itself is always the full
    // RAW_FS width -- span is purely a display zoom).
    const fftN = fullSpec.length;
    const binHz = state.sampleRateHz / fftN;
    const span = Math.min(state.spanHz, state.sampleRateHz);
    const spanBins = Math.min(fftN, Math.max(2, Math.round(span / binHz)));
    const startBin = Math.floor((fftN - spanBins) / 2);
    const spec = fullSpec.subarray(startBin, startBin + spanBins);
    const loFreq = state.freqHz - span / 2;

    ctx.strokeStyle = "#1c2733";
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = (h * i) / 4;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    const n = spec.length;
    ctx.beginPath();
    ctx.strokeStyle = "#35d0ba";
    // A true 1-device-pixel stroke is the crispest a canvas line can be --
    // the old `w / 800` scaled with the canvas's *physical* width, so on a
    // 150%-scaled display (devicePixelRatio 1.5) the line rendered 2px+
    // thick with heavy anti-aliasing, reading as a blurry trace.
    ctx.lineWidth = 1;
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * w;
      const norm = (spec[i] - state.dbMin) / (state.dbMax - state.dbMin);
      const y = h - Math.max(0, Math.min(1, norm)) * h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    ctx.strokeStyle = "rgba(224,160,48,0.6)";
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2, h);
    ctx.stroke();

    drawFreqAxis(ctx, w, h, axisH, loFreq, span, dpr);

    pushWaterfallRow(spec);
  }

  function colormap(t) {
    const stops = [
      [0.0, [8, 8, 40]],
      [0.35, [20, 90, 200]],
      [0.55, [40, 200, 180]],
      [0.75, [230, 210, 40]],
      [1.0, [230, 40, 40]],
    ];
    for (let i = 1; i < stops.length; i++) {
      if (t <= stops[i][0]) {
        const [t0, c0] = stops[i - 1];
        const [t1, c1] = stops[i];
        const f = (t - t0) / (t1 - t0 || 1);
        return [
          Math.round(c0[0] + (c1[0] - c0[0]) * f),
          Math.round(c0[1] + (c1[1] - c0[1]) * f),
          Math.round(c0[2] + (c1[2] - c0[2]) * f),
        ];
      }
    }
    return [230, 40, 40];
  }

  function pushWaterfallRow(spec) {
    const w = wfCanvas.width, h = wfCanvas.height;
    if (w === 0 || h === 0) return;

    wfCtx.drawImage(wfCanvas, 0, 0, w, h - 1, 0, 1, w, h - 1);

    const n = spec.length;
    wfRowCanvas.width = n;
    wfRowCanvas.height = 1;
    const rctx = wfRowCanvas.getContext("2d");
    const imgData = rctx.createImageData(n, 1);
    for (let i = 0; i < n; i++) {
      const norm = Math.max(0, Math.min(1, (spec[i] - state.dbMin) / (state.dbMax - state.dbMin)));
      const [r, g, b] = colormap(norm);
      imgData.data[i * 4 + 0] = r;
      imgData.data[i * 4 + 1] = g;
      imgData.data[i * 4 + 2] = b;
      imgData.data[i * 4 + 3] = 255;
    }
    rctx.putImageData(imgData, 0, 0);
    wfCtx.drawImage(wfRowCanvas, 0, 0, n, 1, 0, 0, w, 1);
  }

  function canvasXToFreq(canvas, clientX) {
    const rect = canvas.getBoundingClientRect();
    const frac = (clientX - rect.left) / rect.width;
    const span = Math.min(state.spanHz, state.sampleRateHz);
    return state.freqHz + (frac - 0.5) * span;
  }

  specCanvas.addEventListener("click", (e) => {
    send({ cmd: "set_freq", hz: Math.round(canvasXToFreq(specCanvas, e.clientX)) });
  });
  wfCanvas.addEventListener("click", (e) => {
    send({ cmd: "set_freq", hz: Math.round(canvasXToFreq(wfCanvas, e.clientX)) });
  });

  function onTuneWheel(e) {
    e.preventDefault();
    const step = state.stepHz;
    const dir = -Math.sign(e.deltaY);
    // Snap the *current* frequency to the step grid first, then move one
    // step -- so a leftover off-grid frequency (e.g. from click-tuning)
    // always lands back on a clean multiple of the selected step.
    const rounded = Math.round(state.freqHz / step) * step;
    send({ cmd: "set_freq", hz: rounded + dir * step });
  }
  specCanvas.addEventListener("wheel", onTuneWheel, { passive: false });
  wfCanvas.addEventListener("wheel", onTuneWheel, { passive: false });

  // ---------------- audio ----------------
  let audioCtx = null;
  let gainNode = null;
  let nextStartTime = 0;
  // Our own source of truth for on/off, rather than reading audioCtx.state
  // right after an unawaited resume()/suspend() call -- that state
  // transition is asynchronous, so a click shortly after the previous one
  // could read stale state and get the toggle stuck.
  let audioEnabled = false;
  // Sources scheduled but not yet finished playing, so "disable" can hard-stop
  // them immediately -- suspend() alone stops new processing but doesn't
  // cancel already-scheduled nodes, and if scheduling had drifted ahead of
  // real time (see MAX_SCHEDULE_AHEAD_S below) they'd otherwise keep
  // audibly playing out after the button says "off".
  let scheduledSources = [];
  const MAX_SCHEDULE_AHEAD_S = 0.3; // drop incoming audio rather than queue unbounded latency

  function ensureAudio() {
    if (audioCtx) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    gainNode = audioCtx.createGain();
    gainNode.gain.value = volSlider.value / 100;
    gainNode.connect(audioCtx.destination);
    nextStartTime = audioCtx.currentTime + 0.1;
  }

  function stopAllScheduled() {
    for (const src of scheduledSources) {
      try {
        src.stop();
      } catch {
        // already ended -- fine
      }
    }
    scheduledSources = [];
  }

  audioBtn.addEventListener("click", () => {
    ensureAudio();
    audioEnabled = !audioEnabled;
    if (audioEnabled) {
      audioCtx.resume();
      // Reset scheduling rather than trusting currentTime to have stayed
      // put across the suspend -- avoids trying to start buffers in the past.
      nextStartTime = audioCtx.currentTime + 0.1;
      audioBtn.textContent = "Mute Audio";
      audioBtn.classList.add("active");
    } else {
      stopAllScheduled();
      audioCtx.suspend();
      audioBtn.textContent = "Enable Audio";
      audioBtn.classList.remove("active");
    }
  });

  exitBtn.addEventListener("click", () => {
    if (!confirm("Stop the SDRduo Display server?\n\nYou'll need to relaunch it to use the receiver again.")) {
      return;
    }
    exitBtn.disabled = true;
    exitBtn.textContent = "Stopping...";
    intentionalShutdown = true;

    // Stop audio immediately rather than let it trail off while the
    // server's still tearing down.
    if (audioEnabled) {
      stopAllScheduled();
      if (audioCtx) audioCtx.suspend();
      audioEnabled = false;
    }

    // The server intentionally delays its actual exit slightly so this
    // response has time to make it back here -- but fetch() itself can
    // still fail if the connection drops mid-flight, which isn't an
    // error worth surfacing (the shutdown is happening either way).
    fetch("/api/shutdown", { method: "POST" }).catch(() => {});
    shutdownOverlay.classList.remove("hidden");
  });

  function playAudio(int16) {
    if (!audioEnabled || !audioCtx) return;
    const n = int16.length;
    if (n === 0) return;
    const now = audioCtx.currentTime;
    if (nextStartTime > now + MAX_SCHEDULE_AHEAD_S) return; // already queued enough, drop rather than build latency
    const buf = audioCtx.createBuffer(1, n, state.audioRateHz);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = int16[i] / 32768;
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(gainNode);
    src.onended = () => {
      const idx = scheduledSources.indexOf(src);
      if (idx !== -1) scheduledSources.splice(idx, 1);
    };
    scheduledSources.push(src);
    if (nextStartTime < now + 0.02) nextStartTime = now + 0.05;
    src.start(nextStartTime);
    nextStartTime += buf.duration;
  }

  // ---------------- boot ----------------
  buildSpanButtons();
  buildStepButtons();
  buildMemButtons();
  connect();
  // Called directly (not via requestAnimationFrame) since rAF can be
  // throttled indefinitely in a backgrounded/non-composited tab, which
  // would leave the canvas stuck at its default 300px buffer width.
  resizeCanvases();
})();
