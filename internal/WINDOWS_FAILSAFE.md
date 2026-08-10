# Реализация обхода белых списков на Windows 11: fail-safe

Этот документ фиксирует подтверждённый контекст Windows-клиента FreeTurn/WireGuard. Он не содержит ссылок VK, токенов CAPTCHA, ключей WireGuard, `obf-key` или приватной конфигурации.

## Подтверждённая схема

```text
Windows 11
  └─ WireGuard Tunnel: BatyaVPN-Laptop
       └─ Endpoint 127.0.0.1:9000
            └─ C:\FreeTurn\client.exe
                 └─ VK TURN / WebRTC → free-turn-proxy → VPS
```

Локальный FreeTurn запускается не службой и не Scheduled Task, а интерактивным `C:\FreeTurn\Start-BatyaVPN.ps1`. Он запрашивает актуальную ссылку звонка VK и может потребовать ручную CAPTCHA.

## Исходный инцидент

Обычный интернет на ноутбуке перестал работать при исправном Wi-Fi. Причиной оказался оставшийся активным системный сервис:

```text
WireGuard Tunnel: BatyaVPN-Laptop
```

Туннель применил маршруты `AllowedIPs`, но локальный транспорт FreeTurn уже не обеспечивал рабочую доставку. После остановки tunnel service обычный HTTPS-доступ немедленно восстановился.

Второй обнаруженный race condition: `client.exe` открывает `127.0.0.1:9000` до завершения VK CAPTCHA. Проверка только наличия UDP-listener преждевременно включала WireGuard. Запрос CAPTCHA затем пытался разрешить DNS с клиентского адреса WireGuard и получал timeout, потому что сам транспорт ещё не был авторизован.

## Инварианты безопасности

1. Обычное состояние — tunnel service отсутствует либо `Stopped + Disabled`.
2. `WireGuard Manager` может оставаться запущенным автоматически; это не активный tunnel service.
3. FreeTurn всегда запускается раньше WireGuard.
4. Наличие UDP/9000 доказывает только запуск процесса, но не завершение CAPTCHA.
5. WireGuard нельзя запускать до явного пользовательского `READY` после успешной CAPTCHA.
6. Непосредственно перед стартом WireGuard повторно проверяются обычный HTTPS, DNS `id.vk.ru`, живой процесс FreeTurn и принадлежность UDP/9000 именно `C:\FreeTurn\client.exe`.
7. Активная tunnel service имеет `Manual`, а OFF всегда возвращает её в `Disabled`.
8. Любая неожиданная ошибка вызывает `off.ps1`.
9. Независимый auto-off ставится до запуска транспорта и заново отсчитывается после успешного handshake.
10. Скрипты не удаляют профиль WireGuard и не меняют его конфигурацию.

## Состояния

```text
OFF
  tunnel: Stopped + Disabled (или отсутствует)
  FreeTurn: stopped
  auto-off: отсутствует
  обычный интернет: работает

STARTING_FREETURN
  tunnel: Stopped + Disabled
  FreeTurn: интерактивная ссылка + CAPTCHA
  auto-off: уже вооружён

WAITING_READY
  UDP/9000: принадлежит client.exe
  tunnel: всё ещё Stopped + Disabled
  пользователь подтверждает завершённую CAPTCHA словом READY

VERIFYING_TUNNEL
  tunnel: Manual + Running
  проверяется свежий WireGuard handshake и HTTPS

ON
  tunnel: Manual + Running
  FreeTurn: running
  auto-off: установлен на полный выбранный интервал

любая ошибка → OFF
```

## Реализация

| Файл | Роль |
|---|---|
| [`../windows-whitelist-bypass/on.ps1`](../windows-whitelist-bypass/on.ps1) | Двухэтапный запуск, проверки, handshake и auto-off |
| [`../windows-whitelist-bypass/off.ps1`](../windows-whitelist-bypass/off.ps1) | Идемпотентный fail-safe OFF |
| [`../windows-whitelist-bypass/install.ps1`](../windows-whitelist-bypass/install.ps1) | Проверка зависимостей, установка скриптов и ярлыков |
| [`../windows-whitelist-bypass/README.md`](../windows-whitelist-bypass/README.md) | Пользовательская инструкция подключения |

Развёрнутые файлы:

- `C:\BatyaVPN\on.ps1`;
- `C:\BatyaVPN\off.ps1`;
- `C:\BatyaVPN\batyavpn.log`;
- ярлыки `BatyaVPN ON` и `BatyaVPN OFF` на рабочем столе;
- динамическая задача `BatyaVPN Auto-Off` от `SYSTEM` с `RunLevel Highest`.

## Проверки ON

1. Все обязательные файлы существуют.
2. OFF baseline успешно достигнут.
3. Независимый auto-off зарегистрирован до дальнейших действий.
4. FreeTurn запущен через существующий интерактивный скрипт.
5. UDP/9000 принадлежит точному пути `C:\FreeTurn\client.exe`.
6. При выключенном туннеле работают HTTPS и DNS VK.
7. Пользователь подтвердил успешную CAPTCHA словом `READY`.
8. FreeTurn и обычный интернет повторно проверены.
9. Служба временно переведена в `Manual` и запущена.
10. `wg.exe` сообщает свежий handshake.
11. HTTPS работает через активный BatyaVPN.
12. Auto-off перенесён на полный интервал от момента успеха.

## Проверки OFF

OFF останавливает только конкретную tunnel service и точный `C:\FreeTurn\client.exe`. PID оболочки проверяется одновременно по PID, имени `powershell.exe` и командной строке `C:\FreeTurn\Start-BatyaVPN.ps1`, что защищает от случайного завершения процесса после повторного использования PID.

Конечный критерий:

```text
tunnel service отсутствует
или
Status = Stopped AND StartType = Disabled
```

## Диагностика

Журнал: `C:\BatyaVPN\batyavpn.log`.

Если CAPTCHA сообщает DNS timeout, а источник запроса относится к адресу WireGuard, туннель был активирован до готовности FreeTurn. Выполнить OFF, получить новую ссылку VK и повторить двухэтапный запуск. Не ослаблять правило `READY` и не считать один UDP-listener достаточной readiness-пробой.

Если handshake не появился, fail-safe OFF является правильным результатом. Проверять отдельно актуальность ссылки, CAPTCHA, работу FreeTurn и серверный `free-turn-proxy`; не оставлять WireGuard активным для продолжительной диагностики.

## Откат клиентского контроллера

Откат не требует удаления WireGuard или его профиля:

1. выполнить `C:\BatyaVPN\off.ps1` от администратора;
2. удалить только ярлыки `BatyaVPN ON/OFF` и каталог `C:\BatyaVPN`, если контроллер больше не нужен;
3. оставить `C:\FreeTurn` и сохранённый WireGuard-профиль нетронутыми.
