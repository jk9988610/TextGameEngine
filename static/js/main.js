/* ==========================================================================
   main.js —— 应用程序入口
   加载顺序：最后加载（依赖 constants/core/game/ui 已就绪）
   职责：启动时校验登录态。注意：登录态仅作为「点在线模式时要不要弹登录面板」的参考，
        绝不自动进入任何模式 —— 刷新后模式选择层始终保持显示。
   ========================================================================== */

async function loadGameInfo() {
    /**拉取游戏标题/简介（game_config.json 可配），填到模式层与顶栏；失败保持默认文案 */
    try {
        const res = await fetch('/api/game-info');
        const info = await res.json();
        if (info.title) {
            document.title = info.title;
            const modeTitle = document.getElementById('game-title-mode');
            const headerTitle = document.querySelector('header h1');
            if (modeTitle) modeTitle.textContent = info.title;
            if (headerTitle) headerTitle.textContent = info.title;
        }
        const introEl = document.getElementById('game-intro');
        if (introEl) {
            introEl.textContent = info.intro || '';
            introEl.style.display = info.intro ? '' : 'none';
        }
    } catch (e) { /* 后端没起来时保留 HTML 默认文案 */ }
}

async function initApp() {
    await loadGameInfo();
    try {
        const res = await fetch('/api/auth/me');
        const data = await res.json();
        if (data.logged_in && data.user) {
            _currentUser = data.user;  // 记着就行，点在线模式直接进
        }
    } catch (e) { /* 后端没起来也不崩 */ }
    // 不自动进入任何模式 —— 模式选择层保持显示，等玩家选
}

// 脚本置于 body 末尾：DOM 已就绪，DOMContentLoaded 即将触发
document.addEventListener('DOMContentLoaded', initApp);
