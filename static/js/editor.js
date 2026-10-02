/* ==========================================================================
   editor.js —— 游戏内容编辑器逻辑（地点 / 物品 / 敌人 三个表单）
   纯原生 JS，不依赖游戏页任何脚本；所有写操作走 /api/editor/* 并由后端校验。
   ========================================================================== */

let DATA = { scenes: {}, items: {}, enemies: {}, initial_scene: '' };
let tab = 'scenes';          // 当前页签：scenes | items | enemies
let selectedId = null;       // 正在编辑的对象 id（新建态为 null）
let isNew = false;

const $ = (id) => document.getElementById(id);
const sortedKeys = (obj) => Object.keys(obj).sort();

// ---------- 启动 ----------
async function init() {
    bindStaticEvents();
    await loadData();
}

async function loadData() {
    try {
        const res = await fetch('/api/editor/data');
        const json = await res.json();
        if (!json.success) {
            toast(json.message || '编辑器接口不可用', 'error');
            return;
        }
        DATA = json;
    } catch (e) {
        toast('无法连接服务器', 'error');
        return;
    }
    renderList();
    if (selectedId && DATA[tab][selectedId]) {
        showForm(tab, selectedId);  // 保存后刷新表单，保持选中
    } else {
        showEmpty();
    }
}

// ---------- 列表 / 页签 ----------
function bindStaticEvents() {
    document.querySelectorAll('.ed-tab').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    $('btn-new').addEventListener('click', onNew);
    $('btn-playtest').addEventListener('click', () => window.open('index.html', '_blank'));

    $('scene-save').addEventListener('click', saveScene);
    $('scene-delete').addEventListener('click', deleteScene);
    $('scene-cancel').addEventListener('click', showEmpty);
    $('item-save').addEventListener('click', saveItem);
    $('item-delete').addEventListener('click', deleteItem);
    $('item-cancel').addEventListener('click', showEmpty);
    $('item-is-weapon').addEventListener('change', (e) => {
        $('item-damage-field').classList.toggle('hidden', !e.target.checked);
    });
    $('item-usable').addEventListener('change', (e) => {
        $('item-heal-field').classList.toggle('hidden', !e.target.checked);
    });
    $('item-currency').addEventListener('change', (e) => {
        $('item-currency-field').classList.toggle('hidden', !e.target.checked);
    });
    $('enemy-save').addEventListener('click', saveEnemy);
    $('enemy-delete').addEventListener('click', deleteEnemy);
    $('enemy-cancel').addEventListener('click', showEmpty);
}

function switchTab(next) {
    tab = next;
    selectedId = null;
    isNew = false;
    document.querySelectorAll('.ed-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === tab));
    renderList();
    showEmpty();
}

function renderList() {
    const listEl = $('ed-list');
    listEl.innerHTML = '';
    const coll = DATA[tab];
    for (const id of sortedKeys(coll)) {
        const row = document.createElement('div');
        row.className = 'ed-list-item' + (id === selectedId ? ' selected' : '');
        const name = coll[id].name || id;
        row.innerHTML = `<div class="li-name"></div><div class="li-id"></div>`;
        row.querySelector('.li-name').textContent = name;
        row.querySelector('.li-id').textContent = id;
        row.addEventListener('click', () => showForm(tab, id));
        listEl.appendChild(row);
    }
}

function showEmpty() {
    selectedId = null;
    isNew = false;
    $('ed-empty').classList.remove('hidden');
    $('form-scene').classList.add('hidden');
    $('form-item').classList.add('hidden');
    $('form-enemy').classList.add('hidden');
    renderList();
}

function onNew() {
    isNew = true;
    selectedId = null;
    $('ed-empty').classList.add('hidden');
    renderList();
    if (tab === 'scenes') {
        $('form-scene').classList.remove('hidden');
        $('form-item').classList.add('hidden');
        $('form-enemy').classList.add('hidden');
        $('scene-form-title').textContent = '新建地点';
        $('scene-id').value = '';
        $('scene-id').disabled = false;
        $('scene-name').value = '';
        $('scene-desc').value = '';
        $('scene-exits-raw').value = '';
        $('scene-enemies-raw').value = '';
        renderOptionGroups(null, [], [], []);
        $('scene-delete').classList.add('hidden');
    } else if (tab === 'enemies') {
        $('form-enemy').classList.remove('hidden');
        $('form-scene').classList.add('hidden');
        $('form-item').classList.add('hidden');
        $('enemy-form-title').textContent = '新建敌人';
        $('enemy-id').value = '';
        $('enemy-id').disabled = false;
        $('enemy-name').value = '';
        $('enemy-desc').value = '';
        $('enemy-hp').value = 30;
        $('enemy-attack').value = 5;
        $('enemy-defense').value = 2;
        $('enemy-gold').value = 0;
        renderEnemyRewards([]);
        $('enemy-delete').classList.add('hidden');
    } else {
        $('form-item').classList.remove('hidden');
        $('form-scene').classList.add('hidden');
        $('item-form-title').textContent = '新建物品';
        $('item-id').value = '';
        $('item-id').disabled = false;
        $('item-name').value = '';
        $('item-desc').value = '';
        $('item-is-weapon').checked = false;
        $('item-damage').value = 5;
        $('item-damage-field').classList.add('hidden');
        $('item-usable').checked = false;
        $('item-heal').value = 50;
        $('item-heal-field').classList.add('hidden');
        $('item-currency').checked = false;
        $('item-currency-value').value = 1;
        $('item-currency-field').classList.add('hidden');
        $('item-delete').classList.add('hidden');
    }
}

function showForm(kind, id) {
    selectedId = id;
    isNew = false;
    renderList();
    $('ed-empty').classList.add('hidden');
    if (kind === 'scenes') {
        const s = DATA.scenes[id];
        if (!s) return;
        $('form-item').classList.add('hidden');
        $('form-scene').classList.remove('hidden');
        $('scene-form-title').textContent = '编辑地点';
        $('scene-id').value = s.id;
        $('scene-id').disabled = true;
        $('scene-name').value = s.name || '';
        $('scene-desc').value = s.description || '';
        const knownIds = new Set(sortedKeys(DATA.scenes));
        const forwardRefs = (s.exits || []).filter(x => !knownIds.has(x));
        $('scene-exits-raw').value = forwardRefs.join(', ');
        const knownEnemyIds = new Set(sortedKeys(DATA.enemies));
        const enemyRefs = s.enemies_here || [];
        $('scene-enemies-raw').value = enemyRefs.filter(x => !knownEnemyIds.has(x)).join(', ');
        renderOptionGroups(s, s.exits || [], s.shop_items || [], enemyRefs);
        $('scene-delete').classList.toggle('hidden', id === DATA.initial_scene);
    } else if (kind === 'enemies') {
        const em = DATA.enemies[id];
        if (!em) return;
        $('form-scene').classList.add('hidden');
        $('form-item').classList.add('hidden');
        $('form-enemy').classList.remove('hidden');
        $('enemy-form-title').textContent = '编辑敌人';
        $('enemy-id').value = em.id;
        $('enemy-id').disabled = true;
        $('enemy-name').value = em.name || '';
        $('enemy-desc').value = em.description || '';
        $('enemy-hp').value = em.hp != null ? em.hp : 30;
        $('enemy-attack').value = em.attack != null ? em.attack : 5;
        $('enemy-defense').value = em.defense != null ? em.defense : 2;
        $('enemy-gold').value = em.reward_gold != null ? em.reward_gold : 0;
        renderEnemyRewards(em.reward_items || []);
        $('enemy-delete').classList.remove('hidden');
    } else {
        const it = DATA.items[id];
        if (!it) return;
        $('form-scene').classList.add('hidden');
        $('form-enemy').classList.add('hidden');
        $('form-item').classList.remove('hidden');
        $('item-form-title').textContent = '编辑物品';
        $('item-id').value = it.id;
        $('item-id').disabled = true;
        $('item-name').value = it.name || '';
        $('item-desc').value = it.description || '';
        $('item-is-weapon').checked = !!it.is_weapon;
        $('item-damage').value = it.damage != null ? it.damage : 5;
        $('item-damage-field').classList.toggle('hidden', !it.is_weapon);
        $('item-usable').checked = !!it.usable;
        $('item-heal').value = it.heal != null ? it.heal : 50;
        $('item-heal-field').classList.toggle('hidden', !it.usable);
        $('item-currency').checked = it.currency_value != null && it.currency_value !== undefined;
        $('item-currency-value').value = it.currency_value != null ? it.currency_value : 1;
        $('item-currency-field').classList.toggle('hidden', $('item-currency').checked === false);
        $('item-delete').classList.remove('hidden');
    }
}

// 出口/物品/商店/敌人复选框组（scene 为正在编辑的场景对象，shopItems 为在售货物，selectedEnemies 为出没敌人）
function renderOptionGroups(scene, selectedExits, shopItems, selectedEnemies) {
    const exitsBox = $('scene-exits');
    exitsBox.innerHTML = '';
    const lockedMap = (scene && scene.locked_exits) || {};
    const selfId = scene ? scene.id : null;
    for (const sid of sortedKeys(DATA.scenes)) {
        if (sid === selfId) continue;  // 不能通向自己
        const label = document.createElement('label');
        const checked = selectedExits.includes(sid);
        label.innerHTML =
            `<input type="checkbox" class="exit-cb" data-id="${sid}" ${checked ? 'checked' : ''}>` +
            `<span></span><span class="li-sub">${sid}</span>` +
            `<span class="li-lock"><input type="checkbox" class="lock-cb" data-id="${sid}"` +
            `${lockedMap[sid] ? ' checked' : ''}> 锁定</span>`;
        label.querySelector('span').textContent = DATA.scenes[sid].name || sid;
        exitsBox.appendChild(label);
    }

    const itemsBox = $('scene-items');
    itemsBox.innerHTML = '';
    const here = (scene && scene.items_here) || [];
    for (const iid of sortedKeys(DATA.items)) {
        const label = document.createElement('label');
        label.innerHTML =
            `<input type="checkbox" class="item-cb" data-id="${iid}" ${here.includes(iid) ? 'checked' : ''}>` +
            `<span></span><span class="li-sub">${iid}</span>`;
        label.querySelector('span').textContent = DATA.items[iid].name || iid;
        itemsBox.appendChild(label);
    }

    // 商店货物：勾选在售 + 价格输入
    const shopBox = $('scene-shop');
    shopBox.innerHTML = '';
    const shopMap = {};
    (shopItems || []).forEach(g => { shopMap[g.item_id] = g.price; });
    for (const iid of sortedKeys(DATA.items)) {
        const label = document.createElement('label');
        const onSale = iid in shopMap;
        const price = onSale ? shopMap[iid] : 0;
        label.innerHTML =
            `<input type="checkbox" class="shop-cb" data-id="${iid}" ${onSale ? 'checked' : ''}>` +
            `<span></span><span class="li-sub">${iid}</span>` +
            `<span class="li-lock">价格 <input type="number" class="shop-price" data-id="${iid}"` +
            ` min="0" step="1" value="${price}"></span>`;
        label.querySelector('span').textContent = DATA.items[iid].name || iid;
        shopBox.appendChild(label);
    }

    // 出没的敌人：勾选已创建的敌人（未建敌人走 raw 文本框）
    const enemyBox = $('scene-enemies');
    enemyBox.innerHTML = '';
    for (const eid of sortedKeys(DATA.enemies)) {
        const label = document.createElement('label');
        label.innerHTML =
            `<input type="checkbox" class="enemy-cb" data-id="${eid}"` +
            `${ (selectedEnemies || []).includes(eid) ? ' checked' : ''}>` +
            `<span></span><span class="li-sub">${eid}</span>`;
        label.querySelector('span').textContent = DATA.enemies[eid].name || eid;
        enemyBox.appendChild(label);
    }
}

// 敌人表单：战利品物品勾选组
function renderEnemyRewards(selectedItems) {
    const box = $('enemy-rewards');
    box.innerHTML = '';
    for (const iid of sortedKeys(DATA.items)) {
        const label = document.createElement('label');
        label.innerHTML =
            `<input type="checkbox" class="reward-cb" data-id="${iid}"` +
            `${selectedItems.includes(iid) ? ' checked' : ''}>` +
            `<span></span><span class="li-sub">${iid}</span>`;
        label.querySelector('span').textContent = DATA.items[iid].name || iid;
        box.appendChild(label);
    }
}

// ---------- 地点：保存 / 删除 ----------
function collectScenePayload() {
    const exits = [...document.querySelectorAll('.exit-cb:checked')]
        .map(cb => cb.dataset.id);
    // 文本框里的前向引用（指向还没建的地点）
    $('scene-exits-raw').value.split(',').map(s => s.trim()).filter(Boolean)
        .forEach(id => { if (!exits.includes(id)) exits.push(id); });

    const locked = {};
    document.querySelectorAll('.lock-cb:checked').forEach(cb => {
        if (exits.includes(cb.dataset.id)) locked[cb.dataset.id] = true;
    });

    // 商店货物（勾选的物品 + 同行价格）
    const shopItems = [];
    document.querySelectorAll('.shop-cb:checked').forEach(cb => {
        const priceInput = document.querySelector(`.shop-price[data-id="${cb.dataset.id}"]`);
        shopItems.push({ item_id: cb.dataset.id, price: parseInt(priceInput.value, 10) });
    });

    // 出没敌人：勾选组 + raw（前向引用还没建的敌人）
    const enemiesHere = [...document.querySelectorAll('.enemy-cb:checked')]
        .map(cb => cb.dataset.id);
    $('scene-enemies-raw').value.split(',').map(s => s.trim()).filter(Boolean)
        .forEach(id => { if (!enemiesHere.includes(id)) enemiesHere.push(id); });

    return {
        id: $('scene-id').value.trim(),
        name: $('scene-name').value,
        description: $('scene-desc').value,
        exits,
        locked_exits: locked,
        items_here: [...document.querySelectorAll('.item-cb:checked')].map(cb => cb.dataset.id),
        shop_items: shopItems,
        enemies_here: enemiesHere,
    };
}

async function saveScene() {
    const payload = collectScenePayload();
    const res = await fetch('/api/editor/scene', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    selectedId = data.id;
    isNew = false;
    await loadData();
    toast([data.message, ...(data.warnings || [])].join('\n'),
        (data.warnings || []).length ? 'warning' : 'success');
}

async function deleteScene() {
    const s = DATA.scenes[selectedId];
    if (!s) return;
    if (!confirm(`确认删除地点【${s.name || selectedId}】？该操作不可撤销（保存前的版本可在 scenes.json.bak 找回）。`)) return;
    const res = await fetch('/api/editor/scene/' + encodeURIComponent(selectedId), { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    showEmpty();
    await loadData();
    toast(data.message, 'success');
}

// ---------- 物品：保存 / 删除 ----------
async function saveItem() {
    const isWeapon = $('item-is-weapon').checked;
    const isUsable = $('item-usable').checked;
    const isCurrency = $('item-currency').checked;
    const payload = {
        id: $('item-id').value.trim(),
        name: $('item-name').value,
        description: $('item-desc').value,
        is_weapon: isWeapon,
        usable: isUsable,
        is_currency: isCurrency,
    };
    if (isWeapon) payload.damage = parseInt($('item-damage').value, 10);
    if (isUsable) payload.heal = parseInt($('item-heal').value, 10);
    if (isCurrency) payload.currency_value = parseInt($('item-currency-value').value, 10);

    const res = await fetch('/api/editor/item', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    selectedId = data.id;
    isNew = false;
    await loadData();
    toast([data.message, ...(data.warnings || [])].join('\n'),
        (data.warnings || []).length ? 'warning' : 'success');
}

async function deleteItem() {
    const it = DATA.items[selectedId];
    if (!it) return;
    if (!confirm(`确认删除物品【${it.name || selectedId}】？该操作不可撤销（可在 items.json.bak 找回）。`)) return;
    const res = await fetch('/api/editor/item/' + encodeURIComponent(selectedId), { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    showEmpty();
    await loadData();
    toast(data.message, 'success');
}

// ---------- 敌人：保存 / 删除 ----------
async function saveEnemy() {
    const payload = {
        id: $('enemy-id').value.trim(),
        name: $('enemy-name').value,
        description: $('enemy-desc').value,
        hp: parseInt($('enemy-hp').value, 10),
        attack: parseInt($('enemy-attack').value, 10),
        defense: parseInt($('enemy-defense').value, 10),
        reward_gold: parseInt($('enemy-gold').value, 10),
        reward_items: [...document.querySelectorAll('.reward-cb:checked')].map(cb => cb.dataset.id),
    };
    const res = await fetch('/api/editor/enemy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    selectedId = data.id;
    isNew = false;
    await loadData();
    toast(data.message, 'success');
}

async function deleteEnemy() {
    const em = DATA.enemies[selectedId];
    if (!em) return;
    if (!confirm(`确认删除敌人【${em.name || selectedId}】？该操作不可撤销（可在 enemies.json.bak 找回）。`)) return;
    const res = await fetch('/api/editor/enemy/' + encodeURIComponent(selectedId), { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    showEmpty();
    await loadData();
    toast(data.message, 'success');
}

// ---------- 消息条 ----------
let _toastTimer = null;
function toast(text, kind) {
    const el = $('ed-toast');
    el.textContent = text;
    el.className = 'ed-toast ' + (kind || 'success');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => el.classList.add('hidden'), 5000);
}

init();
