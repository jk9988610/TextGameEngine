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

    // 显示后端输出
    const outputDiv = document.createElement('div');
    outputDiv.className = data.output.includes('❌') || data.output.includes('❓') ? 'error' : 'engine-output';
    outputDiv.textContent = data.output;
    consoleLog.appendChild(outputDiv);

    // 滚动到底部
    consoleLog.scrollTop = consoleLog.scrollHeight;
    // 刷新游戏状态（控制台命令也会改变状态）
    fetchState();
}
