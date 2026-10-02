/* ==========================================================================
   main.js —— 应用程序入口
   加载顺序：最后加载（依赖 constants/core/game/ui 已就绪）
   职责：启动时校验登录态。注意：登录态仅作为「点在线模式时要不要弹登录面板」的参考，
        绝不自动进入任何模式 —— 刷新后模式选择层始终保持显示。
   ========================================================================== */

async function initApp() {
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
