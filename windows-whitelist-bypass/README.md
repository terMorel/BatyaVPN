# Обход белых списков на Windows 11

Этот каталог содержит fail-safe управление клиентской цепочкой:

```text
приложения Windows → WireGuard → 127.0.0.1:9000 → FreeTurn → VK TURN → VPS
```

Система решает две практические проблемы:

- оставшийся активным `WireGuard Tunnel: BatyaVPN-Laptop` больше не должен лишать ноутбук обычного интернета;
- WireGuard не включается до завершения VK CAPTCHA, хотя FreeTurn открывает UDP/9000 раньше полной авторизации.

## Что устанавливается

| Артефакт | Назначение |
|---|---|
| `C:\BatyaVPN\on.ps1` | Двухэтапный безопасный запуск |
| `C:\BatyaVPN\off.ps1` | Немедленное выключение и возврат tunnel service в `Disabled` |
| `BatyaVPN ON` на рабочем столе | Включение с UAC и автоматическим OFF |
| `BatyaVPN OFF` на рабочем столе | Ручное аварийное выключение |
| `BatyaVPN Auto-Off` в Планировщике | Независимый таймер; существует только во время активного запуска |
| `C:\BatyaVPN\batyavpn.log` | Локальный журнал ON/OFF без приватной конфигурации |

Скрипты не экспортируют и не изменяют ключи, peer-конфигурацию или сохранённый профиль WireGuard.

## Предварительные условия

- Windows 11 и Windows PowerShell 5.1;
- WireGuard for Windows;
- сохранённый профиль `BatyaVPN-Laptop`;
- `C:\FreeTurn\client.exe`;
- `C:\FreeTurn\Start-BatyaVPN.ps1`;
- FreeTurn должен слушать `127.0.0.1:9000` после запуска.

## Установка

Откройте PowerShell в каталоге `windows-whitelist-bypass/` и выполните:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

По умолчанию auto-off равен 60 минутам. Другой интервал задаётся при установке:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Minutes 30
```

Установщик проверяет зависимости, копирует только ON/OFF, создаёт два ярлыка с запросом UAC и завершает работу в безопасном состоянии OFF.

## Подключение

1. Получите новую актуальную ссылку вида `https://vk.ru/call/join/...`.
2. Откройте ярлык **BatyaVPN ON** и подтвердите UAC.
3. Скрипт сначала выполняет OFF: tunnel service должен остаться `Stopped + Disabled`.
4. В отдельном окне FreeTurn вставьте ссылку VK и нажмите Enter.
5. Пройдите CAPTCHA. На этом этапе WireGuard включать вручную нельзя.
6. Дождитесь успешного завершения CAPTCHA и отсутствия новых ошибок `Captcha Proxy`.
7. Вернитесь в первое окно BatyaVPN ON, введите `READY` и нажмите Enter.
8. Скрипт повторно проверит обычный интернет и DNS VK, после чего временно переведёт tunnel service в `Manual` и запустит WireGuard.
9. При необходимости WireGuard откроется отдельно: активируйте только `BatyaVPN-Laptop`.
10. Используйте VPN только после зелёного сообщения `BatyaVPN is ON`.

После успешного запуска проверяются свежий WireGuard handshake и HTTPS-доступ. Независимый auto-off перезапускается на полный выбранный интервал.

## Отключение

Откройте ярлык **BatyaVPN OFF**. Скрипт:

1. останавливает только `WireGuard Tunnel: BatyaVPN-Laptop`;
2. возвращает службу в `Disabled`;
3. завершает только `C:\FreeTurn\client.exe` и созданное ON окно-обёртку;
4. удаляет незавершённый ручной auto-off;
5. проверяет конечное состояние `Stopped + Disabled`.

`WireGuard Manager` отключать не требуется: это управляющая служба, а не активный туннель.

## Если что-то пошло не так

- До успешной CAPTCHA не вводите `READY`.
- Для отмены введите `CANCEL`: будет вызван OFF.
- Любая ошибка handshake, DNS, HTTPS, службы или Планировщика вызывает fail-safe OFF.
- Если окно было закрыто во время запуска, заранее поставленный `BatyaVPN Auto-Off` всё равно выполнит OFF.
- Если браузер CAPTCHA показывает proxy error, нажмите OFF, получите новую ссылку VK и повторите процедуру. Не используйте истёкшую ссылку.

Технические причины и инварианты: [`../internal/WINDOWS_FAILSAFE.md`](../internal/WINDOWS_FAILSAFE.md).
