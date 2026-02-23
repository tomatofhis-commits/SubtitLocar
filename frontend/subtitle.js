/**
 * subtitle.js - CSS Variable-driven subtitle display
 * Supports: settings_update, clear_subtitles, test_subtitle messages
 */

(function () {
  "use strict";

  const DEFAULTS = {
    wsUrl: "ws://localhost:8765",
    showOriginal: true,
    showTranslated: true,
    fontFamily: "Noto Sans JP",
    fontSizeOrig: 36,
    fontSizeTrans: 46,
    colorOrig: "#ffe066",
    colorTrans: "#ffffff",
    outlineWidth: 2,
    outlineColor: "#000000",
    fontFamilySecreAI: "Noto Sans JP",
    colorOrigSecreAI: "#ffb3b3",
    colorTransSecreAI: "#ff99ff",
    textAlign: "left",
    verticalPos: "bottom",        // top / center_v / bottom
    maxCharsPerLine: 40,
    typewriterOn: true,
    typewriterSpeed: 35,
    displayDuration: 8000,
    maxBlocks: 3,
    showStatus: true,
    reconnectInterval: 3000,
  };

  let S = Object.assign({}, DEFAULTS);
  let ws = null;
  let reconnectTimer = null;
  let blocks = [];
  let twTimers = [];
  let hideTimers = [];

  const container = document.getElementById("subtitle-container");
  const statusEl = document.getElementById("connection-status");
  const root = document.documentElement;

  // ── CSS Variable helpers ──────────────────────────────────────────

  function buildShadow() {
    const w = Number(S.outlineWidth) || 0;
    const c = S.outlineColor || "#000";
    if (w <= 0) return "none";
    return `${-w}px ${-w}px 0 ${c}, ${w}px ${-w}px 0 ${c}, ${-w}px ${w}px 0 ${c}, ${w}px ${w}px 0 ${c}, 0 4px 14px rgba(0,0,0,.7)`;
  }

  function fontStack() {
    return `'${S.fontFamily}', 'Noto Sans JP', 'Inter', sans-serif`;
  }

  function applySettings() {
    // CSS variables → ALL current and future elements update instantly
    root.style.setProperty("--orig-font", fontStack());
    root.style.setProperty("--trans-font", fontStack());
    root.style.setProperty("--orig-size", S.fontSizeOrig + "px");
    root.style.setProperty("--trans-size", S.fontSizeTrans + "px");
    root.style.setProperty("--orig-color", S.colorOrig);
    root.style.setProperty("--trans-color", S.colorTrans);
    root.style.setProperty("--text-shadow", buildShadow());
    root.style.setProperty("--text-align", S.textAlign);
    root.style.setProperty("--show-orig", S.showOriginal ? "block" : "none");
    root.style.setProperty("--show-trans", S.showTranslated ? "block" : "none");

    // Horizontal alignment
    if (S.textAlign === "center") {
      container.style.left = "5%"; container.style.right = "5%";
    } else if (S.textAlign === "right") {
      container.style.left = "5%"; container.style.right = "40px";
    } else {
      container.style.left = "40px"; container.style.right = "5%";
    }

    // Vertical position: top / center_v / bottom
    if (S.verticalPos === "top") {
      container.style.top = "80px";
      container.style.bottom = "auto";
      container.style.transform = "none";
    } else if (S.verticalPos === "center_v") {
      container.style.top = "50%";
      container.style.bottom = "auto";
      container.style.transform = "translateY(-50%)";
    } else {  // bottom (default)
      container.style.bottom = "80px";
      container.style.top = "auto";
      container.style.transform = "none";
    }

    if (statusEl) statusEl.style.display = S.showStatus ? "" : "none";
  }

  // ── WebSocket ─────────────────────────────────────────────────────

  function connect() {
    if (ws && ws.readyState <= WebSocket.OPEN) return;
    setStatus("connecting");
    try {
      ws = new WebSocket(S.wsUrl);
    } catch (e) { scheduleReconnect(); return; }
    ws.addEventListener("open", () => { setStatus("connected"); clearTimeout(reconnectTimer); reconnectTimer = null; });
    ws.addEventListener("message", onMessage);
    ws.addEventListener("close", () => { setStatus("disconnected"); scheduleReconnect(); });
    ws.addEventListener("error", () => { try { ws.close(); } catch (_) { } });
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, S.reconnectInterval);
  }

  function setStatus(state) {
    if (!statusEl) return;
    statusEl.textContent = { connected: "● LIVE", disconnected: "● Offline", connecting: "● Connecting..." }[state] || state;
    statusEl.className = state;
  }

  function onMessage(event) {
    try {
      const data = JSON.parse(event.data);
      const t = data.type || "";
      if (t === "connected") return;
      if (t === "settings_update") { S = Object.assign({}, DEFAULTS, data.settings); applySettings(); return; }
      if (t === "clear_subtitles") { clearAllBlocks(); return; }
      if (data.original !== undefined || data.translated !== undefined) {
        addBlock(data.original || "", data.translated || "", data.source || "mic");
      }
    } catch (e) { console.error("subtitle.js:", e); }
  }

  // ── Text helpers ──────────────────────────────────────────────────

  function wrapText(text, maxChars) {
    if (!maxChars || text.length <= maxChars) return text;
    const result = []; let line = "";
    for (const ch of [...text]) {
      if (line.length >= maxChars) { result.push(line); line = ""; }
      line += ch;
    }
    if (line) result.push(line);
    return result.join("\n");
  }

  function spawnChars(el, text) {
    text.split("\n").forEach((line, i) => {
      if (i > 0) el.appendChild(document.createElement("br"));
      [...line].forEach(ch => {
        const span = document.createElement("span");
        span.classList.add("char");
        span.textContent = ch === " " ? "\u00a0" : ch;
        el.appendChild(span);
      });
    });
  }

  // ── Block management ──────────────────────────────────────────────

  function addBlock(original, translated, source = "mic") {
    twTimers.forEach(t => clearTimeout(t));
    twTimers = [];
    while (blocks.length >= S.maxBlocks) fadeOutAndRemove(blocks.shift());

    const blockEl = document.createElement("div");
    blockEl.classList.add("subtitle-block");

    const origEl = document.createElement("div");
    origEl.classList.add("subtitle-original");
    // SecreAI専用のフォント・色オーバーライド機構
    if (source === "secreai") {
      origEl.style.setProperty("--orig-color", S.colorOrigSecreAI);
      origEl.style.setProperty("--orig-font", `'${S.fontFamilySecreAI}', 'Noto Sans JP', 'Inter', sans-serif`);
    }
    spawnChars(origEl, wrapText(original, S.maxCharsPerLine));

    const transEl = document.createElement("div");
    transEl.classList.add("subtitle-translated");
    if (source === "secreai") {
      transEl.style.setProperty("--trans-color", S.colorTransSecreAI);
      transEl.style.setProperty("--trans-font", `'${S.fontFamilySecreAI}', 'Noto Sans JP', 'Inter', sans-serif`);
    }
    spawnChars(transEl, wrapText(translated, S.maxCharsPerLine));

    blockEl.appendChild(origEl);
    blockEl.appendChild(transEl);
    container.appendChild(blockEl);
    blocks.push(blockEl);

    requestAnimationFrame(() => {
      blockEl.classList.add("visible");
      if (S.typewriterOn) startTypewriter(blockEl);
      else { blockEl.querySelectorAll(".char").forEach(c => c.classList.add("shown")); scheduleAutoHide(blockEl); }
    });
  }

  function startTypewriter(blockEl) {
    const chars = blockEl.querySelectorAll(".char");
    if (!chars.length) { scheduleAutoHide(blockEl); return; }
    let idx = 0;
    function tick() {
      if (idx >= chars.length) { scheduleAutoHide(blockEl); return; }
      chars[idx++].classList.add("shown");
      twTimers.push(setTimeout(tick, S.typewriterSpeed));
    }
    tick();
  }

  function scheduleAutoHide(blockEl) {
    if (!S.displayDuration) return;
    hideTimers.push(setTimeout(() => {
      blocks = blocks.filter(b => b !== blockEl);
      fadeOutAndRemove(blockEl);
    }, S.displayDuration));
  }

  function fadeOutAndRemove(el) {
    if (!el || !el.parentNode) return;
    el.classList.remove("visible");
    el.classList.add("fading-out");
    el.addEventListener("transitionend", () => { if (el.parentNode) el.parentNode.removeChild(el); }, { once: true });
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 800);
  }

  function clearAllBlocks() {
    twTimers.forEach(t => clearTimeout(t)); twTimers = [];
    hideTimers.forEach(t => clearTimeout(t)); hideTimers = [];
    blocks.forEach(b => fadeOutAndRemove(b)); blocks = [];
  }

  // ── Init ──────────────────────────────────────────────────────────
  applySettings();
  connect();
})();
