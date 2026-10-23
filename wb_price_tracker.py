#!/usr/bin/env python3
"""
Трекер цен Wildberries с уведомлениями в Telegram.

Режимы (задаются переменными окружения):
  WB_SELLER_ID   - ID продавца: отслеживается весь его каталог
  WB_ITEM_IDS    - артикулы через запятую: отслеживаются только они
  (можно задать оба сразу)

Остальные настройки:
  TG_TOKEN        - токен бота от @BotFather
  TG_CHAT_ID      - ваш chat_id (узнать у @userinfobot)
  CHECK_INTERVAL  - интервал проверки в секундах (по умолчанию 900 = 15 мин)
  MIN_CHANGE_PCT  - минимальное изменение цены в % для уведомления (по умолчанию 1)
  STATE_FILE      - файл для хранения цен (по умолчанию prices.json)
  RUN_ONCE        - 1 = сделать одну проверку и выйти (для GitHub Actions/cron)

ВАЖНО: используются неофициальные публичные эндпоинты WB. Они периодически
меняются - если перестанет работать, правьте URL в блоке "Эндпоинты".
"""

import json
import logging
import os
import time

import requests

# ---------- Настройки ----------
SELLER_ID = os.getenv("WB_SELLER_ID", "").strip()
ITEM_IDS = [x.strip() for x in os.getenv("WB_ITEM_IDS", "").split(",") if x.strip()]
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))
MIN_CHANGE_PCT = float(os.getenv("MIN_CHANGE_PCT", "1"))
STATE_FILE = os.getenv("STATE_FILE", "prices.json")
RUN_ONCE = os.getenv("RUN_ONCE", "") == "1"  # одна проверка и выход (для GitHub Actions)

# ---------- Эндпоинты (при поломке менять здесь) ----------
DEST = "123585931"  # регион доставки (Москва); влияет на цены/наличие
SELLER_URL = "https://catalog.wb.ru/sellers/v2/catalog"
CARD_URL = "https://card.wb.ru/cards/v2/detail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("wb-tracker")
session = requests.Session()
session.headers.update(HEADERS)


# ---------- Вспомогательные функции ----------
def get_json(url, params, retries=4):
    """GET с повторами и экспоненциальной паузой (WB любит отдавать 429)."""
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                log.warning("429 Too Many Requests, жду %s с", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            wait = 5 * (attempt + 1)
            log.warning("Ошибка запроса (%s), повтор через %s с", e, wait)
            time.sleep(wait)
    return None


def extract_price(product):
    """Достаёт цену в рублях. Берём минимальную цену среди доступных размеров."""
    prices = []
    for size in product.get("sizes", []):
        p = size.get("price") or {}
        value = p.get("product") or p.get("total")
        if value:
            prices.append(value / 100)
    if prices:
        return min(prices)
    # запасной вариант для старого формата ответа
    legacy = product.get("salePriceU")
    return legacy / 100 if legacy else None


def parse_products(data):
    items = {}
    if not data:
        return items
    products = (data.get("data") or {}).get("products") or data.get("products") or []
    for p in products:
        price = extract_price(p)
        if price is None:
            continue
        items[str(p["id"])] = {
            "name": f'{p.get("brand", "")} / {p.get("name", "")}'.strip(" /"),
            "price": price,
        }
    return items


def fetch_seller_items():
    result = {}
    page = 1
    while page <= 50:  # защита от бесконечного цикла
        data = get_json(
            SELLER_URL,
            {
                "appType": 1,
                "curr": "rub",
                "dest": DEST,
                "page": page,
                "sort": "popular",
                "spp": 30,
                "supplier": SELLER_ID,
            },
        )
        batch = parse_products(data)
        if not batch:
            break
        result.update(batch)
        page += 1
        time.sleep(1.5)
    return result


def fetch_items_by_ids():
    result = {}
    for i in range(0, len(ITEM_IDS), 50):
        chunk = ITEM_IDS[i : i + 50]
        data = get_json(
            CARD_URL,
            {"appType": 1, "curr": "rub", "dest": DEST, "nm": ";".join(chunk)},
        )
        result.update(parse_products(data))
        time.sleep(1.5)
    return result


def fetch_all():
    items = {}
    if SELLER_ID:
        items.update(fetch_seller_items())
    if ITEM_IDS:
        items.update(fetch_items_by_ids())
    return items


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            log.warning("Не удалось прочитать %s, начинаю с нуля", STATE_FILE)
    return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)  # атомарная запись, файл не побьётся при сбое


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("Telegram не настроен, сообщение: %s", text)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.error("Не удалось отправить в Telegram: %s", e)


def build_message(item_id, name, old, new):
    diff = new - old
    pct = diff / old * 100
    arrow = "🔻" if diff < 0 else "🔺"
    return (
        f"{arrow} <b>{name}</b>\n"
        f"{old:.0f} ₽ → <b>{new:.0f} ₽</b> ({pct:+.1f}%)\n"
        f"https://www.wildberries.ru/catalog/{item_id}/detail.aspx"
    )


# ---------- Основной цикл ----------
    current = fetch_all()
    if not current:
        log.warning("Пустой ответ от WB, пропускаю проверку")
        send_telegram(
            "⚠️ <b>Ошибка проверки Wildberries</b>\n"
            "Не удалось получить товары. Цены не проверены.\n"
            "Подробности смотри в логах GitHub Actions."
        )
        return state

    first_run = not state
    changes = 0

    for item_id, info in current.items():
        new_price = info["price"]
        old = state.get(item_id)
        if old and old["price"] > 0:
            change_pct = abs(new_price - old["price"]) / old["price"] * 100
            if change_pct >= MIN_CHANGE_PCT:
                send_telegram(build_message(item_id, info["name"], old["price"], new_price))
                changes += 1
                time.sleep(0.5)  # не упираемся в лимиты Telegram
        state[item_id] = {"name": info["name"], "price": new_price}

    log.info("Проверено товаров: %s, изменений цены: %s", len(current), changes)
    if first_run:
        send_telegram(f"✅ Трекер запущен. Отслеживаю товаров: {len(current)}")
    elif changes == 0:
        send_telegram(
            f"✅ Все цены проверены. Изменений не обнаружено.\n"
            f"📦 Проверено товаров: {len(current)}"
        )
    return state

def check_once(state):
    def main():
    if not SELLER_ID and not ITEM_IDS:
        raise SystemExit("Укажите WB_SELLER_ID и/или WB_ITEM_IDS")
    state = load_state()
    log.info("Старт. Интервал: %s с, порог: %s%%", CHECK_INTERVAL, MIN_CHANGE_PCT)
    while True:
        try:
            state = check_once(state)
            save_state(state)
        except Exception:
            log.exception("Необработанная ошибка в цикле, продолжаю")
        if RUN_ONCE:
            break
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
