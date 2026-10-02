#!/bin/bash
# ================================================
# 一键部署脚本 —— Ubuntu 24.04 LTS
# 用法：在服务器上（以 root 或 sudo 执行）运行 ./deploy/install.sh
# 前提：项目代码已经在 /opt/textgame 目录下
# ================================================
set -e

PROJECT_DIR="/opt/textgame"
echo "============================================"
echo "🎮 文字游戏引擎 —— 生产部署脚本"
echo "📁 项目目录: $PROJECT_DIR"
echo "============================================"

# ---------- Step 1: 系统更新 + 安装依赖 ----------
echo ""
echo "【1/6】安装系统依赖（python3, pip, nginx）..."
apt update -qq
apt install -y python3 python3-pip nginx > /dev/null
echo "✅ python3 + nginx 安装完成"

# ---------- Step 2: Python 依赖 ----------
echo ""
echo "【2/6】安装 Python 依赖（flask, gunicorn）..."
cd "$PROJECT_DIR"
pip3 install -r requirements.txt --break-system-packages --quiet
echo "✅ pip install 完成"

# ---------- Step 3: 目录权限 ----------
echo ""
echo "【3/6】设置目录权限（www-data 读写 game_data）..."
chown -R www-data:www-data "$PROJECT_DIR"
chmod -R 755 "$PROJECT_DIR"
# SQLite 数据库文件需要 www-data 可写
chmod -R 775 "$PROJECT_DIR/game_data"
# logs 目录
mkdir -p "$PROJECT_DIR/logs"
chown www-data:www-data "$PROJECT_DIR/logs"
echo "✅ 权限设置完成"

# ---------- Step 4: systemd service ----------
echo ""
echo "【4/6】安装 systemd service..."
cp "$PROJECT_DIR/deploy/textgame.service" /etc/systemd/system/textgame.service
# 把 SECRET_KEY 替换成强随机值（首次部署时自动生成）
if grep -q "CHANGE_ME_TO_A_STRONG_RANDOM_SECRET_KEY" /etc/systemd/system/textgame.service; then
    NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/CHANGE_ME_TO_A_STRONG_RANDOM_SECRET_KEY/$NEW_KEY/" /etc/systemd/system/textgame.service
    echo "✅ 已自动生成 SECRET_KEY"
fi
systemctl daemon-reload
systemctl enable textgame
echo "✅ systemd service 已安装并启用（开机自启）"

# ---------- Step 5: Nginx 配置 ----------
echo ""
echo "【5/6】配置 Nginx..."
cp "$PROJECT_DIR/deploy/textgame.nginx.conf" /etc/nginx/sites-available/textgame
rm -f /etc/nginx/sites-enabled/default           # 禁用默认站点
ln -sf /etc/nginx/sites-available/textgame /etc/nginx/sites-enabled/textgame
nginx -t  # 配置语法检查
echo "✅ Nginx 配置完成"

# ---------- Step 6: 启动服务 ----------
echo ""
echo "【6/6】启动 Gunicorn + Nginx..."
systemctl restart textgame
systemctl restart nginx
sleep 1  # 等 gunicorn 起来

# ---------- 验证 ----------
echo ""
echo "============================================"
echo "📊 服务状态检查"
echo "============================================"
echo -n "Gunicorn: "; systemctl is-active textgame
echo -n "Nginx:   "; systemctl is-active nginx
echo ""

# 测试 API
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/auth/me || echo "000")
echo "Gunicorn API 测试: http://127.0.0.1:8000/api/auth/me → HTTP $HTTP_CODE"
HTTP_CODE2=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/api/auth/me || echo "000")
echo "Nginx 反代测试:    http://127.0.0.1/api/auth/me → HTTP $HTTP_CODE2"

echo ""
echo "============================================"
echo "🎉 部署完成！"
echo ""
echo "🌐 访问地址：http://www.bykc.click"
echo "📋 常用命令："
echo "  查看 Gunicorn 日志: journalctl -u textgame -f"
echo "  重启 Gunicorn:      systemctl restart textgame"
echo "  重载 Nginx:         systemctl reload nginx"
echo "  停止服务:           systemctl stop textgame && systemctl stop nginx"
echo "============================================"
