# BatyaVPN

Репозиторий трёх независимых сетевых контуров на одном VPS. Их нельзя объединять в одну панель, переносить одним изменением или диагностировать как одну систему: у них разные транспорты, риски и критерии работоспособности.

## Структура репозитория

| Класс файлов | Для кого и зачем | Расположение |
|---|---|---|
| Панель обычного VPN | Исходный код HyBoard, deploy-скрипты, тесты и технический README компонента | [`panel/`](panel/) |
| Внутренняя документация | Контекст для Codex/исполнителя: baseline, диагностика, эксплуатация, ограничения и disaster recovery | [`internal/`](internal/) |
| Методичка по белым спискам | Тексты для человека, который хочет понять реализованную схему FreeTurn/VK TURN + WireGuard | [`methodology/`](methodology/) |
| Обход белых списков на Windows 11 | Рабочая fail-safe реализация FreeTurn/WireGuard: ON/OFF, установщик и инструкция подключения | [`windows-whitelist-bypass/`](windows-whitelist-bypass/) |

```text
BatyaVPN/
├── panel/        # код и документация панели обычного VPN
├── methodology/  # методичка против режима белых списков
├── windows-whitelist-bypass/  # реализация обхода белых списков на Windows 11
└── internal/     # технический контекст для Codex
```

Внутренние документы не содержат реальных секретов и не являются пользовательскими руководствами. Методичка в `methodology/` объясняет принципы и не содержит живых конфигураций сервера.

| Контур | Роль | Текущий статус | Правило изменений |
|---|---|---|---|
| Hysteria2 | Основной повседневный VPN | Рабочий | Приоритетный контур; изменять с baseline и откатом |
| VLESS + Reality | Резервный прямой транспорт через Xray/3x-ui | Вторичный/экспериментальный | Не затрагивать в задачах Hysteria2 |
| Белые списки | Аварийный VPN через FreeTurn/VK TURN + WireGuard | Рабочий; реализация для Windows 11 внедрена | Не затрагивать без отдельной задачи |

## 1. Обычный VPN: нативный Hysteria2

```text
клиент ── QUIC / UDP 443 ──> hysteria-server ──> интернет
```

Это основной быстрый VPN-контур. Он работает нативно; HyBoard управляет только персональными доступами и не участвует в передаче трафика.

| Компонент | Назначение |
|---|---|
| Hysteria2 | Data plane, UDP/443 |
| `hy-access` | Реестр и жизненный цикл персональных credential ID |
| HyBoard | Выдача, ротация и отзыв доступов; мониторинг и журнал |
| Nginx + mTLS | Отдельный management-plane панели на TCP/8443 |

Весь компонент находится в [`panel/`](panel/): приложение — `panel/hyboard/`, установка — `panel/deploy/`, тесты — `panel/tests/`.

Технические документы:

- [`panel/README.md`](panel/README.md) — состав и установка панели;
- [`internal/HYBOARD_MONITORING.md`](internal/HYBOARD_MONITORING.md) — статистика трафика, alerts и probes;
- [`internal/HYBOARD_REMOTE_ACCESS.md`](internal/HYBOARD_REMOTE_ACCESS.md) — HTTPS/mTLS management-plane;
- [`internal/SERVER_MIGRATION.md`](internal/SERVER_MIGRATION.md) — encrypted recovery bundle и перенос на чистый VPS;
- [`internal/INTERNAL_CONTEXT_HYSTERIA2_HYBOARD.md`](internal/INTERNAL_CONTEXT_HYSTERIA2_HYBOARD.md) — фактический baseline и ограничения.

Инварианты: HyBoard не меняет транспорт Hysteria2, UDP/443, Xray, WireGuard, FreeTurn, firewall или маршрутизацию. Остановка панели не должна прерывать уже выданные Hysteria2-подключения.

## 2. Резервный транспорт: VLESS + Reality

```text
клиент ── VLESS / TCP + Reality ──> Xray / 3x-ui ──> интернет
```

Это отдельный прямой VPN-контур через Xray и 3x-ui. Он не является реализацией Hysteria2 внутри панели и не обслуживается HyBoard. Его состояние и гипотезы диагностики зафиксированы только во внутреннем контексте:

- [`internal/INTERNAL_CONTEXT_XRAY_VLESS_REALITY.md`](internal/INTERNAL_CONTEXT_XRAY_VLESS_REALITY.md)

Изменения VLESS/Reality требуют собственного backup, отдельного тестового профиля и проверки реальным клиентом. Не использовать этот контур как повод менять рабочий Hysteria2.

## 3. Аварийный контур: белые списки

```text
клиент ── WireGuard ──> локальный FreeTurn ──> VK TURN / WebRTC
       ──> free-turn-proxy на VPS ──> WireGuard ──> интернет
```

Это не быстрый обычный VPN, а аварийный маршрут для режима строгих мобильных белых списков. Его назначение — сохранить базовый IP-доступ там, где прямой зарубежный VPN недоступен.

### Реализация обхода белых списков на Windows 11

Для ноутбука реализован отдельный fail-safe контроллер [`windows-whitelist-bypass/`](windows-whitelist-bypass/). В обычном состоянии `WireGuard Tunnel: BatyaVPN-Laptop` остановлен и имеет `Disabled`; FreeTurn запускается первым, а WireGuard — только после завершённой VK CAPTCHA и явного подтверждения `READY`. После старта проверяются свежий handshake и HTTPS, а независимая задача Планировщика выполняет автоматический OFF. Любая ошибка также возвращает туннель в безопасное состояние.

Пошаговая пользовательская инструкция: **[`windows-whitelist-bypass/README.md`](windows-whitelist-bypass/README.md)**. Подтверждённый технический контекст и причины fail-safe порядка: [`internal/WINDOWS_FAILSAFE.md`](internal/WINDOWS_FAILSAFE.md).

### Методичка по белым спискам

Начальная точка: **[методичка по обходу мобильных белых списков](methodology/README.md)**.

| Раздел | Содержание |
|---|---|
| [01](methodology/01-whitelists.md) | Модель мобильных белых списков |
| [02](methodology/02-traffic-flow.md) | Полный маршрут пакетов |
| [03](methodology/03-wireguard.md) | WireGuard в этой архитектуре |
| [04](methodology/04-turn-webrtc.md) | TURN, WebRTC, rtpopus |
| [05](methodology/05-components.md) | Роли компонентов |
| [06](methodology/06-limitations.md) | Ограничения и масштабирование |
| [07](methodology/07-security.md) | Безопасность и приватность |
| [08](methodology/08-troubleshooting.md) | Диагностика |
| [09](methodology/09-devices.md) | Устройства и клиентские сценарии |

Фактический baseline этого контура: [`internal/PROJECT_CONTEXT.md`](internal/PROJECT_CONTEXT.md). Не менять `wg0`, peers, FreeTurn, TURN-параметры или NAT в задачах про Hysteria2/HyBoard.

## Операционные правила

- Не коммитить реальные IP/домены, credentials, URI/QR, `.p12`, SSH-ключи, WireGuard keys, токены, сертификаты, дампы или `.env`.
- GitHub хранит код и контекст, не backup сервера. Recovery bundle создаётся только зашифрованным и хранится вне Git.
- Перед любым серверным изменением: read-only baseline → backup/rollback → одно изменение → реальный клиентский тест.
- При потере или возможной компрометации VPS не переносить секреты вслепую: ротировать access credentials, obfuscation/session/stats secrets, пароль панели и client CA.

## Локальная проверка панели

```bash
cd panel
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest
ruff check .
```

Демо без доступа к серверу:

```bash
HYBOARD_DEMO=1 hyboard
```

Панель демо доступна только на `http://127.0.0.1:28474`; пароль: `demo`.
