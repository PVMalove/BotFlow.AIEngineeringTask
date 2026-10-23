# Материалы BotFlow

- [Презентация, 4 страницы](BotFlow.pdf) — краткий обзор для проверяющего.
- [Интерактивная архитектура](botflow-architecture.html) — схема archify со ссылками на код.
- [Исходник схемы](architecture.json).

Скриншоты в `images/` сняты с реального frontend на вымышленных данных.
`@taskpilot` и `@northstar` — демонстрационные аккаунты.

## Пересборка

`capture_screens.mjs` снимает интерфейс с тестовыми ответами API. Запустите Vite
отдельно и передайте его адрес в `BOTFLOW_SCREENSHOT_URL` (по умолчанию
`http://127.0.0.1:5177`). `BOTFLOW_CHROME` позволяет указать установленный Chromium.
Скрипт использует Playwright из зависимостей frontend.

`build_presentation.mjs` собирает слайды через `@oai/artifact-tool` и сохраняет
`BotFlow.pdf`. Если пакет не доступен обычным импортом, `NODE_MODULES_PATH` задаёт
каталог модулей среды авторинга. Временные файлы пишутся в
`BOTFLOW_DECK_BUILD` (по умолчанию `/tmp/botflow-presentation-build`).
Для экспорта через LibreOffice задайте `BOTFLOW_PDF_ENGINE=libreoffice`.
`BOTFLOW_SOFFICE` позволяет указать исполняемый файл.

Схема пересобирается установленным archify:

```bash
node .harness/skills/archify/bin/archify.mjs finalize architecture \
  docs/presentation/architecture.json docs/presentation/botflow-architecture.html \
  --repo-root . --quality showcase --json
```

Если Chromium не обнаружен автоматически, задайте `ARCHIFY_CHROME`.
SVG и PNG в `images/` экспортированы из проверенной интерактивной схемы.
