/* ==========================================================================
   editor.js —— 游戏内容编辑器逻辑（地点 / 物品 / 敌人 三个表单）
   纯原生 JS，不依赖游戏页任何脚本；所有写操作走 /api/editor/* 并由后端校验。
   ========================================================================== */

let DATA = { scenes: {}, items: {}, enemies: {}, npcs: {}, initial_scene: '' };
let tab = 'scenes';          // 当前页签：scenes | items | enemies | npcs
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
    $('npc-save').addEventListener('click', saveNpc);
    $('npc-delete').addEventListener('click', deleteNpc);
    $('npc-cancel').addEventListener('click', showEmpty);
    $('npc-add-rule').addEventListener('click', () => addRuleRow(null));
    $('npc-add-node').addEventListener('click', () => addNodeCard('', { text: '', choices: [] }));
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
    $('form-npc').classList.add('hidden');
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
        $('form-npc').classList.add('hidden');
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
        $('form-npc').classList.add('hidden');
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
    } else if (tab === 'npcs') {
        $('form-npc').classList.remove('hidden');
        $('form-scene').classList.add('hidden');
        $('form-enemy').classList.add('hidden');
        renderNpcForm(null);
    } else {
        $('form-item').classList.remove('hidden');
        $('form-scene').classList.add('hidden');
        $('form-enemy').classList.add('hidden');
        $('form-npc').classList.add('hidden');
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
        $('form-enemy').classList.add('hidden');
        $('form-npc').classList.add('hidden');
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
        $('form-npc').classList.add('hidden');
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
    } else if (kind === 'npcs') {
        const npc = DATA.npcs[id];
        if (!npc) return;
        $('form-scene').classList.add('hidden');
        $('form-enemy').classList.add('hidden');
        $('form-item').classList.add('hidden');
        $('form-npc').classList.remove('hidden');
        renderNpcForm(npc);
    } else {
        const it = DATA.items[id];
        if (!it) return;
        $('form-scene').classList.add('hidden');
        $('form-enemy').classList.add('hidden');
        $('form-npc').classList.add('hidden');
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

// ---------- NPC：属性 / 条件问候 / 对话节点树编辑（列表式） ----------
const COND_OPTIONS = [
    { v: '', label: '无条件' },
    { v: 'flag', label: '需要标志' },
    { v: 'has_item', label: '持有物品' },
    { v: 'enemy_killed', label: '已击败敌人' },
    { v: 'gold_gte', label: '金币不少于' },
];
const EFFECT_OPTIONS = [
    { v: '', label: '（无效果）' },
    { v: 'set_flag', label: '设置标志' },
    { v: 'give_item', label: '给予物品' },
    { v: 'remove_item', label: '收回物品' },
    { v: 'heal', label: '治疗生命' },
    { v: 'max_hp', label: '增加生命上限' },
    { v: 'gold', label: '增加金币' },
    { v: 'teleport', label: '传送到' },
    { v: 'start_combat', label: '开始战斗' },
];
// 各类型参数输入框的形态：list 为 datalist id，ph 为占位提示
const COND_PARAM_CFG = {
    flag: { list: '', type: 'text', ph: '标志名，如 quest_done' },
    has_item: { list: 'npc-dl-items', type: 'text', ph: '物品 ID' },
    enemy_killed: { list: 'npc-dl-enemies', type: 'text', ph: '敌人 ID' },
    gold_gte: { list: '', type: 'number', ph: '金币数量' },
};
const EFFECT_PARAM_CFG = {
    set_flag: { list: '', type: 'text', ph: '标志名，如 quest_done' },
    give_item: { list: 'npc-dl-items', type: 'text', ph: '物品 ID' },
    remove_item: { list: 'npc-dl-items', type: 'text', ph: '物品 ID' },
    heal: { list: '', type: 'number', ph: '回血量' },
    max_hp: { list: '', type: 'number', ph: '增加的上限值' },
    gold: { list: '', type: 'number', ph: '金币数量' },
    teleport: { list: 'npc-dl-scenes', type: 'text', ph: '地点 ID' },
    start_combat: { list: 'npc-dl-enemies', type: 'text', ph: '敌人 ID' },
};

function ce(tag, cls, text) {
    const x = document.createElement(tag);
    if (cls) x.className = cls;
    if (text != null) x.textContent = text;
    return x;
}

function optionsHtml(list, selected) {
    return list.map(o =>
        `<option value="${o.v}"${o.v === selected ? ' selected' : ''}>${o.label}</option>`
    ).join('');
}

function condTypeOf(cond) {
    if (!cond) return '';
    return ['flag', 'has_item', 'enemy_killed', 'gold_gte'].find(k => k in cond) || '';
}

function configureParamInput(inp, cfg) {
    if (!cfg) { inp.classList.add('hidden'); inp.value = ''; return; }
    inp.classList.remove('hidden');
    inp.type = cfg.type;
    inp.placeholder = cfg.ph;
    if (cfg.list) inp.setAttribute('list', cfg.list);
    else inp.removeAttribute('list');
}

function fillRefDatalists() {
    const fill = (dlId, coll) => {
        const dl = $(dlId);
        dl.innerHTML = '';
        for (const id of sortedKeys(coll)) {
            const o = document.createElement('option');
            o.value = id;
            o.textContent = coll[id].name || id;
            dl.appendChild(o);
        }
    };
    fill('npc-dl-items', DATA.items);
    fill('npc-dl-enemies', DATA.enemies);
    fill('npc-dl-scenes', DATA.scenes);
}

function refreshNodeDatalist() {
    const dl = $('npc-dl-nodes');
    dl.innerHTML = '';
    document.querySelectorAll('.nnode-id').forEach(inp => {
        const v = inp.value.trim();
        if (v) {
            const o = document.createElement('option');
            o.value = v;
            dl.appendChild(o);
        }
    });
}

function renderNpcForm(npc) {
    $('npc-form-title').textContent = npc ? '编辑 NPC' : '新建 NPC';
    $('npc-id').value = npc ? npc.id : '';
    $('npc-id').disabled = !!npc;
    $('npc-name').value = npc ? npc.name : '';

    const sceneSel = $('npc-scene');
    sceneSel.innerHTML = '';
    for (const sid of sortedKeys(DATA.scenes)) {
        sceneSel.appendChild(ce('option', null, `${DATA.scenes[sid].name || sid}（${sid}）`)).value = sid;
    }
    sceneSel.value = npc ? (npc.scene_id || '') : (sortedKeys(DATA.scenes)[0] || '');
    $('npc-greeting').value = npc ? (npc.greeting || 'greet') : 'greet';

    fillRefDatalists();

    const rulesBox = $('npc-rules');
    rulesBox.innerHTML = '';
    (npc && npc.greeting_rules || []).forEach(r => addRuleRow(r));

    const nodesBox = $('npc-nodes');
    nodesBox.innerHTML = '';
    if (npc) {
        Object.entries(npc.nodes || {}).forEach(([nid, node]) => addNodeCard(nid, node));
    } else {
        addNodeCard('greet', { text: '', choices: [] });
    }
    refreshNodeDatalist();
    $('npc-delete').classList.toggle('hidden', !npc);
}

function addRuleRow(rule) {
    const cond = rule ? rule.if : null;
    const t = condTypeOf(cond);
    const row = ce('div', 'npc-rule-row');
    const typeSel = ce('select', 'nr-cond-type');
    typeSel.innerHTML = optionsHtml(COND_OPTIONS, t);
    const param = ce('input', 'nr-cond-param');
    configureParamInput(param, COND_PARAM_CFG[t]);
    param.value = cond && t ? (t === 'gold_gte' ? cond[t] : cond[t]) : '';
    typeSel.addEventListener('change', () => {
        configureParamInput(param, COND_PARAM_CFG[typeSel.value]);
    });
    const nodeInp = ce('input', 'nr-node');
    nodeInp.setAttribute('list', 'npc-dl-nodes');
    nodeInp.placeholder = '起始节点 ID';
    nodeInp.value = rule ? rule.node || '' : '';
    const del = ce('button', 'ed-btn mini', '删除');
    del.type = 'button';
    del.addEventListener('click', () => row.remove());
    row.append('条件', typeSel, param, '→ 起始节点', nodeInp, del);
    $('npc-rules').appendChild(row);
}

function addNodeCard(nodeId, node) {
    const card = ce('div', 'npc-card');

    const head = ce('div', 'npc-card-head');
    const idInp = ce('input', 'nnode-id');
    idInp.value = nodeId;
    idInp.placeholder = '节点 ID（如 greet）';
    idInp.addEventListener('input', refreshNodeDatalist);
    const delNode = ce('button', 'ed-btn mini danger', '删除节点');
    delNode.type = 'button';
    delNode.addEventListener('click', () => { card.remove(); refreshNodeDatalist(); });
    head.append('节点', idInp, delNode);

    const ta = ce('textarea', 'nnode-text');
    ta.rows = 2;
    ta.placeholder = 'NPC 说的话（玩家进入此节点时显示）';
    ta.value = node.text || '';

    const choicesBox = ce('div', 'nnode-choices');
    (node.choices || []).forEach(ch => addChoiceRow(choicesBox, ch));
    const addChBtn = ce('button', 'ed-btn mini', '+ 添加选项');
    addChBtn.type = 'button';
    addChBtn.addEventListener('click', () => addChoiceRow(choicesBox, null));

    card.append(head, ta, choicesBox, addChBtn);
    $('npc-nodes').appendChild(card);
    refreshNodeDatalist();
}

function addChoiceRow(box, choice) {
    const ch = choice || {};
    const row = ce('div', 'npc-choice');

    const line1 = ce('div', 'npc-choice-line');
    const textInp = ce('input', 'nc-text');
    textInp.placeholder = '选项文本（玩家点的那句话）';
    textInp.value = ch.text || '';
    const delCh = ce('button', 'ed-btn mini danger', '删选项');
    delCh.type = 'button';
    delCh.addEventListener('click', () => row.remove());
    line1.append(textInp, delCh);

    const line2 = ce('div', 'npc-choice-line');
    const nextInp = ce('input', 'nc-next');
    nextInp.setAttribute('list', 'npc-dl-nodes');
    nextInp.placeholder = '下一节点（留空 = 结束对话）';
    nextInp.value = ch.next || '';
    const condSel = ce('select', 'nc-cond-type');
    const t = condTypeOf(ch.if);
    condSel.innerHTML = optionsHtml(COND_OPTIONS, t);
    const condParam = ce('input', 'nc-cond-param');
    configureParamInput(condParam, COND_PARAM_CFG[t]);
    condParam.value = ch.if && t ? ch.if[t] : '';
    condSel.addEventListener('change', () => {
        configureParamInput(condParam, COND_PARAM_CFG[condSel.value]);
    });
    line2.append('下一节点', nextInp, '显示条件', condSel, condParam);

    const effectsBox = ce('div', 'nc-effects');
    (ch.effects || []).forEach(eff => addEffectRow(effectsBox, eff));
    const addEffBtn = ce('button', 'ed-btn mini', '+ 添加效果');
    addEffBtn.type = 'button';
    addEffBtn.addEventListener('click', () => addEffectRow(effectsBox, null));

    row.append(line1, line2, effectsBox, addEffBtn);
    box.appendChild(row);
}

function addEffectRow(box, effect) {
    const eff = effect || {};
    const row = ce('div', 'npc-effect-row');
    const typeSel = ce('select', 'ne-type');
    typeSel.innerHTML = optionsHtml(EFFECT_OPTIONS, eff.type || '');
    const param = ce('input', 'ne-param');
    configureParamInput(param, EFFECT_PARAM_CFG[eff.type]);
    param.value = eff ? (eff.item || eff.flag || eff.scene || eff.enemy ||
        (eff.amount != null ? eff.amount : '')) : '';
    typeSel.addEventListener('change', () => {
        configureParamInput(param, EFFECT_PARAM_CFG[typeSel.value]);
    });
    const del = ce('button', 'ed-btn mini danger', '删');
    del.type = 'button';
    del.addEventListener('click', () => row.remove());
    row.append(typeSel, param, del);
    box.appendChild(row);
}

function buildCond(t, raw) {
    if (t === 'gold_gte') return { gold_gte: parseInt(raw, 10) };
    return { [t]: raw };
}

function buildEffect(t, raw) {
    if (['heal', 'max_hp', 'gold'].includes(t)) return { type: t, amount: parseInt(raw, 10) };
    if (t === 'set_flag') return { type: t, flag: raw };
    if (t === 'give_item' || t === 'remove_item') return { type: t, item: raw };
    if (t === 'teleport') return { type: t, scene: raw };
    if (t === 'start_combat') return { type: t, enemy: raw };
    return { type: t };
}

function collectNpcPayload() {
    const greeting_rules = [];
    document.querySelectorAll('#npc-rules .npc-rule-row').forEach(row => {
        const node = row.querySelector('.nr-node').value.trim();
        if (!node) return;  // 没填目标节点的行视为未配置
        const rule = { node };
        const t = row.querySelector('.nr-cond-type').value;
        const p = row.querySelector('.nr-cond-param').value.trim();
        if (t) rule.if = buildCond(t, p);
        greeting_rules.push(rule);
    });

    const nodes = {};
    document.querySelectorAll('#npc-nodes .npc-card').forEach(card => {
        const nid = card.querySelector('.nnode-id').value.trim();
        if (!nid) return;
        const choices = [];
        card.querySelectorAll('.nnode-choices .npc-choice').forEach(row => {
            const choice = { text: row.querySelector('.nc-text').value };
            const next = row.querySelector('.nc-next').value.trim();
            if (next) choice.next = next;
            const t = row.querySelector('.nc-cond-type').value;
            const p = row.querySelector('.nc-cond-param').value.trim();
            if (t) choice.if = buildCond(t, p);
            const effects = [];
            row.querySelectorAll('.nc-effects .npc-effect-row').forEach(er => {
                const et = er.querySelector('.ne-type').value;
                if (et) effects.push(buildEffect(et, er.querySelector('.ne-param').value.trim()));
            });
            if (effects.length) choice.effects = effects;
            choices.push(choice);
        });
        nodes[nid] = { text: card.querySelector('.nnode-text').value, choices };
    });

    return {
        id: $('npc-id').value.trim(),
        name: $('npc-name').value,
        scene_id: $('npc-scene').value,
        greeting: $('npc-greeting').value.trim() || 'greet',
        greeting_rules,
        nodes,
    };
}

async function saveNpc() {
    const payload = collectNpcPayload();
    const res = await fetch('/api/editor/npc', {
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

async function deleteNpc() {
    const npc = DATA.npcs[selectedId];
    if (!npc) return;
    if (!confirm(`确认删除 NPC【${npc.name || selectedId}】？该操作不可撤销（可在 npc_dialogues.json.bak 找回）。`)) return;
    const res = await fetch('/api/editor/npc/' + encodeURIComponent(selectedId), { method: 'DELETE' });
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
