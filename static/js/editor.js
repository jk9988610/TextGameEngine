/* ==========================================================================
   editor.js —— 游戏内容编辑器逻辑（地点 / 物品 / 标签 / 角色 表单）
   角色 = 基础字段 + 标签字段组；贴 talkable 展开对话编辑区，贴 enemy 展开 Beat 配置。
   纯原生 JS，不依赖游戏页任何脚本；所有写操作走 /api/editor/* 并由后端校验。
   ========================================================================== */

let DATA = { scenes: {}, items: {}, tags: {}, characters: {}, enemies: {}, npcs: {}, config: { event_rules: [] }, initial_scene: '' };
let tab = 'scenes';          // 当前页签：scenes | items | tags | characters | events
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
        // 编辑器偏好来自数据文件 editor_settings.json（不用浏览器存储）。
        // 只 Object.assign 到既有共享对象上（npc_canvas 持有同一引用），不可换对象。
        if (json.editor_settings) {
            window.EditorSettings = window.EditorSettings || { collapse: "middle", autosave: false };
            Object.assign(window.EditorSettings, {
                collapse: json.editor_settings.collapse === "right" ? "right" : "middle",
                autosave: !!json.editor_settings.autosave,
            });
        }
    } catch (e) {
        toast('无法连接服务器', 'error');
        return;
    }

    // 事件字典 → 条件下拉/参数配置/引用候选（画布模式也要，故放在 early-return 之前）
    rebuildDictOptions();

    // 画布模式下重拉只刷新工作台（保持模式/选中），列表 DOM 不动
    if (document.body.classList.contains('workbench-canvas')) {
        window.NpcCanvas?.refresh();
        window.SceneCanvas?.refresh();
        return;
    }
    renderList();
    if (selectedId && DATA[tab][selectedId]) {
        showForm(tab, selectedId);  // 保存后刷新表单，保持选中
    } else {
        showEmpty();
    }
}

// 画布工作台调用的两个列表侧动作
window.EditorActions = {
    async reloadData() { return loadData(); },
    editNpc(id) {
        setWorkbench(false);
        switchTab('characters');
        showForm('characters', id);
    },
    editScene(id) {
        setWorkbench(false);
        switchTab('scenes');
        showForm('scenes', id);
    },
};

/* 顶栏「列表编辑 | 画布编辑」模式开关
 * 角色页签 = 对话树画布；地点页签（及其他页签）= 世界地图画布 */
function setWorkbench(canvasOn) {
    document.body.classList.toggle('workbench-canvas', canvasOn);
    $('mode-list').classList.toggle('active', !canvasOn);
    $('mode-canvas').classList.toggle('active', canvasOn);
    window.NpcCanvas?.exit();
    window.SceneCanvas?.exit();
    if (!canvasOn) return;
    if (tab === 'characters') window.NpcCanvas?.enter(selectedId);
    else window.SceneCanvas?.enter(tab === 'scenes' ? selectedId : null);
}

// ---------- 列表 / 页签 ----------
function bindStaticEvents() {
    document.querySelectorAll('.ed-tab').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    $('mode-list').addEventListener('click', () => setWorkbench(false));
    $('mode-canvas').addEventListener('click', () => setWorkbench(true));
    $('btn-new').addEventListener('click', onNew);
    $('btn-playtest').addEventListener('click', () => window.open('index.html', '_blank'));

    $('scene-save').addEventListener('click', saveScene);
    $('scene-delete').addEventListener('click', deleteScene);
    $('scene-cancel').addEventListener('click', showEmpty);
    $('item-save').addEventListener('click', saveItem);
    $('item-delete').addEventListener('click', deleteItem);
    $('item-cancel').addEventListener('click', showEmpty);
    $('tag-save').addEventListener('click', saveTag);
    $('tag-delete').addEventListener('click', deleteTag);
    $('tag-cancel').addEventListener('click', showEmpty);
    $('tag-add-field').addEventListener('click', () => addTagFieldRow(null));
    $('character-save').addEventListener('click', saveCharacter);
    $('character-delete').addEventListener('click', deleteCharacter);
    $('character-cancel').addEventListener('click', showEmpty);
    $('npc-add-rule').addEventListener('click', () => addRuleRow(null));
    $('npc-add-node').addEventListener('click', () => addNodeCard('', { text: '', choices: [] }));
    $('btn-config').addEventListener('click', openConfig);
    $('config-close').addEventListener('click', showEmpty);
    $('config-save').addEventListener('click', saveConfig);
    $('event-save').addEventListener('click', saveEventRule);
    $('event-delete').addEventListener('click', deleteEventRule);
    $('event-cancel').addEventListener('click', showEmpty);
    $('event-add-effect').addEventListener('click',
        () => addEffectRow($('event-effects'), null));
    $('event-on').addEventListener('change', (e) => renderEventArgs(e.target.value, {}));
}

function switchTab(next) {
    tab = next;
    selectedId = null;
    isNew = false;
    document.body.classList.remove('config-open');
    document.querySelectorAll('.ed-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === tab));
    renderList();
    showEmpty();
    // 画布模式下切页签 = 直接切换对应画布（角色对话树 ↔ 世界地图）
    if (document.body.classList.contains('workbench-canvas')) {
        window.NpcCanvas?.exit();
        window.SceneCanvas?.exit();
        if (tab === 'characters') window.NpcCanvas?.enter(null);
        else window.SceneCanvas?.enter(null);
    }
}

function renderList() {
    const listEl = $('ed-list');
    listEl.innerHTML = '';
    // 事件规则是 config.event_rules 数组，单独渲染
    if (tab === 'events') {
        const rules = (DATA.config && DATA.config.event_rules) || [];
        for (const rule of rules) {
            const row = document.createElement('div');
            row.className = 'ed-list-item' + (rule.id === selectedId ? ' selected' : '');
            const name = rule.label || rule.id;
            row.innerHTML = `<div class="li-name"></div><div class="li-id"></div>`;
            row.querySelector('.li-name').textContent = name;
            row.querySelector('.li-id').textContent =
                `${rule.id} · ${(triggerOptions().find(o => o.v === rule.on) || {}).label || rule.on}`;
            row.addEventListener('click', () => showForm('events', rule.id));
            listEl.appendChild(row);
        }
        return;
    }
    const coll = DATA[tab];
    for (const id of sortedKeys(coll)) {
        const row = document.createElement('div');
        row.className = 'ed-list-item' + (id === selectedId ? ' selected' : '');
        // 标签用 label（没有 name 字段），副标题显示贴用对象类型；角色副标题显示已贴标签
        const name = tab === 'tags' ? (coll[id].label || id) : (coll[id].name || id);
        row.innerHTML = `<div class="li-name"></div><div class="li-id"></div>`;
        row.querySelector('.li-name').textContent = name;
        let sub = id;
        if (tab === 'tags') {
            sub = `${id} · ${TAG_TARGET_LABELS[coll[id].target] || coll[id].target || '物品'}`;
        } else if (tab === 'characters') {
            const labels = (coll[id].tags || [])
                .map(t => (DATA.tags[t] || {}).label || t);
            if (labels.length) sub = `${id} · ${labels.join('/')}`;
        }
        row.querySelector('.li-id').textContent = sub;
        row.addEventListener('click', () => showForm(tab, id));
        listEl.appendChild(row);
    }
}

function showEmpty() {
    selectedId = null;
    isNew = false;
    document.body.classList.remove('config-open');
    $('ed-empty').classList.remove('hidden');
    $('form-scene').classList.add('hidden');
    $('form-item').classList.add('hidden');
    $('form-tag').classList.add('hidden');
    $('form-character').classList.add('hidden');
    $('form-event').classList.add('hidden');
    $('form-config').classList.add('hidden');
    renderList();
}

function onNew() {
    isNew = true;
    selectedId = null;
    $('ed-empty').classList.add('hidden');
    renderList();
    if (tab === 'scenes') {
        hideAllForms();
        $('form-scene').classList.remove('hidden');
        $('scene-form-title').textContent = '新建地点';
        $('scene-id').value = '';
        $('scene-id').disabled = false;
        $('scene-name').value = '';
        $('scene-desc').value = '';
        $('scene-exits-raw').value = '';
        $('scene-enemies-raw').value = '';
        renderOptionGroups(null, [], [], []);
        $('scene-delete').classList.add('hidden');
    } else if (tab === 'characters') {
        hideAllForms();
        $('form-character').classList.remove('hidden');
        renderCharacterForm(null);
    } else if (tab === 'events') {
        hideAllForms();
        $('form-event').classList.remove('hidden');
        renderEventForm(null);
    } else if (tab === 'tags') {
        hideAllForms();
        $('form-tag').classList.remove('hidden');
        renderTagForm(null);
    } else {
        hideAllForms();
        $('form-item').classList.remove('hidden');
        $('item-form-title').textContent = '新建物品';
        $('item-id').value = '';
        $('item-id').disabled = false;
        $('item-name').value = '';
        $('item-desc').value = '';
        renderItemTagFields(null);
        $('item-delete').classList.add('hidden');
    }
}

// 隐藏右侧所有表单（切页签/新建前先清场）
function hideAllForms() {
    for (const id of ['form-scene', 'form-item', 'form-tag', 'form-character',
                      'form-event', 'form-config']) {
        $(id).classList.add('hidden');
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
        hideAllForms();
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
    } else if (kind === 'characters') {
        const ch = DATA.characters[id];
        if (!ch) return;
        hideAllForms();
        $('form-character').classList.remove('hidden');
        renderCharacterForm(ch);
    } else if (kind === 'events') {
        const rule = (DATA.config.event_rules || []).find(r => r.id === id);
        if (!rule) return;
        hideAllForms();
        $('form-event').classList.remove('hidden');
        renderEventForm(rule);
    } else if (kind === 'tags') {
        const spec = DATA.tags[id];
        if (!spec) return;
        hideAllForms();
        $('form-tag').classList.remove('hidden');
        renderTagForm(spec);
    } else {
        const it = DATA.items[id];
        if (!it) return;
        hideAllForms();
        $('form-item').classList.remove('hidden');
        $('item-form-title').textContent = '编辑物品';
        $('item-id').value = it.id;
        $('item-id').disabled = true;
        $('item-name').value = it.name || '';
        $('item-desc').value = it.description || '';
        renderItemTagFields(it);
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
    const payload = {
        id: $('item-id').value.trim(),
        name: $('item-name').value,
        description: $('item-desc').value,
        tags: appliedItemTags(),
    };
    // 勾上的标签，其字段组里每个字段都按 key 平铺进 payload（后端按标签规格归一）
    for (const tid of payload.tags) {
        for (const f of fieldSpecs(tid)) {
            payload[f.key] = readFieldInput(tid, f);
        }
    }

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

// ---------- 标签：字段组展开（物品按标签动态长字段） ----------
const TAG_TARGET_LABELS = { item: '物品', scene: '地点', character: '角色' };
const TAG_FIELD_TYPES = [
    { v: 'int', label: '整数' },
    { v: 'text', label: '单行文本' },
    { v: 'textarea', label: '多行文本' },
    { v: 'text_list', label: '文本列表（逗号分隔）' },
    { v: 'item_list', label: '物品多选' },
];

function targetTags(target) {
    return sortedKeys(DATA.tags || {})
        .filter(id => (DATA.tags[id].target || 'item') === target);
}
function fieldSpecs(tid) {
    const spec = (DATA.tags || {})[tid];
    return (spec && spec.fields) || [];
}
// 读取某标签勾选区里已勾选的标签 id
function appliedTagsIn(hostId) {
    return Array.from(document.querySelectorAll(`#${hostId} input[type=checkbox]`))
        .filter(cb => cb.checked).map(cb => cb.dataset.tag);
}
function appliedItemTags() { return appliedTagsIn('item-tags'); }

// 单个字段的输入控件（type 决定控件形态；item_list 为物品多选）
function fieldInputEl(tid, f, value) {
    const wrap = document.createElement('label');
    wrap.className = 'ed-field';
    const cap = document.createElement('span');
    cap.textContent = f.label || f.key;
    wrap.appendChild(cap);

    if (f.type === 'item_list') {
        const box = document.createElement('div');
        box.className = 'ed-check-group';
        box.dataset.field = f.key;
        const selected = new Set(value || []);
        for (const iid of sortedKeys(DATA.items)) {
            const l = document.createElement('label');
            l.className = 'ed-check';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = iid;
            cb.checked = selected.has(iid);
            const s = document.createElement('span');
            s.textContent = DATA.items[iid].name || iid;
            l.append(cb, s);
            box.appendChild(l);
        }
        wrap.appendChild(box);
        return wrap;
    }

    let el;
    if (f.type === 'textarea') {
        el = document.createElement('textarea');
        el.rows = 3;
    } else if (f.type === 'int') {
        el = document.createElement('input');
        el.type = 'number';
        el.step = '1';
        if (f.min != null) el.min = f.min;
        if (f.max != null) el.max = f.max;
    } else {
        el = document.createElement('input');
        el.type = 'text';
        if (f.type === 'text_list') el.placeholder = '逗号分隔，例：bash,charge';
    }
    el.dataset.field = f.key;
    const fallback = f.default != null ? f.default : (f.type === 'int' ? 0 : '');
    el.value = value != null ? (Array.isArray(value) ? value.join(',') : value) : fallback;
    wrap.appendChild(el);
    return wrap;
}

// 读回某标签某字段的输入值（item_list 返回勾选的 id 数组）
function readFieldInput(tid, f) {
    const host = document.querySelector(`[data-tag-group="${tid}"]`);
    if (!host) return undefined;
    const el = host.querySelector(`[data-field="${f.key}"]`);
    if (!el) return undefined;
    if (f.type === 'item_list') {
        return Array.from(el.querySelectorAll('input:checked')).map(c => c.value);
    }
    return el.value;
}

// 渲染某目标类型（item / character）的标签勾选区 + 各标签字段组。
// applied 为已贴标签 id 数组，values 为字段取值来源对象（null = 新建，取默认值），
// onToggle 为勾选变化后的回调（角色表单用它联动显示对话区 / Beat 区）。
function renderTagFields(tagsHostId, fieldsHostId, target, applied, values,
                         emptyHint, onToggle) {
    const tagsBox = $(tagsHostId);
    const fieldsBox = $(fieldsHostId);
    tagsBox.innerHTML = '';
    fieldsBox.innerHTML = '';
    const appliedSet = new Set(applied || []);
    const ids = targetTags(target);
    if (!ids.length) {
        const p = document.createElement('p');
        p.className = 'ed-tip';
        p.textContent = emptyHint;
        tagsBox.appendChild(p);
        return;
    }
    for (const tid of ids) {
        const spec = DATA.tags[tid];
        const cbWrap = document.createElement('label');
        cbWrap.className = 'ed-check';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.tag = tid;
        cb.checked = appliedSet.has(tid);
        const sp = document.createElement('span');
        sp.textContent = spec.label || tid;
        cbWrap.append(cb, sp);
        tagsBox.appendChild(cbWrap);

        const group = document.createElement('div');
        group.dataset.tagGroup = tid;
        group.classList.toggle('hidden', !cb.checked);
        const title = document.createElement('div');
        title.className = 'ed-tip';
        title.textContent = `标签【${spec.label || tid}】的字段组`;
        group.appendChild(title);
        for (const f of fieldSpecs(tid)) {
            group.appendChild(fieldInputEl(tid, f, values && values[f.key]));
        }
        fieldsBox.appendChild(group);

        cb.addEventListener('change', () => {
            group.classList.toggle('hidden', !cb.checked);
            // 首次勾上时把空字段填成默认值，省得用户自己填
            if (cb.checked) {
                for (const f of fieldSpecs(tid)) {
                    const el = group.querySelector(`[data-field="${f.key}"]`);
                    if (el && el.tagName === 'INPUT' && el.value === '' && f.default != null) {
                        el.value = f.default;
                    }
                }
            }
            if (onToggle) onToggle();
        });
    }
}

// 物品表单的标签区（薄封装）
function renderItemTagFields(item) {
    renderTagFields('item-tags', 'item-tag-fields', 'item', (item && item.tags) || [],
        item, '还没有可用于物品的标签，去「标签」页签新建一个。');
}

// ---------- 标签：标签页签表单（定义标签 = 目标类型 + 字段组） ----------
function renderTagForm(spec) {
    const isEdit = !!spec;
    $('tag-form-title').textContent = isEdit ? '编辑标签' : '新建标签';
    $('tag-id').value = isEdit ? (spec.id || '') : '';
    $('tag-id').disabled = isEdit;
    $('tag-label').value = isEdit ? (spec.label || '') : '';
    $('tag-target').value = isEdit ? (spec.target || 'item') : 'item';
    $('tag-runtime-flag').value = isEdit ? ((spec.runtime || {}).flag || '') : '';
    $('tag-fields').innerHTML = '';
    for (const f of (isEdit ? (spec.fields || []) : [])) addTagFieldRow(f);
    $('tag-delete').classList.toggle('hidden', !isEdit);
}

function addTagFieldRow(field) {
    const f = field || {};
    const row = document.createElement('div');
    row.className = 'ed-field-row';
    row.dataset.tagField = '1';
    row.innerHTML = `
        <label class="ed-field"><span>key</span>
            <input type="text" data-k="key" placeholder="damage"></label>
        <label class="ed-field"><span>显示名</span>
            <input type="text" data-k="label" placeholder="伤害值"></label>
        <label class="ed-field"><span>类型</span>
            <select data-k="type">${TAG_FIELD_TYPES
                .map(t => `<option value="${t.v}">${t.label}</option>`).join('')}</select></label>
        <label class="ed-field"><span>默认值</span>
            <input type="text" data-k="default"></label>
        <label class="ed-field"><span>最小值</span>
            <input type="number" data-k="min" step="1"></label>
        <label class="ed-field"><span>最大值</span>
            <input type="number" data-k="max" step="1"></label>
        <button type="button" class="ed-btn mini" data-k="remove">删除</button>`;
    row.querySelector('[data-k="key"]').value = f.key || '';
    row.querySelector('[data-k="label"]').value = f.label || '';
    row.querySelector('[data-k="type"]').value = f.type || 'int';
    row.querySelector('[data-k="default"]').value = f.default != null ? f.default : '';
    row.querySelector('[data-k="min"]').value = f.min != null ? f.min : '';
    row.querySelector('[data-k="max"]').value = f.max != null ? f.max : '';
    row.querySelector('[data-k="remove"]').addEventListener('click', () => row.remove());
    $('tag-fields').appendChild(row);
}

function collectTagPayload() {
    const payload = {
        id: $('tag-id').value.trim(),
        label: $('tag-label').value,
        target: $('tag-target').value,
        fields: [],
    };
    const flag = $('tag-runtime-flag').value.trim();
    if (flag) payload.runtime = { flag };
    document.querySelectorAll('#tag-fields [data-tag-field]').forEach(row => {
        const v = (k) => {
            const el = row.querySelector(`[data-k="${k}"]`);
            return el ? el.value.trim() : '';
        };
        const key = v('key');
        if (!key) return;
        const field = { key, label: v('label'), type: v('type') };
        if (v('default') !== '') field.default = v('default');
        if (v('min') !== '') field.min = parseInt(v('min'), 10);
        if (v('max') !== '') field.max = parseInt(v('max'), 10);
        payload.fields.push(field);
    });
    return payload;
}

async function saveTag() {
    const res = await fetch('/api/editor/tag', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectTagPayload()),
    });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    selectedId = data.id;
    isNew = false;
    await loadData();
    toast(data.message, 'success');
}

async function deleteTag() {
    const spec = DATA.tags[selectedId];
    if (!spec) return;
    if (!confirm(`确认删除标签【${spec.label || selectedId}】？该操作不可撤销（可在 tags.json.bak 找回）。`)) return;
    const res = await fetch('/api/editor/tag/' + encodeURIComponent(selectedId), { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    showEmpty();
    await loadData();
    toast(data.message, 'success');
}

// ---------- 敌人：保存 / 删除 ----------
// Beat 战斗字段的表单 <-> 数据互转（留空 = 用引擎默认）
function resetEnemyBeatFields(em) {
    const brain = (em && em.brain) || {};
    $('enemy-heavy').value = (em && em.heavy_attack != null) ? em.heavy_attack : '';
    $('enemy-rhythm').value = (brain.rhythm || []).join(',');
    $('enemy-frenzy-rhythm').value = (brain.frenzy_rhythm || []).join(',');
    $('enemy-lowhp-mode').value = brain.low_hp_mode || '';
    $('enemy-lowhp-ratio').value = brain.low_hp_ratio != null ? brain.low_hp_ratio : '';
    $('enemy-dodge-charge').checked = brain.dodge_player_charge !== false;
    $('enemy-telegraphs').value = (em && em.telegraphs)
        ? JSON.stringify(em.telegraphs, null, 2) : '';
    $('enemy-intents').value = (em && em.intents)
        ? JSON.stringify(em.intents, null, 2) : '';
}

function collectEnemyBeatPayload() {
    const payload = {};
    const heavy = $('enemy-heavy').value.trim();
    if (heavy !== '') payload.heavy_attack = parseInt(heavy, 10);
    const rhythm = $('enemy-rhythm').value.split(',').map(s => s.trim()).filter(Boolean);
    const frenzy = $('enemy-frenzy-rhythm').value.split(',').map(s => s.trim()).filter(Boolean);
    const mode = $('enemy-lowhp-mode').value;
    const ratio = $('enemy-lowhp-ratio').value.trim();
    const dodge = $('enemy-dodge-charge').checked;
    if (rhythm.length || frenzy.length || mode || ratio !== '' || !dodge) {
        const brain = {};
        if (rhythm.length) brain.rhythm = rhythm;
        if (frenzy.length) brain.frenzy_rhythm = frenzy;
        if (mode) brain.low_hp_mode = mode;
        if (ratio !== '') brain.low_hp_ratio = parseFloat(ratio);
        brain.dodge_player_charge = dodge;
        payload.brain = brain;
    }
    for (const [key, el] of [['telegraphs', $('enemy-telegraphs')], ['intents', $('enemy-intents')]]) {
        const raw = el.value.trim();
        if (!raw) continue;
        try {
            payload[key] = JSON.parse(raw);
        } catch (e) {
            el.focus();
            throw new Error(`${key === 'telegraphs' ? '模糊征兆' : '意图明牌'} 不是合法 JSON：${e.message}`);
        }
    }
    return payload;
}

// ---------- 角色：保存 / 删除 ----------
// 角色 payload = 基础字段 + 已贴标签的字段组（按 key 平铺）+ 对话树 / Beat 配置（按标签取舍）
function collectCharacterPayload() {
    const tags = appliedTagsIn('character-tags');
    const payload = {
        id: $('character-id').value.trim(),
        name: $('character-name').value,
        description: $('character-desc').value,
        tags,
    };
    for (const tid of tags) {
        for (const f of fieldSpecs(tid)) {
            payload[f.key] = readFieldInput(tid, f);
        }
    }
    if (tags.includes('talkable')) Object.assign(payload, collectDialoguePayload());
    if (tags.includes('enemy')) Object.assign(payload, collectEnemyBeatPayload());
    return payload;
}

async function saveCharacter() {
    let payload;
    try {
        payload = collectCharacterPayload();
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    const res = await fetch('/api/editor/character', {
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

async function deleteCharacter() {
    const ch = DATA.characters[selectedId];
    if (!ch) return;
    if (!confirm(`确认删除角色【${ch.name || selectedId}】？该操作不可撤销（可在 characters.json.bak 找回）。`)) return;
    const res = await fetch('/api/editor/character/' + encodeURIComponent(selectedId), { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    showEmpty();
    await loadData();
    toast(data.message, 'success');
}

// ---------- NPC：属性 / 条件问候 / 对话节点树编辑（列表式） ----------
// 条件/效果/触发器的可选条目全部来自事件字典（DATA.events），
// 由 rebuildDictOptions() 在数据加载后填充 —— 字典里加条目，编辑器选项自动出现。
let COND_OPTIONS = [];
let EFFECT_OPTIONS = [];
let COND_PARAM_CFG = {};
// 引用型参数默认挂到这些 datalist 上（画布「入口设置」面板也用同一套配置）
const REF_DATALIST = { item: 'npc-dl-items', scene: 'npc-dl-scenes', enemy: 'npc-dl-enemies' };

function dictSection(name) { return (DATA.events && DATA.events[name]) || []; }
function dictEntry(name, key) { return dictSection(name).find(e => e.key === key) || null; }
function dictParams(entry) { return (entry && entry.params) || []; }

/** 字典参数 → 单值控件配置（{list,type,ph}，供 configureParamInput 使用） */
function paramCfg(p) {
    if (!p) return null;
    if (p.type === 'ref') {
        return { list: REF_DATALIST[p.ref] || '', type: 'text', ph: (p.label || '') + ' ID' };
    }
    if (p.type === 'int') {
        return { list: '', type: 'number', ph: p.placeholder || p.label || '数值' };
    }
    return { list: '', type: 'text', ph: p.placeholder || p.label || '' };
}

function rebuildDictOptions() {
    COND_OPTIONS = [{ v: '', label: '无条件' }].concat(
        dictSection('conditions').map(e => ({ v: e.key, label: e.label || e.key })));
    EFFECT_OPTIONS = [{ v: '', label: '（无效果）' }].concat(
        dictSection('effects').map(e => ({ v: e.key, label: e.label || e.key })));
    COND_PARAM_CFG = {};
    dictSection('conditions').forEach(e => {
        COND_PARAM_CFG[e.key] = paramCfg(dictParams(e)[0]);
    });
    // 引用型条件参数靠 datalist 列候选，这里先灌满（事件页签不一定进过角色表单）
    fillRefDatalists();
    // 条件选项/参数配置共享给画布工作台的「入口设置」面板
    window.EditorShared = { COND_OPTIONS, COND_PARAM_CFG, condTypeOf, buildCond };
}

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
    const e = dictSection('conditions').find(x => x.key in cond);
    return e ? e.key : '';
}

function escAttr(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 引用型参数下拉的全部候选（来自已填充的 datalist 的 option） */
function refOptionsHtmlFromDl(dlId, selected) {
    const dl = $(dlId);
    const options = dl ? [...dl.options] : [];
    let html = '<option value=""></option>';
    for (const o of options) {
        const val = o.value;
        const label = o.textContent && o.textContent !== val ? `${val}（${o.textContent}）` : val;
        html += `<option value="${escAttr(val)}"${val === selected ? ' selected' : ''}>${escAttr(label)}</option>`;
    }
    return html;
}

/**
 * 配置条件/效果参数输入：引用类（带 list）改造成真正的下拉，列出全部候选
 * （datalist 输入点击时浏览器只显示与当前值匹配的项，无法列出全部）；
 * 返回实际的控件元素（select 或原 input），调用方需接收返回值。
 */
function configureParamInput(inp, cfg) {
    if (!cfg) { inp.classList.add('hidden'); inp.value = ''; return inp; }
    inp.classList.remove('hidden');
    if (cfg.list) {
        const sel = document.createElement('select');
        sel.className = inp.className;
        sel.innerHTML = refOptionsHtmlFromDl(cfg.list, inp.value || '');
        sel.value = inp.value || '';
        inp.replaceWith(sel);
        return sel;
    }
    inp.type = cfg.type;
    inp.placeholder = cfg.ph;
    if (cfg.list) inp.setAttribute('list', cfg.list);
    else inp.removeAttribute('list');
    return inp;
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

// 角色的对话编辑区（贴 talkable 时可见；ch 为 null 表示新建）
function renderDialogueFields(ch) {
    const sceneSel = $('npc-scene');
    sceneSel.innerHTML = '';
    for (const sid of sortedKeys(DATA.scenes)) {
        sceneSel.appendChild(ce('option', null, `${DATA.scenes[sid].name || sid}（${sid}）`)).value = sid;
    }
    sceneSel.value = ch ? (ch.scene_id || '') : (sortedKeys(DATA.scenes)[0] || '');
    $('npc-greeting').value = ch ? (ch.greeting || 'greet') : 'greet';

    fillRefDatalists();

    const rulesBox = $('npc-rules');
    rulesBox.innerHTML = '';
    (ch && ch.greeting_rules || []).forEach(r => addRuleRow(r));

    const nodesBox = $('npc-nodes');
    nodesBox.innerHTML = '';
    if (ch && Object.keys(ch.nodes || {}).length) {
        Object.entries(ch.nodes).forEach(([nid, node]) => addNodeCard(nid, node));
    } else {
        addNodeCard('greet', { text: '', choices: [] });
    }
    refreshNodeDatalist();
}

// 角色表单整体渲染（基础字段 + 标签字段组 + 对话区 + Beat 区）
function renderCharacterForm(ch) {
    $('character-form-title').textContent = ch ? '编辑角色' : '新建角色';
    $('character-id').value = ch ? ch.id : '';
    $('character-id').disabled = !!ch;
    $('character-name').value = ch ? (ch.name || '') : '';
    $('character-desc').value = ch ? (ch.description || '') : '';

    renderTagFields('character-tags', 'character-tag-fields', 'character',
        (ch && ch.tags) || [], ch,
        '还没有可用于角色的标签，去「标签」页签新建（例如 enemy / talkable）。',
        syncCharacterBlocks);

    renderDialogueFields(ch);
    resetEnemyBeatFields(ch && (ch.tags || []).includes('enemy') ? ch : null);
    syncCharacterBlocks();
    $('character-delete').classList.toggle('hidden', !ch);
}

// 勾/摘 enemy、talkable 标签时联动显示 Beat 配置区 / 对话编辑区
function syncCharacterBlocks() {
    const applied = new Set(appliedTagsIn('character-tags'));
    $('character-dialogue-box').classList.toggle('hidden', !applied.has('talkable'));
    $('character-beat-box').classList.toggle('hidden', !applied.has('enemy'));
}

function addRuleRow(rule) {
    const cond = rule ? rule.if : null;
    const t = condTypeOf(cond);
    const row = ce('div', 'npc-rule-row');
    const typeSel = ce('select', 'nr-cond-type');
    typeSel.innerHTML = optionsHtml(COND_OPTIONS, t);
    let param = ce('input', 'nr-cond-param');
    param = configureParamInput(param, COND_PARAM_CFG[t]);
    param.value = cond && t ? (t === 'gold_gte' ? cond[t] : cond[t]) : '';
    typeSel.addEventListener('change', () => {
        param = configureParamInput(param, COND_PARAM_CFG[typeSel.value]);
        // 引用型下拉在 change 后若不保留值，清空（避免残留上一个类型的值）
        param.value = '';
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
    // 已有节点改名会涉及全树连线重映射，统一到画布抽屉做（自动重映射）；
    // 新建节点（nodeId 为空）仍在此处填写 ID
    if (nodeId) {
        idInp.readOnly = true;
        idInp.title = '节点 ID 改名请到「画布编辑」选中该节点修改（会自动同步所有连线）';
    }
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
    let condParam = ce('input', 'nc-cond-param');
    condParam = configureParamInput(condParam, COND_PARAM_CFG[t]);
    condParam.value = ch.if && t ? ch.if[t] : '';
    condSel.addEventListener('change', () => {
        condParam = configureParamInput(condParam, COND_PARAM_CFG[condSel.value]);
        condParam.value = ch.if && condSel.value ? ch.if[condSel.value] : '';
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
    const paramsBox = ce('span', 'ne-params');
    const del = ce('button', 'ed-btn mini danger', '删');
    del.type = 'button';
    del.addEventListener('click', () => row.remove());
    typeSel.addEventListener('change', () => renderEffectParams(typeSel, paramsBox, {}));
    row.append(typeSel, paramsBox, del);
    renderEffectParams(typeSel, paramsBox, eff);
    box.appendChild(row);
}

function refOptionsHtml(coll, selected, withEmpty, emptyLabel) {
    let html = withEmpty ? `<option value="">${emptyLabel || '（不限）'}</option>` : '';
    for (const id of sortedKeys(coll || {})) {
        const name = coll[id].name || id;
        html += `<option value="${id}"${id === selected ? ' selected' : ''}>${name}（${id}）</option>`;
    }
    return html;
}

/** 引用型参数字典项 → 候选项 HTML（item/scene/enemy/npc 直查；node/exit 依 parent 联动） */
function refOptionsForParam(p, parentValue, selected, withEmpty) {
    if (p.ref === 'node') {
        const nodes = (DATA.npcs[parentValue] || {}).nodes || {};
        return nodeOptionsHtml(nodes, selected, withEmpty);
    }
    if (p.ref === 'exit') {
        const sc = DATA.scenes[parentValue];
        let html = withEmpty ? '<option value=""></option>' : '';
        for (const x of (sc ? (sc.exits || []) : [])) {
            html += `<option value="${escAttr(x)}"${x === selected ? ' selected' : ''}>${
                escAttr((DATA.scenes[x] || {}).name || x)}</option>`;
        }
        return html;
    }
    const coll = { item: DATA.items, scene: DATA.scenes, enemy: DATA.enemies, npc: DATA.npcs }[p.ref];
    return refOptionsHtml(coll, selected, withEmpty, '（不限）');
}

/** 按字典参数项造一个控件（select/input），带 data-field 供收集；cls 为控件类名 */
function makeParamControl(p, values, cls) {
    const val = values[p.field];
    const cur = val == null ? '' : String(val);
    if (p.type === 'ref') {
        const sel = ce('select', cls);
        sel.dataset.field = p.field;
        const parentVal = p.parent ? (values[p.parent] || '') : '';
        sel.innerHTML = refOptionsForParam(p, parentVal, cur, !p.required);
        sel.value = cur;
        return sel;
    }
    const inp = ce('input', cls);
    inp.dataset.field = p.field;
    inp.type = p.type === 'int' ? 'number' : 'text';
    if (p.min != null) inp.min = p.min;
    if (p.max != null) inp.max = p.max;
    inp.placeholder = p.placeholder || p.label || '';
    inp.value = cur;
    return inp;
}

/**
 * 按字典 params 渲染一组参数控件（通用，不再特判 unlock）：
 * 参数项 type=ref 出下拉，int 出数字框，text 出文本框；
 * 带 parent 的参数在父参数变化时用新父值重建（如 unlock 的出口跟地点走）。
 */
function renderParamGroup(box, params, values, cls) {
    const build = () => {
        box.innerHTML = '';
        params.forEach(p => box.appendChild(makeParamControl(p, values, cls)));
        params.forEach(p => {
            const ctl = box.querySelector(`[data-field="${p.field}"]`);
            if (!ctl) return;
            ctl.addEventListener('change', () => {
                values[p.field] = ctl.value;
                const children = params.filter(q => q.parent === p.field);
                if (children.length) {
                    children.forEach(q => { values[q.field] = ''; });  // 父值变了，子候选失效
                    build();
                }
            });
        });
    };
    build();
}

/** 读回一组参数控件 → 参数字典（int 转数字；空值不写，交后端必填校验报错） */
function readParamGroup(box, params) {
    const out = {};
    params.forEach(p => {
        const ctl = box.querySelector(`[data-field="${p.field}"]`);
        if (!ctl) return;
        const raw = (ctl.value || '').trim();
        if (raw === '') return;
        out[p.field] = p.type === 'int' ? parseInt(raw, 10) : raw;
    });
    return out;
}

function renderEffectParams(typeSel, paramsBox, eff) {
    paramsBox.innerHTML = '';
    const entry = dictEntry('effects', typeSel.value);
    if (!entry) return;
    renderParamGroup(paramsBox, dictParams(entry), Object.assign({}, eff || {}), 'ne-param');
}

function collectEffectFromRow(row) {
    const t = row.querySelector('.ne-type').value;
    if (!t) return null;
    const entry = dictEntry('effects', t);
    if (!entry) return { type: t };
    return Object.assign({ type: t },
        readParamGroup(row.querySelector('.ne-params'), dictParams(entry)));
}

function buildCond(t, raw) {
    const p = dictParams(dictEntry('conditions', t))[0];
    return { [t]: (p && p.type === 'int') ? parseInt(raw, 10) : raw };
}

// 对话树部分（贴 talkable 时并入角色 payload；id/name 由角色基础字段提供）
function collectDialoguePayload() {
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
                const collected = collectEffectFromRow(er);
                if (collected) effects.push(collected);
            });
            if (effects.length) choice.effects = effects;
            choices.push(choice);
        });
        nodes[nid] = { text: card.querySelector('.nnode-text').value, choices };
    });

    return {
        scene_id: $('npc-scene').value,
        greeting: $('npc-greeting').value.trim() || 'greet',
        greeting_rules,
        nodes,
    };
}

// ---------- 事件规则编辑 ----------
// 触发器条目全部来自事件字典（events.json）：字典里登记了什么，编辑器就能选什么
function triggerOptions() {
    return dictSection('triggers').map(e => ({ v: e.key, label: e.label || e.key }));
}

function renderEventForm(rule) {
    $('event-form-title').textContent = rule ? '编辑事件规则' : '新建事件规则';
    $('event-id').value = rule ? rule.id : '';
    $('event-id').disabled = !!rule;
    $('event-label').value = rule ? (rule.label || '') : '';
    const onSel = $('event-on');
    const opts = triggerOptions();
    onSel.innerHTML = optionsHtml(opts, rule ? rule.on : (opts[0] ? opts[0].v : ''));
    renderEventArgs(onSel.value, rule ? (rule.if || {}) : {});
    renderEventWhen(rule ? rule.when : null);
    const box = $('event-effects');
    box.innerHTML = '';
    (rule ? rule.do || [] : []).forEach(eff => addEffectRow(box, eff));
    if (!rule) addEffectRow(box, null);
    $('event-delete').classList.toggle('hidden', !rule);
}

/** 触发参数行按字典 params 渲染（.ev-arg + data-field 供 collectEventPayload 收集） */
function renderEventArgs(on, values) {
    const box = $('event-args');
    box.onchange = null;
    const params = dictParams(dictEntry('triggers', on));
    const vals = Object.assign({}, values || {});
    const build = () => {
        box.innerHTML = '';
        params.forEach(p => {
            const row = ce('label', 'event-arg-row');
            row.append(`${p.label || p.field}${p.required ? '' : '（可空）'}`);
            row.appendChild(makeParamControl(p, vals, 'ev-arg'));
            box.appendChild(row);
        });
        params.forEach(p => {
            const ctl = box.querySelector(`.ev-arg[data-field="${p.field}"]`);
            if (!ctl) return;
            ctl.addEventListener('change', () => {
                vals[p.field] = ctl.value;
                const children = params.filter(q => q.parent === p.field);
                if (children.length) {
                    children.forEach(q => { vals[q.field] = ''; });
                    build();
                }
            });
        });
    };
    build();
}

function nodeOptionsHtml(nodes, selected, withEmpty) {
    let html = withEmpty ? '<option value="">（任意节点）</option>' : '';
    for (const nid of sortedKeys(nodes)) {
        html += `<option value="${nid}"${nid === selected ? ' selected' : ''}>${nid}</option>`;
    }
    return html;
}

function renderEventWhen(when) {
    const box = $('event-when');
    box.innerHTML = '';
    const t = condTypeOf(when);
    const typeSel = ce('select', 'ew-cond-type');
    typeSel.innerHTML = optionsHtml(COND_OPTIONS, t);
    let param = ce('input', 'ew-cond-param');
    param = configureParamInput(param, COND_PARAM_CFG[t]);
    param.value = when && t ? when[t] : '';
    typeSel.addEventListener('change', () => {
        param = configureParamInput(param, COND_PARAM_CFG[typeSel.value]);
        param.value = '';
    });
    box.append(typeSel, param);
}

function collectEventPayload() {
    const rule = {
        id: $('event-id').value.trim(),
        label: $('event-label').value,
        on: $('event-on').value,
    };
    const args = {};
    document.querySelectorAll('#event-args .ev-arg').forEach(sel => {
        if (sel.value) args[sel.dataset.field] = sel.value;
    });
    if (Object.keys(args).length) rule.if = args;
    const wt = document.querySelector('#event-when .ew-cond-type').value;
    const wp = document.querySelector('#event-when .ew-cond-param').value.trim();
    if (wt) rule.when = buildCond(wt, wp);
    const doList = [];
    document.querySelectorAll('#event-effects .npc-effect-row').forEach(row => {
        const eff = collectEffectFromRow(row);
        if (eff) doList.push(eff);
    });
    rule.do = doList;
    return rule;
}

async function saveEventRule() {
    const payload = collectEventPayload();
    const res = await fetch('/api/editor/event-rule', {
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

async function deleteEventRule() {
    const rule = (DATA.config.event_rules || []).find(r => r.id === selectedId);
    if (!rule) return;
    if (!confirm(`确认删除事件规则【${rule.label || selectedId}】？`)) return;
    const res = await fetch('/api/editor/event-rule/' + encodeURIComponent(selectedId),
        { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
    showEmpty();
    await loadData();
    toast(data.message, 'success');
}

// ---------- 游戏设置（开局配置） ----------
function openConfig() {
    const cfg = DATA.config || {};
    if (document.body.classList.contains('workbench-canvas')) setWorkbench(false);
    document.body.classList.add('config-open');
    selectedId = null;
    renderList();
    $('ed-empty').classList.add('hidden');
    document.querySelectorAll('.ed-form').forEach(f => f.classList.add('hidden'));
    $('form-config').classList.remove('hidden');

    $('config-title').value = cfg.game_title || '';
    $('config-intro').value = cfg.game_intro || '';
    const sceneSel = $('config-scene');
    sceneSel.innerHTML = refOptionsHtml(DATA.scenes, cfg.initial_scene, false);
    const box = $('config-items');
    box.innerHTML = '';
    const initial = cfg.initial_inventory || [];
    for (const iid of sortedKeys(DATA.items)) {
        const label = document.createElement('label');
        label.innerHTML =
            `<input type="checkbox" class="cfg-item-cb" data-id="${iid}"` +
            `${initial.includes(iid) ? ' checked' : ''}>` +
            `<span></span><span class="li-sub">${iid}</span>`;
        label.querySelector('span').textContent = DATA.items[iid].name || iid;
        box.appendChild(label);
    }
    $('config-gold').value = cfg.initial_gold != null ? cfg.initial_gold : 0;
    const p = cfg.player || {};
    $('config-hp').value = p.hp != null ? p.hp : 50;
    $('config-attack').value = p.attack != null ? p.attack : 5;
    $('config-defense').value = p.defense != null ? p.defense : 2;
}

async function saveConfig() {
    const payload = {
        game_title: $('config-title').value,
        game_intro: $('config-intro').value,
        initial_scene: $('config-scene').value,
        initial_inventory: [...document.querySelectorAll('.cfg-item-cb:checked')]
            .map(cb => cb.dataset.id),
        initial_gold: parseInt($('config-gold').value, 10),
        player: {
            hp: parseInt($('config-hp').value, 10),
            attack: parseInt($('config-attack').value, 10),
            defense: parseInt($('config-defense').value, 10),
        },
    };
    const res = await fetch('/api/editor/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.success) { toast(data.message, 'error'); return; }
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
