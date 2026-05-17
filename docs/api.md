# Apartment Sources API

Локальний JSON API для джерел, зібраних оголошень і запуску збору.

## Запуск

```bash
python api.py --host 127.0.0.1 --port 8000
```

## Endpoints

### Health

```bash
curl http://127.0.0.1:8000/health
```

### Список джерел

```bash
curl http://127.0.0.1:8000/api/sources
```

### Одне джерело

```bash
curl http://127.0.0.1:8000/api/sources/REM.ua
curl http://127.0.0.1:8000/api/sources/DIM.RIA
curl http://127.0.0.1:8000/api/sources/OLX
curl http://127.0.0.1:8000/api/sources/LUN
```

### Поточні оголошення з CSV

```bash
curl "http://127.0.0.1:8000/api/listings?source=REM.ua&city=Одеса&min_price=35000&max_price=45000&limit=10"
```

Параметри:

- `source`: одне або кілька джерел через кому, наприклад `REM.ua,DIM.RIA`
- `city`: одне або кілька міст через кому, наприклад `Одеса,Дніпро`
- `min_price`
- `max_price`
- `limit`

### Оголошення без телефону

```bash
curl "http://127.0.0.1:8000/api/phones/missing?source=OLX&limit=20"
```

### Автоматично витягнути телефон з опису

```bash
curl -X POST http://127.0.0.1:8000/api/phones/extract \
  -H "Content-Type: application/json" \
  -d '{"overwrite": false}'
```

Це спрацює тільки якщо телефон уже є в тексті оголошення. Якщо сайт ховає номер за кнопкою або маскою, потрібно передати номер явно.

### Автоматично витягнути телефон зі сторінок оголошень

```bash
curl -X POST http://127.0.0.1:8000/api/phones/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "sources": ["REM.ua", "DIM.RIA", "OLX"],
    "overwrite": false,
    "sleep": 0.4,
    "olx_phone_delay": 1.2
  }'
```

Цей endpoint відкриває URL кожного оголошення і шукає номер у HTML, `tel:`-посиланнях, JSON-LD та структурованих даних сторінки. Для `OLX` додатково бере numeric offer id зі сторінки і пробує endpoint `/api/v1/offers/{id}/limited-phones/`; `olx_phone_delay` задає паузу перед цим запитом. На `REM.ua` номер доступний у сторінці. На `DIM.RIA` і частині `OLX` сайт може віддати тільки маску або `suspicious activity`, тоді рядок лишається порожнім.

### Заповнити телефон вручну або з іншого сервісу

По номеру рядка:

```bash
curl -X POST http://127.0.0.1:8000/api/phones \
  -H "Content-Type: application/json" \
  -d '{"№": "12", "phone": "+380001112233"}'
```

По URL:

```bash
curl -X POST http://127.0.0.1:8000/api/phones \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/listing",
    "phone": "000 111 22 33"
  }'
```

Масово:

```bash
curl -X POST http://127.0.0.1:8000/api/phones \
  -H "Content-Type: application/json" \
  -d '{
    "updates": [
      {"№": "1", "phone": "000 111 22 33"},
      {"№": "2", "phone": ["0001112233", "+380001112233"]}
    ],
    "overwrite": false
  }'
```

Телефони нормалізуються до формату `+380XXXXXXXXX`. Якщо в рядку вже є телефон, API не перезапише його без `"overwrite": true`.

### Запуск збору

```bash
curl -X POST http://127.0.0.1:8000/api/collect \
  -H "Content-Type: application/json" \
  -d '{
    "sources": ["REM.ua", "DIM.RIA", "OLX"],
    "cities": ["Одеса", "Дніпро"],
    "min_price": 35000,
    "max_price": 45000,
    "max_pages": 1,
    "sleep": 1.0
  }'
```

`POST /api/collect` перезаписує `data/apartments_multi_source.csv` результатами збору. `LUN` показується у списку джерел, але для HTTP-збору позначений як `collectable_by_http: false`, бо часто віддає Cloudflare/JS challenge.
