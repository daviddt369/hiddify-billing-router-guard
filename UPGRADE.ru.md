# Руководство по обновлению

Это руководство описывает обновление Hiddify Commercial Addon Stack после обновления базового HiddifyPanel.

> **Предупреждение:** Перед обновлением обязательно сделайте снапшот сервера или ручную резервную копию.
> Скрипты обновления автоматически создают резервные копии файлов, но полный снапшот — наиболее надёжный вариант восстановления.

---

## Когда использовать это руководство

Используйте это руководство, если:
- Вы обновили HiddifyPanel до новой версии и нужно переприменить слой аддонов.
- Вы получили новую версию этого репозитория и хотите задеплоить обновлённые файлы аддонов.

**Не используйте** этот путь для перехода между мажорными версиями HiddifyPanel. Стек аддонов привязан к **HiddifyPanel 12.0.0**.

---

## Чеклист перед обновлением

1. Сделайте снапшот сервера (рекомендуется).
2. Убедитесь, что сервисы работают:

   ```bash
   systemctl is-active hiddify-panel hiddify-panel-background-tasks
   ```

3. Получите последнюю версию кода аддонов:

   ```bash
   cd /root/hiddify-billing-router-guard
   git pull
   ```

4. Запустите dry-run, чтобы увидеть что изменится без применения:

   ```bash
   sudo bash release/upgrade-installer/upgrade-business-layer.sh --dry-run
   ```

---

## Запуск обновления

```bash
sudo bash release/upgrade-installer/upgrade-business-layer.sh
```

Скрипт:
- Создаёт резервные копии существующих файлов аддона перед заменой
- Копирует обновлённые файлы business-слоя в runtime панели
- Переприменяет патчи routing и antishare на общие файлы (`admin/__init__.py`, `admin-layout.html`, `business-settings.html`)
- Запускает скрипт миграции БД (идемпотентный — безопасно запускать на уже мигрированных базах)
- Перезапускает сервисы панели
- Запускает все smoke-тесты

---

## Smoke-тесты после обновления

Запустите после обновления для проверки работоспособности:

```bash
sudo bash release/business-installer/smoke-business.sh
sudo bash release/routing-installer/smoke-routing.sh
sudo bash release/antishare-installer/smoke-antishare.sh
```

---

## Обновление routing и antishare (при необходимости)

Скрипт `upgrade-business-layer.sh` обновляет только business-аддон. Если файлы routing или antishare тоже изменились, примените их по порядку:

```bash
# Переустановка routing
sudo bash release/routing-installer/install-routing.sh

# Переустановка antishare
sudo bash release/antishare-installer/install-antishare.sh
```

Оба установщика идемпотентны и создают резервные копии перед заменой файлов.

---

## Полное обновление стека (экспериментально)

Доступен объединённый скрипт для обновления всех трёх аддонов сразу:

```bash
sudo bash release/upgrade-installer/upgrade-existing-stack.sh
```

Сначала запустите dry-run и внимательно изучите вывод перед применением.

---

## Откат после неудачного обновления

При сбое обновления скрипт откатывается автоматически. Для ручного отката:

```bash
sudo bash release/rollback-all.sh
```

Или по отдельности в обратном порядке:

```bash
sudo bash release/antishare-installer/rollback-antishare.sh
sudo bash release/routing-installer/rollback-routing.sh
sudo bash release/business-installer/rollback-business.sh
```

Каждый скрипт отката восстанавливает файлы из последней резервной копии, созданной во время обновления.

---

## Ожидаемые предупреждения после обновления

- **"Telegram bot token is not configured"** — ожидается при первом перезапуске панели, если токен не перенастроен. Проверьте интерфейс администратора.
- **Предупреждения routing health probe** — ожидаются до подтверждения доступности upstream-ноды после обновления.
