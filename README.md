# Веб-приложение для управления планированием смен технического персонала

Дипломный проект на Django 5 + PostgreSQL + Google OR-Tools. Автоматическое составление графика смен через целочисленное линейное программирование (CP-SAT solver).

## Стек

- Python 3.12+, Django 5.1
- PostgreSQL 14+
- Google OR-Tools 9.11 (CP-SAT)
- Bootstrap 5, FullCalendar 6, Chart.js (подключаются с CDN)

## Установка

Требования: установленный Python 3.12+ и PostgreSQL 14+.

### Шаг 1. Установка PostgreSQL (если ещё не установлен)

- **Windows:** скачать установщик с [postgresql.org/download/windows](https://www.postgresql.org/download/windows/), запустить и пройти мастер установки. Запомнить пароль пользователя `postgres`.
- **Linux (Ubuntu/Debian):** `sudo apt install postgresql postgresql-contrib`.

### Шаг 2. Создание базы данных

Открыть консоль `psql`:

```sql
CREATE DATABASE shift_scheduler;
ALTER USER postgres WITH PASSWORD 'postgres';
\q
```

### Шаг 3. Клонирование проекта и установка зависимостей

```bash
git clone <ссылка на репозиторий>
cd shift_scheduler_project

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS

pip install -r requirements.txt
```

### Шаг 4. Настройка `.env`

```bash
copy .env.example .env            # Windows
# cp .env.example .env            # Linux/macOS
```

Открыть `.env` в редакторе и при необходимости поменять пароль PostgreSQL.

### Шаг 5. Миграции и запуск

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_data         # необязательно: тестовые данные
python manage.py runserver
```

Открыть в браузере <http://127.0.0.1:8000/>.

Логины тестовых сотрудников после `seed_data`: `ivanov`, `petrov`, `sidorov`, … пароль `password123`.

## Разделы приложения

| Раздел | Адрес | Назначение |
|---|---|---|
| Главная | `/main/` | KPI, ближайшие непокрытые смены, предупреждения DSS |
| Расписание | `/schedule/` | Календарь FullCalendar, кнопка «Оптимизировать» |
| Сотрудники | `/employees/` | Справочник, поиск, фильтр по навыкам |
| Смены | `/shifts/` | Список смен, групповое добавление |
| Назначения | `/assignments/` | Ручные назначения сотрудников на смены |
| Аналитика | `/analytics/` | Нарушения ТК РФ со ссылками на статьи |
| Отчёты | `/reports/` | Часы и переработки, экспорт в CSV |
| Админка | `/admin/` | Управление пользователями (только суперпользователь) |

## Тесты

```bash
pytest -v
```

Должно пройти 7 тестов из 7.

## Публикация в сети Интернет (на примере TimeWeb VPS)

1. Зарегистрироваться на <https://timeweb.cloud/>, заказать VPS «Cloud MSK 15» (~477 ₽/мес, 5 дней бесплатно). ОС: Ubuntu 22.04 LTS.

2. Подключиться по SSH (адрес и пароль приходят на e-mail). Установить ПО:

   ```bash
   apt update && apt install -y python3.12 python3.12-venv python3-pip git \
       postgresql postgresql-contrib nginx
   ```

3. Создать базу данных:

   ```bash
   sudo -u postgres psql -c "CREATE DATABASE shift_scheduler;"
   sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'СЛОЖНЫЙ_ПАРОЛЬ';"
   ```

4. Развернуть проект:

   ```bash
   cd /var/www
   git clone <ссылка> shift_scheduler
   cd shift_scheduler
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install gunicorn
   cp .env.example .env
   ```

   Открыть `.env` и установить:

   ```
   DEBUG=False
   ALLOWED_HOSTS=<имя-сервера>.tw1.ru
   DB_PASSWORD=СЛОЖНЫЙ_ПАРОЛЬ
   SECRET_KEY=<длинная случайная строка>
   ```

5. Применить миграции, создать суперпользователя, собрать статику:

   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py collectstatic --noinput
   ```

6. Запустить через gunicorn (для проверки):

   ```bash
   gunicorn shift_scheduler.wsgi:application --bind 127.0.0.1:8000
   ```

7. Настроить nginx как обратный прокси. Создать `/etc/nginx/sites-available/shift_scheduler`:

   ```nginx
   server {
       listen 80;
       server_name <имя-сервера>.tw1.ru;

       location /static/ {
           alias /var/www/shift_scheduler/staticfiles/;
       }

       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

   ```bash
   ln -s /etc/nginx/sites-available/shift_scheduler /etc/nginx/sites-enabled/
   nginx -t && systemctl restart nginx
   ```

8. Для постоянной работы gunicorn оформить systemd-сервис в `/etc/systemd/system/shift_scheduler.service`:

   ```ini
   [Unit]
   Description=Gunicorn for shift_scheduler
   After=network.target

   [Service]
   User=www-data
   WorkingDirectory=/var/www/shift_scheduler
   ExecStart=/var/www/shift_scheduler/.venv/bin/gunicorn \
       --workers 3 --bind 127.0.0.1:8000 shift_scheduler.wsgi:application
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

   ```bash
   systemctl daemon-reload
   systemctl enable --now shift_scheduler
   ```

Сайт доступен по адресу `http://<имя-сервера>.tw1.ru/`.

## Структура проекта

```
shift_scheduler_project/
├── manage.py
├── requirements.txt
├── .env.example
├── pytest.ini
├── shift_scheduler/           # настройки проекта
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── scheduler/                 # основное приложение
    ├── models.py              # модели данных
    ├── views.py               # представления
    ├── urls.py                # маршруты
    ├── forms.py               # формы
    ├── optimizer.py           # модуль оптимизации (CP-SAT)
    ├── analytics.py           # модуль поддержки принятия решений (DSS)
    ├── notifications.py       # система уведомлений
    ├── benchmarks.py          # нагрузочные тесты оптимизатора
    ├── tests.py               # модульные тесты
    ├── admin.py               # настройка админки
    ├── apps.py
    ├── context_processors.py
    ├── management/commands/
    │   └── seed_data.py       # генерация тестовых данных
    ├── migrations/
    └── templates/scheduler/   # HTML-шаблоны
```

## Лицензия

Apache License 2.0
