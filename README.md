# Chromium Observer

Локальный форк Chromium для наблюдения за своим ИИ-агентом: JPEG видимой области активной вкладки раз в секунду, обычный интерфейс браузера, сервер с последним кадром в памяти.

**Статус: разработка.** Сервер и просмотрщик проверены. Нативная Linux-сборка и проверки macOS ещё не завершены; готовых бинарных релизов пока нет.

Исходная версия и commit закреплены в `upstream.json`. Полный checkout загружается в `chromium/src`, изменения для воспроизводимости хранятся в `patches/observer.patch`, а читаемая копия нативного кода — в `native/`. Базовые исходники Chromium и артефакты сборки не включены в этот репозиторий. Уведомление о лицензии Chromium сохранено в `LICENSE.chromium`.

```bash
git clone https://github.com/MIkleRustRepin/chromium-observer.git
cd chromium-observer
```

## Сборка Linux

Нужны Python 3.10+, Git, зависимости Chromium и достаточно места на диске. Первичная загрузка и сборка занимают значительное время. Скрипт сборки запускает два задания и останавливает собственный процесс при остатке диска меньше 5 ГиБ; чужие файлы не удаляет.

```bash
python3 scripts/bootstrap.py
cd chromium/src
sudo ./build/install-build-deps.sh --no-prompt --no-arm --no-chromeos-fonts
export PATH="$PWD/../../tools/depot_tools:$PATH"
export DEPOT_TOOLS_UPDATE=0
gclient runhooks
cd ../..
python3 scripts/apply_patch.py
python3 scripts/build.py
python3 scripts/package.py
```

## Локальный запуск

В одном терминале:

```bash
python3 -m server.server
```

В другом:

```bash
chromium/src/out/Observer/chrome --user-data-dir="$PWD/artifacts/agent-profile"
```

Открыть **http://127.0.0.1:8765/** в отдельном обычном браузере. При локальном просмотре расположить окна рядом, чтобы наблюдаемая вкладка оставалась видимой. Использовать выделенный профиль наблюдаемого Chromium, чтобы запущенный системный браузер не перехватил запуск. Захват запускается автоматически без дополнительных переключателей.

Проверочная страница: `file:///абсолютный/путь/к/проекту/scripts/test-page.html`. Она показывает часы, анимацию и область для прокрутки.

## macOS Apple Silicon

Установить **полную Xcode** из Mac App Store и открыть её один раз, чтобы принять лицензию и установить компоненты. Затем на Mac Studio / Mini с Apple Silicon запустить:

```bash
./build-mac.sh
```

Скрипт сам скачает закреплённые Chromium, `depot_tools`, зависимости и Python для сборки, выполнит hooks, применит патч, соберёт `Chromium.app` для arm64 и создаст `artifacts/chromium-observer-macos-arm64.tar.gz`. Homebrew не нужен. Начальный запуск использует Python 3.9+ из Xcode command-line tools; дальнейшие этапы — закреплённый Python из `depot_tools`.

Можно скачать только консольный скрипт — он сам клонирует этот проект в `~/chromium-observer`:

```bash
curl -fL https://raw.githubusercontent.com/MIkleRustRepin/chromium-observer/main/build-mac.sh -o /tmp/chromium-observer-build-mac.sh
bash /tmp/chromium-observer-build-mac.sh
```

Нужны APFS и минимум 100 ГиБ свободного диска для первого запуска; рекомендуется 150 ГиБ и больше. Путь к проекту должен быть без пробелов. Число заданий выбирается по RAM/CPU; Mac не уходит в сон во время работы. Повторный запуск использует скачанные файлы и результаты компиляции. Логи сохраняются в `logs/build-mac-*.log`.

```bash
./build-mac.sh --jobs 4
./build-mac.sh --endpoint http://127.0.0.1:8765/api/frame
./build-mac.sh --clean  # пересоздать только артефакты out/Observer
```

Для другого каталога при запуске отдельного скачанного скрипта задать `CHROMIUM_OBSERVER_DIR`. При нестандартном расположении Xcode можно задать `DEVELOPER_DIR`. `--tests` дополнительно собирает browser tests; автоматически они не запускаются.

Запускать `chromium/src/out/Observer/Chromium.app/Contents/MacOS/Chromium` с отдельным `--user-data-dir`. Сервер: `tools/depot_tools/python-bin/python3 -m server.server`. Архив содержит локальную сборку; подпись Developer ID и notarization в задачу не входят. Фактическая сборка на macOS пока не проверена.

## Поведение и настройки

- Выбирается активная вкладка последнего активного открытого окна Chromium. Окна инкогнито исключены. Если выбранное окно свёрнуто или поверхность недоступна, новый кадр не отправляется.
- Передаётся viewport страницы в исходном разрешении, JPEG quality 80. Панели браузера, системные диалоги и курсор ОС в кадр не входят. Защищённое содержимое подчиняется ограничениям Chromium.
- Период — 1000 мс; тайм-аут захвата — 500 мс, отправки — 2 секунды. Выполняется только один цикл; при задержках кадры пропускаются без очереди.
- Сервер хранит только последний JPEG в RAM. После перезапуска история отсутствует. В просмотрщике кадр старше трёх секунд помечается как устаревший.
- Endpoint задаётся при сборке: `python3 scripts/build.py --endpoint http://127.0.0.1:8765/api/frame`. При другом локальном порте сервер запускать с `--port`.
- Сервер привязан к loopback. Для первого этапа удалённое размещение не настраивается.
- Обычный внешний вид не означает необнаружимость: изменения бинарника и сетевые соединения можно исследовать средствами ОС.

## Проверки

```bash
python3 -m unittest server.test_server -v
node scripts/test_viewer.cjs
```

Вторая команда требует установленный пакет `playwright` и Chrome; путь к бинарнику можно задать через `TEST_BROWSER`. Она проверяет сервер и просмотрщик, а не нативный захват.

Нативные проверки и фактический статус сборки описываются в `VALIDATION.md`.

API: `POST /api/frame` принимает `image/jpeg` и заголовки `X-Frame-Session`, `X-Frame-Sequence`, `X-Captured-At` (Unix milliseconds). `GET /api/frame` отдаёт последний JPEG и метаданные в заголовках, до первого кадра — `204`. `GET /api/status` отдаёт JSON с метаданными. Для всех ответов отключено кеширование.
