
# ---------- Основной цикл ----------
def check_once(state):
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
                send_telegram(
                    build_message(item_id, info["name"], old["price"], new_price)
                )
                changes += 1
                time.sleep(0.5)

        state[item_id] = {
            "name": info["name"],
            "price": new_price,
        }

    log.info(
        "Проверено товаров: %s, изменений цены: %s",
        len(current),
        changes,
    )

    if first_run:
        send_telegram(
            f"✅ Трекер запущен. Отслеживаю товаров: {len(current)}"
        )
    elif changes == 0:
        send_telegram(
            "✅ Все цены проверены. Изменений не обнаружено.\n"
            f"📦 Проверено товаров: {len(current)}"
        )

    return state


def main():
    if not SELLER_ID and not ITEM_IDS:
        raise SystemExit("Укажите WB_SELLER_ID и/или WB_ITEM_IDS")

    state = load_state()
    log.info(
        "Старт. Интервал: %s с, порог: %s%%",
        CHECK_INTERVAL,
        MIN_CHANGE_PCT,
    )

    while True:
        try:
            state = check_once(state)
            save_state(state)
        except Exception:
            log.exception("Необработанная ошибка в цикле")

        if RUN_ONCE:
            break

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
