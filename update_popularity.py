"""
update_popularity.py — считает рейтинг книг по вошедшим читателям и пишет
popularity.json рядом с reader-prototype.html.

Источник: Firestore проекта ivrit-progress, документы users/{uid}/readerMeta/
{bookId} (страница, предложение, «прочитано», «слова, которые уже видел»).
Для каждой книги считаются:
  readers   — сколько разных вошедших пользователей её открывало
              (есть сохранённая страница/предложение/отметка/слова);
  completed — сколько из них дочитало до конца.
В файл попадают ТОЛЬКО эти агрегаты по книгам — никаких uid и содержимого
документов. Читалка (renderLibrary) по ним сортирует нетронутые и дочитанные
книги и подсказывает новичку самую популярную. Анонимные читатели (без входа
через Google) нигде не сохраняются и в рейтинг не попадают.

Запуск (нужен ключ сервис-аккаунта, по умолчанию берётся из E:\\ivrit):
    python update_popularity.py
    python update_popularity.py --key путь/к/ключу.json --out popularity.json
Потом закоммитить popularity.json как обычный файл сайта.

Использует REST API Firestore (не gRPC) — на этой машине gRPC не проходит
через антивирус; тот же подход, что в fetch_feedback.py тренажёра.
"""

import argparse
import collections
import datetime
import glob
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import google.auth.transport.requests
from google.oauth2 import service_account

PROJECT_ID = "ivrit-progress"
SCOPES = ["https://www.googleapis.com/auth/datastore"]
RUN_QUERY = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents:runQuery"
DEFAULT_KEY_GLOB = r"E:\ivrit\ivrit-progress-firebase-adminsdk-*.json"


def field_present(fields, name):
    return name in fields and fields[name] not in (None, {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "popularity.json"))
    args = ap.parse_args()

    key = args.key
    if not key:
        found = glob.glob(DEFAULT_KEY_GLOB)
        if not found:
            sys.exit("Не нашёл ключ сервис-аккаунта: укажи --key")
        key = found[0]

    creds = service_account.Credentials.from_service_account_file(key, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(creds)

    readers = collections.defaultdict(set)
    completed = collections.defaultdict(set)
    body = {"structuredQuery": {"from": [{"collectionId": "readerMeta", "allDescendants": True}]}}
    resp = session.post(RUN_QUERY, json=body, timeout=120)
    resp.raise_for_status()
    docs = 0
    for row in resp.json():
        d = row.get("document")
        if not d:
            continue
        docs += 1
        # name: projects/.../documents/users/{uid}/readerMeta/{bookId}
        parts = d["name"].split("/documents/", 1)[1].split("/")
        if len(parts) != 4 or parts[0] != "users" or parts[2] != "readerMeta":
            continue
        uid, book_id = parts[1], parts[3]
        f = d.get("fields", {})
        opened = any(field_present(f, k) for k in ("page", "sentence", "seen")) or \
            f.get("completed", {}).get("booleanValue") is True
        if opened:
            readers[book_id].add(uid)
        if f.get("completed", {}).get("booleanValue") is True:
            completed[book_id].add(uid)

    books = {b: {"readers": len(u), "completed": len(completed.get(b, ()))} for b, u in readers.items()}
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "books": dict(sorted(books.items(), key=lambda kv: (-kv[1]["readers"], kv[0]))),
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Просмотрено документов readerMeta: {docs}. Книг с читателями: {len(books)}. -> {args.out}")
    for b, v in list(out["books"].items())[:10]:
        print(f"  {b}: читателей {v['readers']}, дочитали {v['completed']}")


if __name__ == "__main__":
    main()
