/* ==========================================================================
   core.js —— 基础设施层
   包含：全局状态、localStorage client_id、fetch 拦截器、开发者控制台、通用工具
   加载顺序：constants.js 之后、game.js/ui.js 之前（fetch 拦截器必须先于任何请求生效）
   ========================================================================== */

// 全局状态
let consoleVisible = false;

// ---------- localStorage client_id（存档隔离键，清 cookie 也不变） ----------
const CLIENT_ID_KEY = 'game_client_id';
let _clientId = localStorage.getItem(CLIENT_ID_KEY);
if (!_clientId) {
    _clientId = (crypto.randomUUID ? crypto.randomUUID() :
        Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
    localStorage.setItem(CLIENT_ID_KEY, _clientId);
}
// 劫持 fetch，自动给每个请求的 URL 加上 client_id（已有的不重复加）
const _origFetch = window.fetch;
window.fetch = function(url, opts) {
    // 只改相对路径（以 / 开头）的 URL
    if (typeof url === 'string' && url.startsWith('/')) {
        const sep = url.includes('?') ? '&' : '?';
        url = url + sep + 'client_id=' + _clientId;
        // 关键：显式加 mode 参数 —— 从 localStorage 读当前选择的模式
        // 这样后端完全不依赖 Flask session 里存的 mode，彻底消除串档根因
        const savedMode = localStorage.getItem('game_mode');
        if (savedMode === 'offline') {
            url += '&mode=offline';
        } else if (savedMode === 'online') {
            url += '&mode=online';
        }
    }
    return _origFetch.call(this, url, opts);
};

// ---------- 控制台切换逻辑 ----------
const devConsole = document.getElementById('dev-console');
const consoleInput = document.getElementById('console-input');
const consoleLog = document.getElementById('console-log');
const closeConsoleBtn = document.getElementById('close-console');
const consoleSendBtn = document.getElementById('console-send');

// 全局监听 ~ 键（keyCode=192）切换控制台
document.addEventListener('keydown', (e) => {
    if (e.keyCode === 192) { // ~ 键
        e.preventDefault();  // 阻止默认的波形符输入
        toggleConsole();
    }
    // 控制台显示时，回车发送命令
    if (e.keyCode === 13 && consoleVisible) {
        e.preventDefault();
        const cmd = consoleInput.value.trim();
        if (cmd) sendConsoleCommand(cmd);
        consoleInput.value = '';
    }
    // ESC 关闭控制台
    if (e.keyCode === 27 && consoleVisible) {
        toggleConsole();
    }
});

// 关闭按钮
closeConsoleBtn.addEventListener('click', () => toggleConsole());
// 发送按钮
consoleSendBtn.addEventListener('click', () => {
    const cmd = consoleInput.value.trim();
    if (cmd) sendConsoleCommand(cmd);
    consoleInput.value = '';
});

function toggleConsole() {
    consoleVisible = !consoleVisible;
    devConsole.style.display = consoleVisible ? 'flex' : 'none';
    if (consoleVisible) consoleInput.focus();
}

// ---------- 通用工具 ----------
// action message 3 秒自动消失（统一入口）
let _msgTimer = null;
function showActionMsg(text) {
    const el = document.getElementById('action-message');
    el.textContent = text;
    clearTimeout(_msgTimer);
    _msgTimer = setTimeout(() => { el.textContent = ''; }, 3000);
}

// ---------- 开发者控制台命令发送 ----------
async function sendConsoleCommand(cmd) {
    /** 发送开发者命令到后端 */
    // 显示玩家输入
    const inputDiv = document.createElement('div');
    inputDiv.className = 'dev-cmd';
    inputDiv.textContent = `> ${cmd}`;
    consoleLog.appendChild(inputDiv);

    // 调用后端
    const res = await fetch('/api/console', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd })
    });
    const data = await res.json();

    // 显示后端输出（错误输出统一以「错误：」开头，用于着色）
    const outputDiv = document.createElement('div');
    outputDiv.className = data.output.startsWith('错误：') ? 'error' : 'engine-output';
    outputDiv.textContent = data.output;
    consoleLog.appendChild(outputDiv);

    // 滚动到底部
    consoleLog.scrollTop = consoleLog.scrollHeight;
    // 刷新游戏状态（控制台命令也会改变状态）
    fetchState();
}

// ---------- 禁止复制类操作（输入框/文本域内豁免，不影响填表单和输命令） ----------
function _isFormField(node) {
    return !!(node && node.closest && node.closest('input, textarea, [contenteditable="true"]'));
}
document.addEventListener('contextmenu', (e) => {
    if (!_isFormField(e.target)) e.preventDefault();
});
for (const _ev of ['copy', 'cut']) {
    document.addEventListener(_ev, (e) => {
        if (!_isFormField(e.target)) e.preventDefault();
    });
}

// ---------- 通用弹窗拖动 ----------
// 设计要点（单一拖拽路径，避免与点击冲突）：
// 1) 只有指定的"把手"（标题栏）响应 pointerdown；
// 2) 把手内的按钮（如 ✕）通过 closest('button') 排除，点按钮绝不触发拖拽；
// 3) 拖拽开始瞬间把面板从"CSS/flex 居中"切换为 fixed+具体坐标（取当前实际位置），
//    因此不会出现跳动；再次打开弹窗时调 resetModalPosition() 清掉内联坐标即可回中。
function resetModalPosition(el) {
    /** 弹窗重新打开时调用：清除拖动写入的内联定位，恢复样式表定义的居中 */
    el.style.position = '';
    el.style.left = '';
    el.style.top = '';
    el.style.margin = '';
    el.style.transform = '';
}

function makeDraggable(el, handle) {
    /** 让 el 可以按住 handle 拖动；自动限制在视口内，标题栏始终可见可抓 */
    let dragging = false;
    let startX = 0, startY = 0, startLeft = 0, startTop = 0;

    handle.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;  // 鼠标只认左键
        if (e.target.closest('button, input, a, textarea')) return;  // 交互控件不拖拽

        const rect = el.getBoundingClientRect();
        dragging = true;
        startX = e.clientX;
        startY = e.clientY;
        startLeft = rect.left;
        startTop = rect.top;

        // 从居中态切换为 fixed 绝对定位（值就是当前渲染位置，视觉无跳动）
        el.style.position = 'fixed';
        el.style.margin = '0';
        el.style.left = startLeft + 'px';
        el.style.top = startTop + 'px';
        el.style.transform = 'none';

        try { handle.setPointerCapture(e.pointerId); } catch (_) {}
        e.preventDefault();
    });

    handle.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        let nx = startLeft + (e.clientX - startX);
        let ny = startTop + (e.clientY - startY);
        // 边界约束：左右各至少留 80px、顶部不越界、底部至少露 44px 标题栏
        nx = Math.min(Math.max(nx, -el.offsetWidth + 80), window.innerWidth - 80);
        ny = Math.min(Math.max(ny, 0), window.innerHeight - 44);
        el.style.left = nx + 'px';
        el.style.top = ny + 'px';
    });

    const stop = () => { dragging = false; };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
}

// 绑定四个浮动弹窗（DOM 在脚本加载时已就绪，因为 script 标签在 body 末尾）
(function initDraggablePanels() {
    const dialoguePanel = document.getElementById('dialogue-panel');
    const combatPanel = document.getElementById('combat-panel');
    const savePanel = document.getElementById('save-panel');
    const inventoryCard = document.getElementById('inventory-card');
    if (dialoguePanel) makeDraggable(dialoguePanel, dialoguePanel.querySelector('.dialogue-header'));
    if (combatPanel) makeDraggable(combatPanel, combatPanel.querySelector('.combat-header'));
    if (savePanel) makeDraggable(savePanel, savePanel.querySelector('h3'));
    if (inventoryCard) makeDraggable(inventoryCard, inventoryCard.querySelector('h3'));
})();
