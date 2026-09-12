"""
wait_for_gemini_quota.py — умное ожидание для retry-циклов генерации.

Раньше все bash-циклы (2_generate_story.py / 5_gemini_pipeline.py по
кругу моделей) при исчерпании ВСЕХ моделей просто спали фиксированные
15-20 минут и пробовали снова — вслепую, весь день, пока квота реально
не сбросится. Проверили вживую (2026-09-11): бесплатная дневная квота
Gemini сбрасывается ровно в полночь по тихоокеанскому времени (PT),
НЕ по местной полуночи пользователя — подтверждено прямым замером за
минуту до и через минуту после границы (gemini-3.6-flash и
gemini-3.8-flash синхронно перешли 429 -> OK).

Вместо очередного sleep N — этот скрипт считает, сколько секунд осталось
до следующей полуночи PT (+ буфер), спит ровно столько и завершается.
Использование в retry-цикле:

    if [ $model_idx -eq 0 ]; then
      python wait_for_gemini_quota.py
    else
      sleep 3
    fi

Не проверялось для Cloudflare Workers AI — там сброс лимита нейронов
может быть привязан к другой границе (не факт, что тоже PT-полночь),
поэтому для CF-циклов пока используй обычный sleep с разумным
интервалом, не этот скрипт.

PDT (UTC-7) актуален с марта по начало ноября; в остальное время года
тихоокеанское время — PST (UTC-8), тогда OFFSET_HOURS ниже нужно
поменять на -8.
"""

import argparse
import datetime
import sys
import time

OFFSET_HOURS = -7  # PDT (UTC-7); смени на -8 в PST-сезон (ноябрь-март)


def seconds_until_next_pt_midnight(buffer_seconds=60):
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    pt_offset = datetime.timedelta(hours=OFFSET_HOURS)
    now_pt = now_utc + pt_offset
    next_midnight_pt_date = (now_pt + datetime.timedelta(days=1)).date()
    next_midnight_pt = datetime.datetime.combine(
        next_midnight_pt_date, datetime.time(0, 0, 0),
        tzinfo=datetime.timezone(pt_offset),
    )
    next_midnight_utc = next_midnight_pt.astimezone(datetime.timezone.utc)
    return (next_midnight_utc - now_utc).total_seconds() + buffer_seconds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buffer", type=int, default=60,
                     help="запас в секундах после полуночи PT (по умолчанию 60)")
    ap.add_argument("--max-wait", type=int, default=26 * 3600,
                     help="защитный потолок сна в секундах — если расчёт почему-то "
                          "даёт неадекватно большое число, не спать дольше этого")
    ap.add_argument("--dry-run", action="store_true",
                     help="только показать расчёт, не спать")
    args = ap.parse_args()

    wait = seconds_until_next_pt_midnight(args.buffer)
    wait = min(wait, args.max_wait)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    wake_at = now_utc + datetime.timedelta(seconds=wait)
    print(f"Сейчас UTC: {now_utc}", file=sys.stderr)
    print(f"Сплю {wait:.0f}с ({wait/3600:.2f}ч) до полуночи PT + {args.buffer}с "
          f"-> проснусь примерно {wake_at} UTC", file=sys.stderr)

    if not args.dry_run:
        time.sleep(wait)
        print("Проснулся, квота Gemini должна была сброситься.", file=sys.stderr)


if __name__ == "__main__":
    main()
