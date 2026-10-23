// Run with the bundled Node runtime. Set NODE_MODULES_PATH for another installation.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const root = path.dirname(fileURLToPath(import.meta.url));
const modules = process.env.NODE_MODULES_PATH;
const { Presentation, PresentationFile } = await import(modules
  ? pathToFileURL(path.join(modules, '@oai/artifact-tool/dist/artifact_tool.mjs')).href
  : '@oai/artifact-tool');
const build = process.env.BOTFLOW_DECK_BUILD || '/tmp/botflow-presentation-build';
await fs.mkdir(build, { recursive: true });
const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const ink = '#202324', muted = '#585B5C', gold = '#A16C16', paper = '#F7F5F0';
const font = 'Carlito';
function text(slide, value, x, y, w, h, size = 28, color = ink, bold = false) {
  const box = slide.shapes.add({ geometry: 'textbox', name: value.split('\n')[0].slice(0, 45),
    position: { left: x, top: y, width: w, height: h }, fill: 'none',
    line: { fill: 'none', width: 0 } });
  box.text = value;
  box.text.style = { typeface: font, fontSize: size, bold, color, insets: 0,
    verticalAlignment: 'top', wrap: 'word' };
  return box;
}
function slide(title, notes = '') {
  const s = deck.slides.add(); s.background.fill = paper;
  text(s, title, 64, 48, 1152, 68, 46, ink, true);
  text(s, String(deck.slides.items.length).padStart(2, '0'), 1154, 667, 60, 26, 18, muted);
  s.speakerNotes.text = notes;
  return s;
}
async function image(slide, filename, x, y, w, h) {
  const b = await fs.readFile(path.join(root, 'images', filename));
  slide.images.add({ blob: b.buffer.slice(b.byteOffset, b.byteOffset+b.byteLength),
    contentType: 'image/png', alt: filename, fit: 'contain',
    position: { left: x, top: y, width: w, height: h } });
}

let s = slide('BotFlow', 'Тестовое задание. Скриншоты реального frontend с вымышленными API-данными: capture_screens.mjs.');
text(s, "Сообщение в Telegram.\nЗадача на общей доске.", 64, 142, 1152, 100, 39, ink, true);
text(s, "Отправить текст\nили голос", 64, 301, 310, 92, 32, ink, true);
text(s, "Увидеть задачу\nи сменить статус", 64, 423, 310, 92, 32, ink, true);
text(s, "Русский и армянский\nв одном интерфейсе", 64, 567, 310, 72, 26, muted);
await image(s, '02-kanban-board.png', 400, 240, 816, 414);

s = slide('Доступ и работа команды', 'Реализация: identity-service/application/commands/auth.py, access.py. Одноразовый код, заявки и отзыв сессий.');
text(s, "Войти по коду из бота", 64, 164, 380, 50, 31, ink, true);
text(s, "Без отдельного пароля\nдля веб-панели.", 64, 221, 370, 85, 27, muted);
text(s, "Одобрить участника", 64, 346, 380, 50, 31, ink, true);
text(s, "Администратор управляет\nзаявками и доступом.", 64, 403, 370, 85, 27, muted);
text(s, "Работать со своими задачами", 64, 528, 380, 76, 31, ink, true);
text(s, "Права проверяются на сервере.", 64, 608, 380, 42, 26, muted);
await image(s, '04-admin-panel.png', 464, 163, 752, 400);
text(s, "Ручной сценарий проверен на двух пользователях.", 464, 587, 752, 72, 27, gold);

s = slide('Как устроен проект', 'Схема через archify: architecture.json. Три отдельных PostgreSQL. Automation: api/worker.py, transcribe_voice.py. Tasks: create_task.py, board_ws.py.');
await image(s, 'architecture.png', 56, 137, 1168, 404);
text(s, "Голос распознаётся локально в отдельном worker. Аудио хранится в MinIO.", 64, 553, 1152, 55, 27, muted);
text(s, "Python / FastAPI     React / TypeScript     PostgreSQL     Docker Compose", 64, 622, 1152, 40, 26, ink, true);

s = slide('Обработка ошибок и проверка результата', 'Код: kernel_platform/outbox.py, inbox.py, tasks-service/application/commands/create_task.py, change_task_status.py, frontend/src/useBoard.ts, boardRealtime.ts. CI: .github/workflows/ci.yml. https://staff.am/ru/jobs/software-development/ai-full-stack-automation-engineer-3');
const rows = [
  ['Повторная доставка сообщения', 'Inbox и ключ источника защищают от дублей задач.'],
  ['Одновременное изменение', 'Сервер проверяет версию. Панель обновляет карточку.'],
  ['Разрыв соединения', 'После восстановления панель перечитывает данные.'],
];
rows.forEach(([a,b],i)=>{text(s,a,64,174+i*116,493,75,32,ink,true);text(s,b,590,174+i*116,626,92,29,muted);});
text(s, "GitHub Actions: линтеры, типы, тесты, сборка и E2E", 64, 557, 1152, 51, 30, gold, true);
text(s, "Готов показать сценарий и разобрать решения по коду.", 64, 614, 1152, 48, 28, ink);

for (const [i, s] of deck.slides.items.entries()) {
  await fs.writeFile(path.join(build, `slide-${i+1}.png`), new Uint8Array(await (await deck.export({slide:s,format:'png',scale:1.5})).arrayBuffer()));
  await fs.writeFile(path.join(build, `slide-${i+1}.layout.json`), await (await s.export({format:'layout'})).text());
}
await (await PresentationFile.exportPptx(deck)).save(path.join(build, 'candidate.pptx'));
if (process.env.BOTFLOW_PDF_ENGINE === 'libreoffice') {
  await promisify(execFile)(process.env.BOTFLOW_SOFFICE || 'libreoffice', [
    '-env:UserInstallation=' + pathToFileURL(path.join(build, 'lo-profile')).href,
    '--headless', '--convert-to', 'pdf', '--outdir', build, path.join(build, 'candidate.pptx'),
  ]);
  await fs.copyFile(path.join(build, 'candidate.pdf'), path.join(root, 'BotFlow.pdf'));
} else {
  await fs.writeFile(path.join(root, 'BotFlow.pdf'), new Uint8Array(await (await deck.export({format:'pdf'})).arrayBuffer()));
}
console.log(JSON.stringify({slides:deck.slides.items.length,pdf:path.join(root,'BotFlow.pdf'),previews:build}));
