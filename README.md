# SmartButtjet
 SmartButtJet 🚀 Drop a message and watch your income become records, editable notes, stats, and charts. Pause the money radar when needed, then fire it back up. It also calculates your wealth in burgers, coffee, and suspicious fractions of matchboxes. Finance, but with more thrust and more butt. 🍑
# Income Stats Bot

Інтерактивний Telegram-бот для обліку доходів у спеціальній групі. Кожне
звичайне повідомлення в дозволеній групі вважається доходом. Локальний parser
розпізнає українські й англійські суми та валюти без AI/API-запитів.

## Можливості

- локальний parser UAH, USD та EUR;
- UAH як валюта за замовчуванням;
- кілька доходів з одного повідомлення;
- inline-редагування суми, валюти, категорії та опису;
- довільні категорії;
- нотатки до записів;
- Pandas-статистика;
- інтерактивні Plotly HTML-діаграми;
- ZIP-експорт для адміністраторів;
- CSV із атомарним записом;
- асинхронний aiogram polling.

## Налаштування

Потрібні Python 3.12–3.13, `uv` і `just`.

```bash
cp .env.example .env
```

Додай токен BotFather:

```env
TELEGRAM_BOT_TOKEN=123456:your-token
```

Відредагуй `config.yaml`:

```yaml
bot:
  timezone: Europe/Kyiv
  allowed_chat_ids:
    - -1001234567890
  admin_user_ids:
    - 123456789
```

Порожній `allowed_chat_ids` дозволяє всі чати й зручний лише для локального
тестування. Для реального бота завжди вкажи ID потрібної групи.

Оскільки бот обробляє звичайні повідомлення групи, у BotFather потрібно
вимкнути Privacy Mode: `/setprivacy` → вибрати бота → `Disable`.

## Запуск

```bash
just setup
just check
just run
```

Перевірка parser без Telegram:

```bash
just parse "Отримав 1 500 грн за консультацію"
just parse "Earned $250 for design"
```

## Команди

- `/help` — допомога;
- `/records` — останні записи;
- `/stats` — статистика;
- `/chart` — інтерактивна діаграма;
- `/export` — ZIP для адміністратора;
- `/cancel` — скасувати редагування.

Основні дії також доступні через reply та inline-кнопки.

## Дані

Бот створює:

```text
data/records.csv
data/record_notes.csv
data/exports/
```

CSV підходить для MVP і невеликої групи. Якщо одночасних користувачів або
обсяг даних суттєво зросте, наступним кроком має бути SQLite чи PostgreSQL.

## Розробка

```bash
just format
just lint
just typecheck
just test
just check
```

Усі зміни виконуються у feature-гілках і доставляються через pull request.
Прямий push у `main` або `master` заборонений правилами `AGENTS.md`.
