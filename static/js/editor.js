/* ==========================================================================
   editor.js —— 游戏内容编辑器逻辑（地点 / 物品 两个表单）
   纯原生 JS，不依赖游戏页任何脚本；所有写操作走 /api/editor/* 并由后端校验。
   ========================================================================== */

let DATA = { scenes: {}, items: {}, initial_scene: '' };
let tab = 'scenes';          // 当前页签：scenes | items
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
        $('scene-form-title').textContent = '新建地点';
        $('scene-id').value = '';
        $('scene-id').disabled = false;
        $('scene-name').value = '';
        $('scene-desc').value = '';
        $('scene-exits-raw').value = '';
        $('scene-enemies').value = '';
        renderOptionGroups(null, []);
        $('scene-delete').classList.add('hidden');
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
        $('scene-enemies').value = (s.enemies_here || []).join(', ');
        renderOptionGroups(s, s.exits || []);
        $('scene-delete').classList.toggle('hidden', id === DATA.initial_scene);
    } else {
        const it = DATA.items[id];
        if (!it) return;
        $('form-scene').classList.add('hidden');
        $('form-item').classList.remove('hidden');
        $('item-form-title').textContent = '编辑物品';
        $('item-id').value = it.id;
        $('item-id').disabled = true;
        $('item-name').value = it.name || '';
        $('item-desc').value = it.description || '';
        $('item-is-weapon').checked = !!it.is_weapon;
        $('item-damage').value = it.damage != null ? it.damage : 5;
        $('item-damage-field').classList.toggle('hidden', !it.is_weapon);
        $('item-delete').classList.remove('hidden');
    }
}

// 出口/物品复选框组（scene 为正在编辑的场景对象，selectedExits 为当前出口列表）
function renderOptionGroups(scene, selectedExits) {
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

    return {
        id: $('scene-id').value.trim(),
        name: $('scene-name').value,
        description: $('scene-desc').value,
        exits,
        locked_exits: locked,
        items_here: [...document.querySelectorAll('.item-cb:checked')].map(cb => cb.dataset.id),
        enemies_here: $('scene-enemies').value.split(',').map(s => s.trim()).filter(Boolean),
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
    const payload = {
        id: $('item-id').value.trim(),
        name: $('item-name').value,
        description: $('item-desc').value,
        is_weapon: isWeapon,
    };
    if (isWeapon) payload.damage = parseInt($('item-damage').value, 10);

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
