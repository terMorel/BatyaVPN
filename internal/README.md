# Внутренняя документация BatyaVPN

Только для Codex и исполнителей, продолжающих разработку или восстановление системы. Здесь фиксируются подтверждённое состояние, границы контуров, эксплуатационные команды и аварийные процедуры. Реальные секреты не коммитятся.

| Файл | Назначение |
|---|---|
| [`INTERNAL_CONTEXT_HYSTERIA2_HYBOARD.md`](INTERNAL_CONTEXT_HYSTERIA2_HYBOARD.md) | Нативный Hysteria2, HyBoard, baseline и инварианты |
| [`INTERNAL_CONTEXT_XRAY_VLESS_REALITY.md`](INTERNAL_CONTEXT_XRAY_VLESS_REALITY.md) | Xray/VLESS Reality и 3x-ui |
| [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) | FreeTurn/WireGuard-контур белых списков |
| [`WINDOWS_FAILSAFE.md`](WINDOWS_FAILSAFE.md) | Реализация обхода белых списков на Windows 11: FreeTurn → CAPTCHA → WireGuard, ON/OFF и auto-off |
| [`HYBOARD_MONITORING.md`](HYBOARD_MONITORING.md) | Метрики, alerts, Telegram и внешние probes |
| [`HYBOARD_REMOTE_ACCESS.md`](HYBOARD_REMOTE_ACCESS.md) | HTTPS/mTLS management-plane |
| [`SERVER_MIGRATION.md`](SERVER_MIGRATION.md) | Backup и перенос основного VPN-контура |

Код панели находится в [`../panel/`](../panel/). Публичная методичка — в [`../methodology/`](../methodology/).
