/**
 * settings.js - Settings Panel Logic
 * Changes are sent over WebSocket → subtitle.html receives them in real-time.
 * No localStorage dependency (avoids OBS isolation issue).
 */

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
    textAlign: "left",
    maxCharsPerLine: 40,
    typewriterOn: true,
    typewriterSpeed: 35,
    displayDuration: 8000,
    maxBlocks: 3,
    showStatus: true,
    reconnectInterval: 3000,
};

// ── WebSocket (to relay settings) ──────────────────────────────────
let ws = null;
let wsReady = false;
const WS_STATUS = document.getElementById("ws-status");

function connectWS(url) {
    if (ws) { try { ws.close(); } catch (_) { } }
    ws = new WebSocket(url);
    ws.addEventListener("open", () => { wsReady = true; setWsStatus("connected"); });
    ws.addEventListener("close", () => { wsReady = false; setWsStatus("disconnected"); setTimeout(() => connectWS(url), 3000); });
    ws.addEventListener("error", () => { wsReady = false; });
}

function setWsStatus(state) {
    if (!WS_STATUS) return;
    const labels = { connected: "● サーバー接続中", disconnected: "● 切断中" };
    WS_STATUS.textContent = labels[state] || state;
    WS_STATUS.className = "ws-status " + state;
}

function sendSettings(S) {
    // Also persist locally so settings survive page reload
    localStorage.setItem("subtitle_settings_panel", JSON.stringify(S));
    if (!wsReady) { setWsStatus("disconnected"); return; }
    ws.send(JSON.stringify({ type: "settings_update", settings: S }));
}

// ── Load/Save ───────────────────────────────────────────────────────
function loadSettings() {
    try {
        const saved = localStorage.getItem("subtitle_settings_panel");
        if (saved) return Object.assign({}, DEFAULTS, JSON.parse(saved));
    } catch (e) { }
    return Object.assign({}, DEFAULTS);
}

function readUI() {
    return {
        wsUrl: document.getElementById("wsUrl").value.trim(),
        showOriginal: document.getElementById("showOriginal").checked,
        showTranslated: document.getElementById("showTranslated").checked,
        showStatus: document.getElementById("showStatus").checked,
        textAlign: document.querySelector("input[name=textAlign]:checked")?.value || "left",
        maxBlocks: parseInt(document.getElementById("maxBlocks").value),
        maxCharsPerLine: parseInt(document.getElementById("maxCharsPerLine").value),
        displayDuration: parseInt(document.getElementById("displayDuration").value),
        fontFamily: document.getElementById("fontFamily").value,
        fontSizeOrig: parseInt(document.getElementById("fontSizeOrig").value),
        fontSizeTrans: parseInt(document.getElementById("fontSizeTrans").value),
        colorOrig: document.getElementById("colorOrig").value,
        colorTrans: document.getElementById("colorTrans").value,
        outlineColor: document.getElementById("outlineColor").value,
        outlineWidth: parseInt(document.getElementById("outlineWidth").value),
        typewriterOn: document.getElementById("typewriterOn").checked,
        typewriterSpeed: parseInt(document.getElementById("typewriterSpeed").value),
        reconnectInterval: 3000,
    };
}

function applyToUI(S) {
    document.getElementById("wsUrl").value = S.wsUrl;
    document.getElementById("showOriginal").checked = S.showOriginal;
    document.getElementById("showTranslated").checked = S.showTranslated;
    document.getElementById("showStatus").checked = S.showStatus;
    document.getElementById("maxBlocks").value = S.maxBlocks;
    document.getElementById("maxCharsPerLine").value = S.maxCharsPerLine;
    document.getElementById("displayDuration").value = S.displayDuration;
    document.getElementById("fontFamily").value = S.fontFamily;
    document.getElementById("fontSizeOrig").value = S.fontSizeOrig;
    document.getElementById("fontSizeTrans").value = S.fontSizeTrans;
    document.getElementById("colorOrig").value = S.colorOrig;
    document.getElementById("colorTrans").value = S.colorTrans;
    document.getElementById("outlineColor").value = S.outlineColor;
    document.getElementById("outlineWidth").value = S.outlineWidth;
    document.getElementById("typewriterOn").checked = S.typewriterOn;
    document.getElementById("typewriterSpeed").value = S.typewriterSpeed;
    const radioAlign = document.querySelector(`input[name=textAlign][value="${S.textAlign}"]`);
    if (radioAlign) radioAlign.checked = true;
    refreshLabels(S);
    updatePreview(S);
}

function refreshLabels(S) {
    document.getElementById("maxBlocks-val").textContent = S.maxBlocks;
    document.getElementById("maxCharsPerLine-val").textContent = S.maxCharsPerLine === 0 ? "無制限" : S.maxCharsPerLine;
    document.getElementById("displayDuration-val").textContent = S.displayDuration === 0 ? "OFF" : (S.displayDuration / 1000).toFixed(1) + " 秒";
    document.getElementById("fontSizeOrig-val").textContent = S.fontSizeOrig + " px";
    document.getElementById("fontSizeTrans-val").textContent = S.fontSizeTrans + " px";
    document.getElementById("outlineWidth-val").textContent = S.outlineWidth + " px";
    document.getElementById("typewriterSpeed-val").textContent = S.typewriterSpeed + " ms";
    document.getElementById("colorOrig-hex").textContent = S.colorOrig;
    document.getElementById("colorTrans-hex").textContent = S.colorTrans;
    document.getElementById("outlineColor-hex").textContent = S.outlineColor;
}

function buildShadow(S) {
    const w = Number(S.outlineWidth) || 0;
    const c = S.outlineColor || "#000";
    if (w <= 0) return "none";
    return `${-w}px ${-w}px 0 ${c}, ${w}px ${-w}px 0 ${c}, ${-w}px ${w}px 0 ${c}, ${w}px ${w}px 0 ${c}, 0 4px 14px rgba(0,0,0,.7)`;
}

function updatePreview(S) {
    const o = document.getElementById("preview-original");
    const t = document.getElementById("preview-translated");
    const sh = buildShadow(S);
    [o, t].forEach(el => {
        el.style.fontFamily = `'${S.fontFamily}', 'Noto Sans JP', 'Inter', sans-serif`;
        el.style.textAlign = S.textAlign;
        el.style.textShadow = sh;
    });
    o.style.fontSize = S.fontSizeOrig + "px"; o.style.color = S.colorOrig; o.style.display = S.showOriginal ? "block" : "none";
    t.style.fontSize = S.fontSizeTrans + "px"; t.style.color = S.colorTrans; t.style.display = S.showTranslated ? "block" : "none";
}

// ── Wire controls ───────────────────────────────────────────────────
function wireControls() {
    const onChange = () => {
        const S = readUI();
        refreshLabels(S);
        updatePreview(S);
        // Reconnect WS if URL changed
        if (S.wsUrl !== ws?.url) connectWS(S.wsUrl);
        sendSettings(S);
    };

    document.querySelectorAll("input, select").forEach(el => {
        el.addEventListener("input", onChange);
        el.addEventListener("change", onChange);
    });

    document.getElementById("btn-reset").addEventListener("click", () => {
        applyToUI(DEFAULTS);
        sendSettings(DEFAULTS);
    });

    document.getElementById("btn-preview").addEventListener("click", () => {
        if (!wsReady) { alert("WebSocketサーバーに接続していません。\npython src/main.py を起動してください。"); return; }
        ws.send(JSON.stringify({
            type: "test_subtitle",
            original: "雀魂で対局中！絶対上がるぞ！",
            translated: "Playing Mahjong Soul! I'm definitely winning this one!",
        }));
        const btn = document.getElementById("btn-preview");
        btn.textContent = "送信済み!";
        btn.style.background = "#4ade80";
        setTimeout(() => { btn.textContent = "テスト送信"; btn.style.background = ""; }, 1200);
    });
}

// ── Init ────────────────────────────────────────────────────────────
const S = loadSettings();
applyToUI(S);
wireControls();
connectWS(S.wsUrl);
