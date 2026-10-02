/* ==========================================================================
   ui.js —— 界面与流程控制：模式选择、登录/注册、ESC 菜单、存档管理
   加载顺序：game.js 之后、main.js 之前
   注意：本文件末尾把所有 HTML 内联 onclick 用到的函数显式挂到 window 上
   ========================================================================== */

// ---------- 模式选择 + 登录逻辑 ----------
const modeOverlay = document.getElementById('mode-overlay');
const authOverlay = document.getElementById('auth-overlay');
const modeBadge = document.getElementById('mode-badge');
const onlineUserLabel = document.getElementById('online-user-label');
const toggleSaveBtn = document.getElementById('toggle-save-btn');
const logoutBtn = document.getElementById('logout-btn');
const closeSaveBtn = document.getElementById('close-save-btn');

// 全局：当前登录态
let _currentUser = null;  // {user_id, username} 或 null

function chooseMode(mode, silent = false) {
    /** 选模式：offline 弹启动面板；online 登录了就直接进，没登录弹登录面板 */
    if (mode === 'offline') {
        showOfflineStart();
    } else if (mode === 'online') {
        if (_currentUser) {
            // 已登录 —— 跳过登录面板直接进
            enterOnlineGame();
        } else {
            // 没登录 —— 弹登录面板
            modeOverlay.classList.add('hidden');
            switchAuthTab('login');
            document.getElementById('auth-message').textContent = '';
            authOverlay.classList.add('active');
        }
    }
}

async function showOfflineStart() {
    /**
     * 显示离线模式启动弹窗 —— 关键：先清后端 session！
     * 如果之前登录过在线，Flask session 里还存着 mode='online' + user_id，
     * 不清掉的话后端 _get_engines() 会按在线模式走 → 离线存档全乱。
     */
    // 先调 logout 清后端 session（mode + user_id + sid 全清）
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch(e) {}
    _currentUser = null;  // 前端也清

    modeOverlay.classList.add('hidden');
    authOverlay.classList.remove('active');
    document.getElementById('offline-start-overlay').classList.remove('hidden');

    // 查后端存档状态（现在后端是纯离线模式了）
    let hasAuto = false, hasAnySlot = false;
    try {
        const res = await fetch('/api/saves?mode=offline');
        const data = await res.json();
        hasAuto = data.auto_has_data || false;
        hasAnySlot = (data.slots || []).some(s => s.has_data);
    } catch(e) { /* 后端没数据就当没存档 */ }

    // 根据存档状态锁定按钮
    const contCard = document.getElementById('continue-card');
    const loadCard = document.getElementById('load-card');
    const contDesc = document.getElementById('continue-desc');

    if (hasAuto) {
        contCard.classList.remove('locked');
        contDesc.textContent = '加载最新自动存档';
    } else {
        contCard.classList.add('locked');
        contDesc.textContent = '暂无自动存档';
    }

    if (hasAuto || hasAnySlot) {
        loadCard.classList.remove('locked');
    } else {
        loadCard.classList.add('locked');
    }
}

async function newOfflineGame() {
    /** 新游戏：重置后端状态 → 进酒馆 */
    localStorage.setItem('game_mode', 'offline');
    document.getElementById('offline-start-overlay').classList.add('hidden');

    // 重置后端 game_state
    await fetch('/api/console', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: 'reset' })
    });

    enterOfflineGame();
}

async function continueOfflineGame() {
    /** 继续游戏：读 auto 自动存档 */
    try {
        const res = await fetch('/api/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot_name: 'auto' })
        });
        const data = await res.json();
        if (!data.success) {
            alert(data.message);
            return;
        }
    } catch(e) { alert('网络错误'); return; }

    localStorage.setItem('game_mode', 'offline');
    document.getElementById('offline-start-overlay').classList.add('hidden');
    enterOfflineGame();
    fetchState();
}

function openLoadPanel() {
    /** 载入游戏：直接打开存档管理弹窗 */
    localStorage.setItem('game_mode', 'offline');
    document.getElementById('offline-start-overlay').classList.add('hidden');
    enterOfflineGame();
    openSavePanel();
}

function enterOfflineGame() {
    /** 进入离线模式游戏（UI 切换 + 加载场景数据） */
    modeOverlay.classList.add('hidden');
    authOverlay.classList.remove('active');
    document.getElementById('offline-start-overlay').classList.add('hidden');
    onlineUserLabel.style.display = 'none';
    logoutBtn.style.display = 'none';
    toggleSaveBtn.style.display = '';
    resetSceneHistory();  // 新档/读档 → 清空场景历史，避免错误的「← 返回」
    fetchState();  // 关键！加载场景/背包数据（之前漏了 → 空壳子）
}

function enterOnlineGame() {
    /** 进入在线模式游戏（已登录）—— 在线模式不显示存档按钮 */
    modeOverlay.classList.add('hidden');
    authOverlay.classList.remove('active');
    localStorage.setItem('game_mode', 'online');
    if (_currentUser) {
        onlineUserLabel.textContent = _currentUser.username;
        onlineUserLabel.style.display = '';
    }
    logoutBtn.style.display = '';
    toggleSaveBtn.style.display = 'none';  // 在线模式自动存档，不需要手动存
    resetSceneHistory();
    fetchState();
}

// ---------- 登录/注册面板交互 ----------
function switchAuthTab(tab) {
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    if (tab === 'login') {
        document.querySelectorAll('.auth-tab')[0].classList.add('active');
        document.getElementById('auth-form-login').style.display = '';
        document.getElementById('auth-form-register').style.display = 'none';
    } else {
        document.querySelectorAll('.auth-tab')[1].classList.add('active');
        document.getElementById('auth-form-login').style.display = 'none';
        document.getElementById('auth-form-register').style.display = '';
    }
    document.getElementById('auth-message').textContent = '';
    document.getElementById('auth-message').className = 'auth-message';
}

function setAuthMsg(text, isError = false) {
    const msgDiv = document.getElementById('auth-message');
    msgDiv.textContent = text;
    msgDiv.className = 'auth-message ' + (isError ? 'error' : 'success');
}

async function doLogin() {
    const username = document.getElementById('auth-login-username').value.trim();
    const password = document.getElementById('auth-login-password').value;
    if (!username || !password) {
        setAuthMsg('请输入用户名和密码', true);
        return;
    }
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (data.success) {
            _currentUser = data.user;
            setAuthMsg(data.message);
            setTimeout(() => enterOnlineGame(), 500);
        } else {
            setAuthMsg(data.message, true);
        }
    } catch (e) {
        setAuthMsg('网络错误，请稍后再试', true);
    }
}

async function doRegister() {
    const username = document.getElementById('auth-reg-username').value.trim();
    const password = document.getElementById('auth-reg-password').value;
    const password2 = document.getElementById('auth-reg-password2').value;
    if (!username || !password) {
        setAuthMsg('请填写完整信息', true);
        return;
    }
    if (password !== password2) {
        setAuthMsg('两次输入的密码不一致', true);
        return;
    }
    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (data.success) {
            _currentUser = { user_id: data.user_id, username: data.username };
            setAuthMsg(data.message);
            setTimeout(() => enterOnlineGame(), 500);
        } else {
            setAuthMsg(data.message, true);
        }
    } catch (e) {
        setAuthMsg('网络错误，请稍后再试', true);
    }
}

function backToModeSelect() {
    authOverlay.classList.remove('active');
    localStorage.removeItem('game_mode');
    modeOverlay.classList.remove('hidden');
}

async function doLogout() {
    if (!confirm('确定退出登录吗？')) return;
    await fetch('/api/auth/logout', { method: 'POST' });
    _currentUser = null;
    localStorage.removeItem('game_mode');
    onlineUserLabel.style.display = 'none';
    logoutBtn.style.display = 'none';
    toggleSaveBtn.style.display = 'none';
    modeOverlay.classList.remove('hidden');
}

logoutBtn.addEventListener('click', doLogout);

// ---------- ESC 菜单逻辑 ----------
function closeEscMenu() {
    document.getElementById('esc-menu-overlay').classList.add('hidden');
}

function openEscMenu() {
    const escOverlay = document.getElementById('esc-menu-overlay');
    escOverlay.classList.toggle('hidden');
    if (!escOverlay.classList.contains('hidden')) {
        // 根据模式显示/隐藏菜单项
        const isOnline = _currentUser !== null;
        document.getElementById('esc-quick-save').style.display = isOnline ? 'none' : '';
        document.getElementById('esc-save-mgmt').style.display = isOnline ? 'none' : '';
    }
}

async function returnToModeSelect() {
    /** 返回模式选择 —— 清 localStorage + await 后端 logout + 清内存 + 刷新 */
    localStorage.removeItem('game_mode');
    // 必须 await！不然 session 还没清完就 reload 了
    if (_currentUser) {
        await fetch('/api/auth/logout', { method: 'POST' });
        _currentUser = null;
    }
    onlineUserLabel.style.display = 'none';
    logoutBtn.style.display = 'none';
    toggleSaveBtn.style.display = 'none';
    modeBadge.textContent = '';
    modeBadge.className = 'mode-badge';
    closeEscMenu();
    // 清掉 localStorage 里所有存档相关（离线模式也要彻底重来）
    // 刷新页面 → 从空白开始
    location.reload();
}

async function doQuickSave() {
    /** 快速保存 —— POST /api/save/quick */
    const res = await fetch('/api/save/quick', { method: 'POST' });
    const data = await res.json();
    const msg = document.getElementById('action-message');
    if (msg) msg.textContent = data.success ? '已快速保存' : data.message;
    closeEscMenu();
}

function openSavePanelFromMenu() {
    closeEscMenu();
    openSavePanel();
}

// ESC 键 → 优先关闭上层弹窗，否则切换菜单（和 ~ 键互斥）
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        // 存档 / 背包弹窗打开时，ESC 先关它们
        if (!saveOverlay.classList.contains('hidden')) { closeSavePanel(); return; }
        if (!inventoryOverlay.classList.contains('hidden')) { closeInventory(); return; }
        // 只在游戏主界面时响应（不覆盖 mode/auth 面板）
        if (modeOverlay.classList.contains('hidden') &&
            !authOverlay.classList.contains('active') &&
            document.getElementById('offline-start-overlay').classList.contains('hidden')) {
            openEscMenu();
        }
    }
});

// F5 → 快速保存（离线模式）
document.addEventListener('keydown', (e) => {
    if (e.key === 'F5' && !e.shiftKey && !e.ctrlKey) {
        if (localStorage.getItem('game_mode') === 'offline') {
            e.preventDefault();
            doQuickSave();
        }
    }
});

// ---------- 存档弹窗逻辑 ----------
const saveOverlay = document.getElementById('save-overlay');

function openSavePanel() {
    /** 打开存档管理弹窗并刷新列表 */
    resetModalPosition(saveOverlay.querySelector('.save-panel'));
    saveOverlay.classList.remove('hidden');
    refreshSaveList();
}
function closeSavePanel() {
    saveOverlay.classList.add('hidden');
}

toggleSaveBtn.addEventListener('click', openSavePanel);
closeSaveBtn.addEventListener('click', closeSavePanel);
// 点遮罩空白处关闭
saveOverlay.addEventListener('click', (e) => {
    if (e.target === saveOverlay) closeSavePanel();
});

// ---------- 背包弹窗逻辑 ----------
const inventoryOverlay = document.getElementById('inventory-overlay');

function openInventory() {
    resetModalPosition(document.getElementById('inventory-card'));
    inventoryOverlay.classList.remove('hidden');
}
function closeInventory() {
    inventoryOverlay.classList.add('hidden');
}

document.getElementById('toggle-inventory-btn').addEventListener('click', openInventory);
document.getElementById('close-inventory-btn').addEventListener('click', closeInventory);
// 点遮罩空白处关闭
inventoryOverlay.addEventListener('click', (e) => {
    if (e.target === inventoryOverlay) closeInventory();
});

async function refreshSaveList() {
    /** 从后端拉取存档列表并渲染（适配三路隔离） */
    const res = await fetch('/api/saves');
    const data = await res.json();
    const slotsDiv = document.getElementById('save-slots');
    slotsDiv.innerHTML = '';

    if (data.mode === 'online') {
        // 在线模式：只显示 online_auto 状态
        const info = document.createElement('div');
        info.style.color = '#4ade80';
        info.style.padding = '1rem';
        info.innerHTML = `在线模式自动存档<br><span style="color:#94a3b8;font-size:0.8rem;">服务器实时保存，无需手动操作</span>`;
        if (data.online_auto_updated_at) {
            info.innerHTML += `<br><span style="color:#64748b;font-size:0.75rem;">上次更新：${data.online_auto_updated_at}</span>`;
        }
        slotsDiv.appendChild(info);
        return;
    }

    // 离线模式：三路分区
    // 1. 自动存档（系统维护，只有读档）
    const autoHeader = document.createElement('div');
    autoHeader.className = 'save-section-title';
    autoHeader.innerHTML = '自动存档 <span style="color:#64748b;font-size:0.75rem;">（系统每30秒保存，覆盖自己）</span>';
    slotsDiv.appendChild(autoHeader);

    if (data.auto_has_data) {
        slotsDiv.appendChild(buildSlotRow('auto', {
            updated_at: data.auto_updated_at,
            is_auto: true,
            has_data: true,
        }));
    } else {
        slotsDiv.appendChild(buildSlotRow('auto', null));
    }

    // 2. 快速保存
    const quickHeader = document.createElement('div');
    quickHeader.className = 'save-section-title';
    quickHeader.innerHTML = '快速保存 <span style="color:#64748b;font-size:0.75rem;">（F5 快捷键，覆盖自己）</span>';
    slotsDiv.appendChild(quickHeader);

    if (data.quick_has_data) {
        slotsDiv.appendChild(buildSlotRow('quick', {
            updated_at: data.quick_updated_at,
            is_quick: true,
            has_data: true,
        }));
    } else {
        slotsDiv.appendChild(buildSlotRow('quick', null));
    }

    // 3. 手动槽位 slot_1~5
    const slotHeader = document.createElement('div');
    slotHeader.className = 'save-section-title';
    slotHeader.innerHTML = '手动槽位 <span style="color:#64748b;font-size:0.75rem;">（可新开或覆盖已有槽位）</span>';
    slotsDiv.appendChild(slotHeader);

    for (const slot of data.slots) {
        slotsDiv.appendChild(buildSlotRow(slot.slot_name, slot.has_data ? slot : null));
    }
}

function buildSlotRow(slotName, info) {
    /** 构建一个存档槽位行（统一适配 auto/quick/slot_N） */
    const row = document.createElement('div');
    row.className = 'save-slot-row';
    const hasData = info && info.has_data;
    row.style.borderLeft = hasData ? '3px solid #4ade80' : '3px solid #64748b';

    // 槽位名 + 时间
    const label = slotName === 'auto' ? '自动存档' :
                  slotName === 'quick' ? '快速保存' :
                  `槽位 ${slotName.replace('slot_', '')}`;

    const infoDiv = document.createElement('div');
    infoDiv.className = 'save-slot-info';
    if (hasData) {
        infoDiv.innerHTML = `<strong>${label}</strong><span class="slot-time">${info.updated_at}</span>`;
    } else {
        infoDiv.innerHTML = `<strong>${label}</strong> — 空`;
    }

    // 按钮区
    const btns = document.createElement('div');
    btns.className = 'save-slot-btns';

    const isSystemSlot = slotName === 'auto';  // auto 是系统的，不允许手动存/删
    const canManualSave = !isSystemSlot;       // quick 和 slot_N 都可以手动存

    // 手动存
    if (canManualSave) {
        const saveBtn = document.createElement('button');
        saveBtn.textContent = '存';
        saveBtn.title = slotName === 'quick' ? '覆盖快速保存' : (hasData ? '覆盖此槽位' : '新开存档');
        saveBtn.onclick = () => manualSave(slotName);
        btns.appendChild(saveBtn);
    }

    // 载入
    const loadBtn = document.createElement('button');
    loadBtn.className = 'btn-load';
    loadBtn.textContent = '载入';
    loadBtn.disabled = !hasData;
    loadBtn.onclick = () => loadSave(slotName);
    btns.appendChild(loadBtn);

    // 删除（只有 auto 不让删）
    if (!isSystemSlot) {
        const delBtn = document.createElement('button');
        delBtn.className = 'btn-delete';
        delBtn.textContent = '删除';
        delBtn.disabled = !hasData;
        delBtn.onclick = () => deleteSave(slotName);
        btns.appendChild(delBtn);
    }

    row.appendChild(infoDiv);
    row.appendChild(btns);
    return row;
}

async function manualSave(slotName) {
    /**
     * 手动存档到指定槽位 —— 快速保存(quick)走 /api/save/quick
     * 其他槽位(slot_1~5)走 /api/save
     */
    let res, data;
    if (slotName === 'quick') {
        res = await fetch('/api/save/quick', { method: 'POST' });
        data = await res.json();
    } else {
        res = await fetch('/api/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot_name: slotName })
        });
        data = await res.json();
    }
    showActionMsg(
        data.success ? `已保存到 ${slotName}${data.updated_at ? '（' + data.updated_at + '）' : ''}` : data.message);
    if (data.success) refreshSaveList();
}

async function loadSave(slotName) {
    /** 从指定槽位读档 */
    if (!confirm(`确认读取 ${slotName}？当前进度会被覆盖。`)) return;
    const res = await fetch('/api/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_name: slotName })
    });
    const data = await res.json();
    showActionMsg(data.message);
    if (data.success) fetchState();  // 刷新场景/背包/对话/战斗面板
}

async function deleteSave(slotName) {
    /** 删除一个槽位 */
    if (!confirm(`确认删除 ${slotName} 的存档？此操作不可撤销。`)) return;
    const res = await fetch('/api/save', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_name: slotName })
    });
    const data = await res.json();
    showActionMsg(data.message);
    refreshSaveList();
}

// ---------- 底部状态栏按钮 ----------
document.getElementById('btn-console').addEventListener('click', () => toggleConsole());
document.getElementById('btn-menu').addEventListener('click', () => openEscMenu());

// ---------- 暴露给 HTML 内联 onclick 的入口 ----------
// 拆分后这些函数仍处于全局作用域，但显式挂到 window 可避免"函数找不到"，
// 也为将来切换到 type="module" 预留兼容。
window.chooseMode = chooseMode;
window.switchAuthTab = switchAuthTab;
window.doLogin = doLogin;
window.doRegister = doRegister;
window.backToModeSelect = backToModeSelect;
window.newOfflineGame = newOfflineGame;
window.continueOfflineGame = continueOfflineGame;
window.openLoadPanel = openLoadPanel;
window.doQuickSave = doQuickSave;
window.openSavePanelFromMenu = openSavePanelFromMenu;
window.returnToModeSelect = returnToModeSelect;
window.closeEscMenu = closeEscMenu;
