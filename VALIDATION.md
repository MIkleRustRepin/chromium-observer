# Проверка Chromium Observer

## Среда

- Ubuntu 25.10, x86_64, около 15 ГиБ RAM.
- Swap: суммарно 10 ГиБ; настройка проверена после перезапуска системы.
- Исходная ревизия Chromium: `5869fe313571d75152b77813cf0a4ee26a4e6df0`, версия `155.0.8059.84`.

## Выполнено

- `python3 -m unittest server.test_server -v`: 5 тестов пройдено. Проверены последний кадр, отсутствие кеширования, отклонение устаревших запросов, смена сессии, параллельные загрузки и неправильные запросы.
- `node scripts/test_viewer.cjs`: пройдено в установленном Google Chrome. Проверены декодирование настоящего JPEG, размер 900×600, устаревание кадра и восстановление. Задержка в этом запуске — 878 мс. Это проверка сервера и просмотрщика, а не нативного сервиса.
- `python3 scripts/apply_patch.py`: обнаруживает уже применённый патч без повторного изменения файлов.
- Системные зависимости Chromium установлены через upstream `install-build-deps.py`; для Ubuntu 25.10 использован `--unsupported`.
- Синхронизация зависимостей и `gclient runhooks`: успешно.
- GN generation: успешно, 35 981 targets из 5066 файлов.
- `gn check out/Observer '//chrome/browser/observer:*'`: Header dependency check OK.
- `gn format --dry-run` и `clang-format --dry-run --Werror` для новых C++-файлов: успешно.

## Ещё не подтверждено

Компиляционная проверка остановилась на ошибке TS2306 в зависимости DevTools (`theme_support.ts`); причина проверяется. До компиляции нового C++-модуля сборка пока не дошла. Нативные тесты ещё не запускались; готовый Linux-бинарник отсутствует.

После сборки:

```bash
python3 scripts/build.py --tests
chromium/src/out/Observer/browser_tests --gtest_filter='PageFrameStreamerBrowserTest.*' --test-launcher-jobs=1
xvfb-run -a node scripts/test_native.cjs
python3 scripts/package.py
```

На Linux browser tests также запускать с виртуальным дисплеем, если обычный дисплей не используется. Сквозной тест требует Playwright, Python Pillow и openbox. Он использует обычный профиль, поскольку вкладки инкогнито исключены из захвата.

Сквозной тест сохраняет результаты в `artifacts/native-validation.json` и последний настоящий кадр в `artifacts/native-last-frame.jpg`.

macOS arm64: подготовлены исходники и команды; сборка и проверки ожидают доступного Mac с Xcode.
