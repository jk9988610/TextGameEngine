/* ==========================================================================
   game.js —— 游戏主循环：状态拉取与渲染、NPC 对话、战斗
   加载顺序：core.js 之后、ui.js 之前
   ========================================================================== */

// ---------- 游戏后端交互 ----------

// 场景切换历史：用于把"来时的那条出口"标成「← 返回」
let _prevSceneId = null;       // 上一个场景（回头路的终点）
let _lastSeenSceneId = null;   // 上一次拿到的场景，用于判断是否真的换了场景
function resetSceneHistory() {
    /** 开新档 / 读档 / 切换模式时清空场景历史 */
    _prevSceneId = null;
    _lastSeenSceneId = null;
}

async function fetchState() {
    /** 从后端拉取当前游戏状态并渲染 */
    const res = await fetch('/api/state');
    const data = await res.json();

    // 只有真的换了场景才记录"来处"（同一场景内拿物品等操作不算）
    const currentId = data.scene.id;
    if (_lastSeenSceneId && currentId !== _lastSeenSceneId) {
        _prevSceneId = _lastSeenSceneId;
    }
    _lastSeenSceneId = currentId;

    // 渲染场景：名字进卡片标题，时间进底部状态栏
    document.getElementById('scene-name').textContent = data.scene.name;
    document.getElementById('scene-desc').textContent = data.scene.description;
    document.getElementById('status-scene').textContent = data.scene.name;
    document.getElementById('status-time').textContent = formatGameTime(data.game_time);
    document.getElementById('status-gold').textContent = `金币：${data.player_gold || 0}`;

    // 渲染可拿取物品 —— 空了就整块隐藏
    const itemsTitle = document.querySelector('.section-title:has(~ #items-list)');
    const itemsList = document.getElementById('items-list');
    if (data.scene.items_here.length === 0) {
        itemsList.parentElement.style.display = 'none';
    } else {
        itemsList.parentElement.style.display = '';
        itemsList.innerHTML = '';
        data.scene.items_here.forEach(item => {
            const btn = document.createElement('button');
            btn.className = 'action-btn';
            btn.textContent = `拿取：${item.name}`;
            btn.onclick = () => doAction('take_item', item.id);
            itemsList.appendChild(btn);
        });
    }

    // 渲染商店货物 —— 空了整块隐藏（无限供应，价格后端校验）
    const shopSection = document.getElementById('shop-section');
    const shopList = document.getElementById('shop-list');
    const shopGoods = data.scene.shop_items || [];
    if (shopGoods.length === 0) {
        shopSection.style.display = 'none';
    } else {
        shopSection.style.display = '';
        shopList.innerHTML = '';
        shopGoods.forEach(goods => {
            const btn = document.createElement('button');
            btn.className = 'action-btn shop';
            const affordable = (data.player_gold || 0) >= goods.price;
            btn.textContent = `购买：${goods.name}（${goods.price} 金币）`;
            btn.style.opacity = affordable ? '' : '0.55';
            btn.onclick = () => doAction('buy_item', goods.id);
            shopList.appendChild(btn);
        });
    }

    // 渲染可走出口 —— 区分"来时的路（返回）"和"未探索的路（前往）"
    const exitsList = document.getElementById('exits-list');
    if (data.scene.exits.length === 0) {
        exitsList.parentElement.style.display = 'none';
    } else {
        exitsList.parentElement.style.display = '';
        exitsList.innerHTML = '';
        data.scene.exits.forEach(exit => {
            const btn = document.createElement('button');
            const targetName = exit.name || SCENE_NAMES[exit.id] || exit.id;
            if (exit.locked) {
                btn.className = 'action-btn locked';
                btn.textContent = `${targetName}（锁定）`;
                btn.disabled = true;
            } else if (exit.id === _prevSceneId) {
                btn.className = 'action-btn back';
                btn.textContent = `← 返回：${targetName}`;
                btn.onclick = () => doAction('move_scene', exit.id);
            } else {
                btn.className = 'action-btn forward';
                btn.textContent = `→ 前往：${targetName}`;
                btn.onclick = () => doAction('move_scene', exit.id);
            }
            exitsList.appendChild(btn);
        });
    }

    // 渲染 NPC 按钮（空了隐藏整块）
    const npcSection = document.getElementById('npc-section');
    const npcList = document.getElementById('npc-list');
    if (data.npcs_here && data.npcs_here.length > 0) {
        npcSection.style.display = '';
        npcList.innerHTML = '';
        data.npcs_here.forEach(npc => {
            const btn = document.createElement('button');
            btn.className = 'action-btn';
            btn.textContent = `和 ${npc.name} 说话`;
            btn.onclick = () => startNpcDialogue(npc.id);
            npcList.appendChild(btn);
        });
    } else {
        npcSection.style.display = 'none';
    }

    // 渲染敌人按钮（空了隐藏整块）
    const enemySection = document.getElementById('enemy-section');
    const enemyList = document.getElementById('enemy-list');
    if (data.enemies_here && data.enemies_here.length > 0) {
        enemySection.style.display = '';
        enemyList.innerHTML = '';
        data.enemies_here.forEach(enemy => {
            const btn = document.createElement('button');
            btn.className = 'action-btn';
            btn.style.borderColor = 'var(--danger)';
            btn.textContent = `挑战 ${enemy.name}`;
            btn.onclick = () => startEnemyCombat(enemy.id);
            enemyList.appendChild(btn);
        });
    } else {
        enemySection.style.display = 'none';
    }

    // 渲染玩家背包（同时决定攻击按钮是否可用：背包里有武器才能打）
    const invList = document.getElementById('player-inventory');
    invList.innerHTML = '';
    let hasWeapon = false;
    if (data.player_inventory.length === 0) {
        invList.innerHTML = '<span class="empty">（背包是空的）</span>';
    } else {
        data.player_inventory.forEach(item => {
            const div = document.createElement('div');
            div.className = 'item';
            const nameSpan = document.createElement('span');
            nameSpan.className = 'item-name';
            nameSpan.textContent = `• ${item.name}`;
            div.appendChild(nameSpan);
            if (item.usable) {
                const useBtn = document.createElement('button');
                useBtn.className = 'item-use-btn';
                useBtn.textContent = '使用';
                useBtn.onclick = () => doAction('use_item', item.id);
                div.appendChild(useBtn);
            }
            invList.appendChild(div);
            if (item.is_weapon) hasWeapon = true;
        });
    }
    attackBtn.disabled = !hasWeapon;

    // 同时拉取对话状态并渲染
    await fetchDialogue();
    // 同时拉取战斗状态并渲染
    await fetchCombat();
}

// 游戏时间格式化辅助
function formatGameTime(seconds) {
    seconds = seconds || 0;
    const days = Math.floor(seconds / 86400) + 1;
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `第${days}天 ${String(hours).padStart(2,'0')}:${String(mins).padStart(2,'0')}`;
}

// ---------- NPC 对话逻辑 ----------
const dialoguePanel = document.getElementById('dialogue-panel');
const dialogueCloseBtn = document.getElementById('dialogue-close');
let _dialogueActive = false;   // 跟踪弹窗激活态，用于"仅重开时回中"

// 玩家点"和 XX 说话"按钮 → 主动启动对话
async function startNpcDialogue(npcId) {
    const res = await fetch('/api/dialogue/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ npc_id: npcId })
    });
    const data = await res.json();
    if (data.success) {
        await fetchState();  // 刷新对话状态 → 会自动弹出 modal
    } else {
        showActionMsg(data.message);
    }
}

// 跳过对话按钮（隐藏 modal）
dialogueCloseBtn.addEventListener('click', async () => {
    dialoguePanel.classList.remove('active');
});

async function fetchDialogue() {
    /** 从后端拉取对话状态并渲染 */
    const res = await fetch('/api/dialogue');
    const data = await res.json();
    renderDialogue(data);
}

function renderDialogue(d) {
    /** 渲染对话面板（仅在 无→有 的切换瞬间回中，避免拖动后被轮询拉回） */
    if (!d || !d.active) {
        dialoguePanel.classList.remove('active');
        _dialogueActive = false;
        return;
    }
    if (!_dialogueActive) resetModalPosition(dialoguePanel);
    _dialogueActive = true;
    dialoguePanel.classList.add('active');
    document.getElementById('dialogue-name').textContent = d.npc_name || 'NPC';
    document.getElementById('dialogue-text').textContent = d.text || '...';

    const choicesDiv = document.getElementById('dialogue-choices');
    choicesDiv.innerHTML = '';
    if (!d.choices || d.choices.length === 0) {
        // 没有选项 → 对话即将结束，显示"继续"按钮
        const btn = document.createElement('button');
        btn.className = 'dialogue-choice-btn';
        btn.textContent = '继续...';
        btn.onclick = async () => {
            // 选 -1 表示结束对话（后端如果 choices 为空，前端直接隐藏）
            dialoguePanel.classList.remove('active');
        };
        choicesDiv.appendChild(btn);
    } else {
        d.choices.forEach((choice, i) => {
            const btn = document.createElement('button');
            btn.className = 'dialogue-choice-btn';
            btn.textContent = choice.text;
            btn.onclick = () => selectDialogueChoice(i);
            choicesDiv.appendChild(btn);
        });
    }
}

async function selectDialogueChoice(choiceIndex) {
    /** 发送对话选项到后端 */
    const res = await fetch('/api/dialogue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ choice_index: choiceIndex })
    });
    const data = await res.json();
    // 显示操作结果
    showActionMsg(data.message || '');
    // 刷新游戏状态（场景/背包可能因为对话变化）+ 刷新对话
    await fetchState();
}

// ---------- 战斗逻辑 ----------
const combatPanel = document.getElementById('combat-panel');
const combatLog = document.getElementById('combat-log');
const attackBtn = document.getElementById('attack-btn');
let _combatActive = false;     // 跟踪弹窗激活态，用于"仅重开时回中"

attackBtn.addEventListener('click', () => attackEnemy());

// 玩家点"挑战 XX"按钮 → 主动开始/继续战斗（保留敌人残血）
async function startEnemyCombat(enemyId) {
    const res = await fetch('/api/combat/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enemy_id: enemyId })
    });
    const data = await res.json();
    showActionMsg(data.message);
    if (data.success) {
        await fetchState();  // 刷新战斗状态 → 会自动弹出 modal
    }
}

async function fetchCombat() {
    /** 从后端拉取战斗状态并渲染 */
    const res = await fetch('/api/combat');
    const data = await res.json();
    renderCombat(data);
}

function renderCombat(d) {
    /** 渲染战斗面板（仅在 无→有 的切换瞬间回中，攻击轮询不拉回位置，残血战斗位置保持） */
    if (!d || !d.active) {
        combatPanel.classList.remove('active');
        _combatActive = false;
        return;
    }
    if (!_combatActive) resetModalPosition(combatPanel);
    _combatActive = true;
    combatPanel.classList.add('active');

    // 敌人
    const e = d.enemy;
    document.getElementById('enemy-name').textContent = e.name || '敌人';
    const enemyHpPct = Math.max(0, (e.hp / e.max_hp) * 100);
    document.getElementById('enemy-hp-bar').style.width = enemyHpPct + '%';
    document.getElementById('enemy-hp-text').textContent = `HP: ${e.hp}/${e.max_hp}  |  ATK: ${e.attack}  DEF: ${e.defense}`;

    // 玩家
    const p = d.player;
    const playerHpPct = Math.max(0, (p.hp / p.max_hp) * 100);
    document.getElementById('player-hp-bar').style.width = playerHpPct + '%';
    document.getElementById('player-hp-text').textContent = `HP: ${p.hp}/${p.max_hp}  |  ATK: ${p.attack}  |  DEF: ${p.defense}`;

    // 背包里的可使用消耗品（战斗中喝药：回血但消耗一回合，敌人会反击）
    const itemsBox = document.getElementById('combat-items');
    itemsBox.innerHTML = '';
    (d.usable_items || []).forEach(it => {
        const btn = document.createElement('button');
        btn.className = 'combat-item-btn';
        btn.textContent = `喝：${it.name}`;
        btn.addEventListener('click', () => useItemInCombat(it.id));
        itemsBox.appendChild(btn);
    });
}

function renderCombatLog(data) {
    /**把后端返回的战斗回合日志写进战斗面板（攻击/喝药共用） */
    combatLog.innerHTML = '';
    (data.log || []).forEach(line => {
        const div = document.createElement('div');
        if (line.includes('你用') || line.includes('你喝下')) div.className = 'log-player';
        else if (line.includes('反击') || line.includes('对你')) div.className = 'log-enemy';
        else div.className = 'log-system';
        div.textContent = line;
        combatLog.appendChild(div);
    });
    combatLog.scrollTop = combatLog.scrollHeight;
}

async function attackEnemy() {
    /** 玩家攻击敌人：武器由后端自动选择背包中第一把武器 */
    const res = await fetch('/api/combat/attack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    });
    const data = await res.json();

    renderCombatLog(data);

    // 显示结果
    showActionMsg(data.message || '');

    // 玩家死亡 → 面板会被移除（后端已处理回酒馆）
    // 敌人死亡 → 面板会被移除（后端已处理结束战斗）
    // 刷新所有状态
    await fetchState();
}

async function useItemInCombat(itemId) {
    /** 战斗中喝药：回血 + 敌人趁机反击一回合 */
    const res = await fetch('/api/combat/use-item', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_id: itemId })
    });
    const data = await res.json();
    if (!data.success && !data.player_dead) {
        // 满血/没有该物品等拦截：只提示，不刷日志
        showActionMsg(data.message || '无法使用');
        return;
    }
    renderCombatLog(data);
    showActionMsg(data.message || '');
    await fetchState();
}

// ---------- 通用点击操作 ----------
async function doAction(type, target) {
    /** 发送点击操作到后端 */
    const res = await fetch('/api/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, target })
    });
    const data = await res.json();
    // 显示后端返回的消息
    showActionMsg(data.message);
    // 刷新游戏状态
    fetchState();
}
