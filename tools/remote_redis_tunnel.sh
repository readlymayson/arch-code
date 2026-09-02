#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# remote_redis_tunnel.sh — SSH-туннель Redis для Windows-воркера.
#
# ПОСЛЕ ПЕРЕНОСА arch-code на Windows (2026-09) VPS остаётся хостом
# Redis. Redis слушает ТОЛЬКО на 127.0.0.1:6379 (безопасно — без
# bind 0.0.0.0 и requirepass не нужен firewall). Windows-воркер
# подключается через SSH-туннель:
#
#   Windows: ssh -N -L 6379:127.0.0.1:6379 dev@217.12.38.121
#
# Этот скрипт (на VPS) — вспомогательный: поднимает автоподдерживаемый
# туннель (systemd) ИЛИ просто проверяет доступность Redis.
#
# Использование:
#   ./tools/remote_redis_tunnel.sh check   — проверить Redis (PONG?)
#   ./tools/remote_redis_tunnel.sh install — установить systemd-юнит
#                                             remote-redis-tunnel (для
#                                             обратного туннеля, если нужно)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"

check() {
    echo "==> Redis: ${REDIS_HOST}:${REDIS_PORT}"
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>&1; then
        echo "✅ Redis доступен локально."
        echo ""
        echo "Для Windows-воркера (на Windows-хосте):"
        echo "  ssh -N -L 6379:127.0.0.1:6379 dev@217.12.38.121"
        echo "Затем REDIS_URL=redis://127.0.0.1:6379/0 (db 0 — личный инстанс)"
        echo "или REDIS_URL=redis://127.0.0.1:6379/1 (db 1 — публичный)"
    else
        echo "❌ Redis недоступен."
        exit 1
    fi
}

install() {
    echo "==> Установка systemd-юнита remote-redis-tunnel (необязательно)."
    echo "    Обычно туннель поднимается НА Windows-стороне (ssh -N -L)."
    echo "    Этот юнит нужен только для обратного туннеля (нестандартно)."
    echo "    Пропущено."
}

case "${1:-check}" in
    check)   check ;;
    install) install ;;
    *) echo "Использование: $0 [check|install]"; exit 1 ;;
esac
