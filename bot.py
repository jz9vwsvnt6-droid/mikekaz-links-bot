"""
Mikhail Links Bot — без внешних зависимостей, только стандартная библиотека Python.

Следит за сообщениями в группе, находит ссылки, автоматически раскладывает их
по темам (ИИ-инструменты, промпты, курсы, карьера, бизнес и т.д.) и хранит в
локальной SQLite-базе. Работает через long polling (getUpdates), поэтому не
нужен публичный домен/вебхук — достаточно постоянно работающего процесса.

Запуск:
    export BOT_TOKEN="ваш_токен_от_BotFather"
    python3 bot.py
"""

import os
import re
import json
import sqlite3
import logging
import time
import urllib.request
import urllib.parse
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("links-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DB_PATH = os.environ.get("DB_PATH", "links.db")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

URL_RE = re.compile(r"https?://\S+")

CATEGORY_RULES = [
    ("Промпты и гайды", r"промпт|prompt"),
    ("Курсы и обучение", r"курс|обучени|учебник|роадмап|лекци"),
    ("Книги и подборки", r"книг"),
    ("Продуктивность", r"продуктивност|прокрастинац|лайфхак"),
    ("Карьера и работа", r"собес|резюме|карьер|оффер|эйчар|рекрутер"),
    ("Бизнес и стартапы", r"стартап|бизнес"),
    ("Турция и жизнь за границей", r"турци|тапу|недвижимост|виза|внж|аптек|автобус|мерсин|стамбул|лекарств"),
    ("Развлечения", r"фильм|подкаст|кино|сериал"),
    ("ИИ-инструменты", r"нейронк|ии.?инструмент|ai.?tool|chatgpt|claude|gpt|скилл|figma|huggingface|github"),
]


def categorize(text: str) -> str:
    low = text.lower()
    for name, pattern in CATEGORY_RULES:
        if re.search(pattern, low):
            return name
    return "Разное"


# --- Telegram API helpers ---------------------------------------------------

def api_call(method, params=None, files=None, timeout=35):
    url = f"{API}/{method}"
    if files:
        # multipart not needed for our use (only sendDocument uses a local path via sendMessage+file trick)
        raise NotImplementedError
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def send_message(chat_id, text, reply_markup=None, parse_mode=None, disable_preview=False):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    if parse_mode:
        params["parse_mode"] = parse_mode
    if disable_preview:
        params["disable_web_page_preview"] = "true"
    return api_call("sendMessage", params)


def send_document(chat_id, file_path, filename):
    url = f"{API}/sendDocument"
    boundary = "----linksbotboundary"
    with open(file_path, "rb") as f:
        content = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def answer_callback(callback_id):
    api_call("answerCallbackQuery", {"callback_query_id": callback_id})


# --- storage -----------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            message_id INTEGER,
            category TEXT,
            title TEXT,
            link TEXT,
            author TEXT,
            created_at TEXT
        )
        """
    )
    return conn


def save_links(chat_id, message_id, text, links, author):
    category = categorize(text)
    title = text.strip().replace("\n", " ")[:200]
    conn = db()
    with conn:
        for link in links:
            conn.execute(
                "INSERT INTO links (chat_id, message_id, category, title, link, author, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (chat_id, message_id, category, title, link, author, datetime.utcnow().isoformat()),
            )
    conn.close()
    return category


# --- command handling ---------------------------------------------------------

def handle_start(chat_id):
    send_message(
        chat_id,
        "Привет! Я слежу за этим чатом и автоматически раскладываю по темам все "
        "ссылки, которые сюда присылают.\n\n"
        "Команды:\n"
        "/topics — список тем со счётчиком ссылок\n"
        "/find слово — поиск по заголовкам\n"
        "/export — выгрузить всё одним markdown-файлом",
    )


def handle_topics(chat_id):
    conn = db()
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM links WHERE chat_id=? GROUP BY category ORDER BY COUNT(*) DESC",
        (chat_id,),
    ).fetchall()
    conn.close()
    if not rows:
        send_message(chat_id, "Пока ничего не сохранено — присылайте ссылки в чат.")
        return
    buttons = [[{"text": f"{cat} ({count})", "callback_data": f"cat:{cat}"}] for cat, count in rows]
    send_message(chat_id, "Темы:", reply_markup={"inline_keyboard": buttons})


def handle_category(chat_id, category):
    conn = db()
    rows = conn.execute(
        "SELECT title, link FROM links WHERE chat_id=? AND category=? ORDER BY id DESC LIMIT 20",
        (chat_id, category),
    ).fetchall()
    conn.close()
    if not rows:
        send_message(chat_id, f"В теме «{category}» пока пусто.")
        return
    lines = [f"*{category}*"]
    for title, link in rows:
        safe_title = title.replace("[", "(").replace("]", ")")
        lines.append(f"- [{safe_title[:80]}]({link})")
    send_message(chat_id, "\n".join(lines), parse_mode="Markdown", disable_preview=True)


def handle_find(chat_id, keyword):
    if not keyword:
        send_message(chat_id, "Использование: /find слово")
        return
    conn = db()
    rows = conn.execute(
        "SELECT category, title, link FROM links WHERE chat_id=? AND title LIKE ? ORDER BY id DESC LIMIT 20",
        (chat_id, f"%{keyword}%"),
    ).fetchall()
    conn.close()
    if not rows:
        send_message(chat_id, "Ничего не нашлось.")
        return
    lines = [f"*{cat}* — [{title[:70]}]({link})" for cat, title, link in rows]
    send_message(chat_id, "\n".join(lines), parse_mode="Markdown", disable_preview=True)


def handle_export(chat_id):
    conn = db()
    rows = conn.execute(
        "SELECT category, title, link, created_at FROM links WHERE chat_id=? ORDER BY category, id",
        (chat_id,),
    ).fetchall()
    conn.close()
    if not rows:
        send_message(chat_id, "Пока нечего экспортировать.")
        return

    by_cat = {}
    for cat, title, link, created_at in rows:
        by_cat.setdefault(cat, []).append((title, link, created_at))

    lines = ["# Ссылки по темам\n"]
    for cat, items in by_cat.items():
        lines.append(f"\n## {cat} ({len(items)})\n")
        for title, link, created_at in items:
            date = created_at[:10]
            lines.append(f"- **{date}** — {title[:120]} — [ссылка]({link})")

    path = "/tmp/export.md"
    with open(path, "w") as f:
        f.write("\n".join(lines))
    send_document(chat_id, path, "ссылки-по-темам.md")


# --- update loop ---------------------------------------------------------------

def process_update(update):
    if "callback_query" in update:
        cq = update["callback_query"]
        answer_callback(cq["id"])
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        if data.startswith("cat:"):
            handle_category(chat_id, data.split("cat:", 1)[1])
        return

    msg = update.get("message")
    if not msg or "text" not in msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg["text"]

    if text.startswith("/start") or text.startswith("/help"):
        handle_start(chat_id)
        return
    if text.startswith("/topics"):
        handle_topics(chat_id)
        return
    if text.startswith("/find"):
        keyword = text[len("/find"):].strip()
        handle_find(chat_id, keyword)
        return
    if text.startswith("/export"):
        handle_export(chat_id)
        return

    links = URL_RE.findall(text)
    if links:
        author = msg.get("from", {}).get("first_name", "unknown")
        category = save_links(chat_id, msg["message_id"], text, links, author)
        log.info("Saved %d link(s) to '%s' from chat %s", len(links), category, chat_id)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN environment variable")
    log.info("Bot starting (long polling)...")
    offset = 0
    while True:
        try:
            resp = api_call("getUpdates", {"offset": offset, "timeout": 30}, timeout=40)
        except Exception as e:
            log.warning("getUpdates failed: %s — retrying in 5s", e)
            time.sleep(5)
            continue
        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            try:
                process_update(update)
            except Exception:
                log.exception("Failed to process update")


if __name__ == "__main__":
    main()
