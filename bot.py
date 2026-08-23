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


# Историческая подборка (собрана вручную из всей переписки группы до
# подключения бота) — загружается один раз командой /seed в чате, куда её
# нужно добавить, если хотите видеть эти ссылки в /topics с самого начала.
SEED_DATA = [
    ("Нейронки для работы с музыкой и звуками (Adobe Enhance, Clip Audio и др.)", "https://podcast.adobe.com/enhance", "2023-02-25T18:20:23"),
    ("Нейронки для всех задач: генераторы текстов Gerwin, Turbotext", "https://gerwin.io/ru", "2023-04-18T20:09:08"),
    ("Morise — нейронка для монтажа вирусных видео", "https://morise.ai/", "2023-12-04T12:10:52"),
    ("Нейронка превращает наброски в картины (Hugging Face)", "https://huggingface.co/spaces/linoyts/scribble-sdxl", "2024-06-04T22:54:34"),
    ("Подборка ИИ-инструментов для всех задач в IT (Habr)", "https://t.me/+p7SetUjKUYI4Yzky", "2024-06-09T09:47:05"),
    ("EvTexture — нейронка для улучшения качества видео", "https://github.com/DachunKai/EvTexture", "2024-06-23T04:25:53"),
    ("Transformer Explainer — как устроены нейронки", "https://poloclub.github.io/transformer-explainer/", "2024-08-10T06:07:14"),
    ("Сборник интерактивных инструментов про устройство нейронок", "https://github.com/Machine-Learning-Tokyo/Interactive_Tools", "2024-08-12T07:58:25"),
    ("BoldVoice — прокачка английского произношения по голосу", "https://www.boldvoice.com/", "2024-12-16T17:15:24"),
    ("Гайд: запускаем DeepSeek локально на компьютере", "https://huggingface.co/unsloth/DeepSeek-R1-Distill-Llama-8B/tree/main", "2025-01-29T15:57:36"),
    ("400 тысяч бесплатных нейронок — каталог AI App Directory от Hugging Face", "https://huggingface.co/spaces", "2025-02-05T14:49:57"),
    ("Как ChatGPT-4o подделывает документы (кейс/предостережение)", "https://www.hdblog.it/sicurezza/articoli/n613771/chatgpt-falsifica-scontrin-documentii/", "2025-04-03T14:15:05"),
    ("Fluig — нейронка создаёт диаграммы из любых документов", "https://www.fluig.cc/home", "2025-05-11T11:39:15"),
    ("Автоматизация поиска работы на hh.ru через n8n", "https://github.com/jointime1/n8n-hh.ru", "2026-01-12T06:03:44"),
    ("Безлимитный доступ к Claude Opus 4.5 через GitHub Copilot", "https://github.com/features/copilot", "2026-02-04T13:08:17"),
    ("Suno 5.5 — новая версия генератора музыки", "https://suno.com", "2026-03-27T07:09:23"),
    ("Huihui-Qwen3.5 — модель без цензуры (Hugging Face)", "https://huggingface.co/huihui-ai/Huihui-Qwen3.5-35B-A3B-abliterated", "2026-04-05T08:56:48"),
    ("GLM-5.1 — бесплатная модель-конкурент Claude", "https://z.ai/blog/glm-5.1", "2026-04-13T03:48:29"),
    ("Claude for Legal — набор инструментов для юридической работы от Anthropic", "https://github.com/anthropics/claude-for-legal", "2026-05-15T10:31:58"),
    ("Расширение Limit Skip — снятие лимитов ChatGPT/Gemini/Claude", "https://addons.mozilla.org/ru/firefox/addon/limit-skip/", "2026-05-27T05:24:28"),
    ("Odysseus — репозиторий-конкурент ChatGPT/Claude от PewDiePie", "https://github.com/pewdiepie-archdaemon/odysseus", "2026-06-03T04:50:48"),
    ("Подборка навыков (skills) для ИИ-агентов", "https://github.com/anthropics/claude-for-legal", "2026-06-06T15:27:23"),
    ("База бесплатных курсов и лекций по нейронкам и кодингу", "https://learnprompting.thinkific.com/courses/take/ChatGPT-for-Everyone", "2024-03-26T12:57:05"),
    ("Мощный промпт для ChatGPT — режим научного эксперта", "https://t.me/denissexy/8278", "2024-06-05T20:38:44"),
    ("Курс по промптам для Claude (гайд по установке API)", "https://docs.google.com/spreadsheets/d/19jzLgRruG9kjUQNKtCg1ZjdD6l6weA6qRXG5zLIAhC8/edit#gid=869808629", "2024-06-09T04:55:50"),
    ("Коллекция промпт-хаков для ChatGPT (jailbreak_llms)", "https://github.com/verazuo/jailbreak_llms", "2024-06-11T12:28:57"),
    ("Официальный гайд по промптам для Luma Dream Machine", "https://lumaai.notion.site/FAQ-and-Prompt-Guide-Luma-Dream-Machine-f7bd5f77478c4994aa69", "2024-06-16T16:26:29"),
    ("База по промпт-инжинирингу от Anthropic (курсы на GitHub)", "https://github.com/anthropics/courses/blob/master/prompt_engineering_interactive_tutorial/README.md", "2024-08-28T05:34:59"),
    ("Мегапромпт для прокачки любой нейронки до уровня GPT-o1", "https://claude.ai/login", "2024-10-07T07:24:13"),
    ("Промпт: персональный план достижения любой цели в ChatGPT", "https://chatgpt.com/", "2024-10-14T08:04:59"),
    ("Мегапромпт для изучения любой темы (план + материалы)", "https://chatgpt.com/", "2024-11-12T18:38:44"),
    ("Промпт против прокрастинации — разбивка большой задачи", "https://www.agenticworkers.com/library/nj4s", "2024-12-14T06:24:36"),
    ("Официальный гайд по промптам от OpenAI (курс Reasoning with o1)", "https://www.deeplearning.ai/short-courses/reasoning-with-o1/", "2024-12-24T03:16:05"),
    ("Гигантский промпт-структуратор знаний по любой теме", "https://github.com/codedidit/learnanything/blob/main/.swm/a-easy-walkthrough.h6ljq0t6.sw.md", "2025-02-14T12:57:32"),
    ("Napkin.ai — строит графики и диаграммы по одному промпту", "https://www.napkin.ai/", "2025-03-16T03:59:40"),
    ("Коллекция слитых системных промптов популярных нейронок", "https://github.com/jujumilk3/leaked-system-prompts", "2025-04-29T13:38:12"),
    ("PromptPort — большая база готовых промптов", "https://promptport.ai/", "2025-05-01T13:15:17"),
    ("Prezo.ai — генерация презентаций по промпту", "https://prezo.ai/", "2025-05-20T20:15:05"),
    ("Manus / ANUS — цепочки промптов для ИИ-агентов (планирование поездок)", "https://manus.im/", "2025-05-24T08:41:30"),
    ("Мегапромпт для запуска бизнеса на российском рынке", "https://telegra.ph/Prompt-dlya-issledovaniya-CA-06-06", "2025-06-06T22:06:29"),
    ("OpenAI Prompt Packs — готовые промпты от OpenAI Academy", "https://academy.openai.com/public/tags/prompt-packs-6849a0f98c613939acef841c", "2025-09-28T22:56:23"),
    ("Официальный курс по промптам для Sora 2 от OpenAI", "https://cookbook.openai.com/examples/sora/sora2_prompting_guide", "2025-10-08T07:41:13"),
    ("Pencil.dev — генерация интерфейсов (замена части работы в Figma)", "https://www.pencil.dev/", "2026-02-26T09:03:39"),
    ("Prompt Master — скилл для Claude Code", "https://github.com/nidhinjs/prompt-master", "2026-03-29T08:10:18"),
    ("Сборник промптов для ChatGPT Images 2.0", "https://github.com/YouMind-OpenLab/awesome-gpt-image-2", "2026-04-24T05:49:39"),
    ("Гайд по промптам для GPT-5.5 от OpenAI", "https://developers.openai.com/api/docs/guides/prompt-guidance", "2026-04-28T03:57:42"),
    ("Design MD — копирование любого сайта без кода", "https://www.designmd.supply/", "2026-05-31T12:42:02"),
    ("Топ бесплатных курсов по Data Science (Гарвард, Google, Стэнфорд)", "https://cs50.harvard.edu/python/2022/", "2023-08-24T07:45:34"),
    ("Бесплатные курсы по нейронкам от NVIDIA", "https://courses.nvidia.com/courses/course-v1:DLI+S-FX-07+V1/", "2024-03-23T19:24:34"),
    ("Бесплатный курс по ИИ от Imperial College London", "https://neuro4ml.github.io/", "2024-06-09T11:16:03"),
    ("Учебник по промптам от топовых исследователей (arXiv)", "https://arxiv.org/pdf/2406.06608", "2024-08-09T20:08:20"),
    ("База по нейронкам с нуля — плейлист лекций", "https://youtu.be/wjZofJX0v4M", "2024-09-01T09:28:22"),
    ("Бесплатные курсы Йельского университета", "https://oyc.yale.edu/courses", "2024-10-18T09:54:00"),
    ("LLMs-from-scratch — учебник по созданию своей нейронки", "https://github.com/rasbt/LLMs-from-scratch", "2024-11-09T05:41:08"),
    ("Учебник по ИИ-агентам от Google (Kaggle whitepaper)", "https://www.kaggle.com/whitepaper-agents", "2025-01-19T07:44:16"),
    ("Manus — генератор презентаций", "https://manus.im/", "2025-05-31T17:49:39"),
    ("Gemini Deep Research / SciSpace — помощь в написании научных работ", "http://gemini.google.com/", "2025-07-08T08:30:11"),
    ("NotebookLM — генерация видеопрезентаций и подкастов на русском", "https://notebooklm.google/", "2025-08-26T04:36:58"),
    ("Live Resume — сервис для актуального резюме", "https://resumeislive.vercel.app/", "2025-12-14T20:35:13"),
    ("Автоматизация отклика на вакансии hh.ru", "https://github.com/Steev193/hh-ru-apply", "2026-04-06T10:31:38"),
    ("PlanExe — генератор детальных бизнес-планов", "https://app.mach-ai.com/planexe_early_access", "2026-01-22T05:38:18"),
    ("Paperclip — сборка компании из ИИ-сотрудников", "https://github.com/paperclipai/paperclip", "2026-03-07T04:30:40"),
    ("DeepTutor — научный ассистент уровня команды учёных", "https://github.com/HKUDS/DeepTutor", "2026-04-09T03:46:56"),
    ("Knowledge Work Plugins — плагины Claude для замены рутинной работы", "https://github.com/anthropics/knowledge-work-plugins", "2026-05-24T18:45:05"),
    ("Jina Reader — обход пейволов для чтения статей", "https://github.com/jina-ai/reader", "2026-06-08T08:19:40"),
    ("Гайд Anthropic по запуску ИИ-стартапа", "https://situational-awareness.ai/wp-content/uploads/2024/06/situationalawareness.pdf", "2026-06-17T13:06:05"),
    ("Подробный гайд по освоению Claude за выходные", "https://claude.ai/login", "2026-06-28T04:03:28"),
    ("Подкаст Маска с топами Neuralink — 8 часов про ИИ и мозг", "https://www.youtube.com/watch", "2025-01-17T20:19:02"),
    ("Топ шедевров литературы по мнению GPT-5 Pro и Gemini 2.5 Pro", "https://t.me/denissexy/10666", "2025-08-23T21:21:59"),
]


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


def delete_message(chat_id, message_id):
    try:
        api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        pass  # уже удалено вручную, слишком старое или бот не админ — не критично


def setup_bot_ui():
    """Регистрирует список команд и кнопку меню, чтобы они были видны в чате."""
    commands = [
        {"command": "topics", "description": "Список тем со ссылками"},
        {"command": "find", "description": "Поиск по заголовкам: /find слово"},
        {"command": "export", "description": "Выгрузить всё в markdown-файл"},
        {"command": "seed", "description": "Загрузить историческую подборку ссылок"},
        {"command": "start", "description": "Помощь и список команд"},
    ]
    try:
        api_call("setMyCommands", {"commands": json.dumps(commands)})
        api_call("setChatMenuButton", {"menu_button": json.dumps({"type": "commands"})})
        log.info("Command menu registered")
    except Exception as e:
        log.warning("Failed to register command menu: %s", e)


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            chat_id INTEGER PRIMARY KEY,
            last_message_id INTEGER
        )
        """
    )
    return conn


def get_last_bot_message(chat_id):
    conn = db()
    row = conn.execute(
        "SELECT last_message_id FROM bot_state WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def set_last_bot_message(chat_id, message_id):
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO bot_state (chat_id, last_message_id) VALUES (?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET last_message_id=excluded.last_message_id",
            (chat_id, message_id),
        )
    conn.close()


def reply(chat_id, text, reply_markup=None, parse_mode=None, disable_preview=False):
    """Отправляет сообщение и удаляет предыдущее сообщение бота в этом чате,
    чтобы не захламлять историю повторными ответами на команды/кнопки."""
    last_id = get_last_bot_message(chat_id)
    if last_id:
        delete_message(chat_id, last_id)
    resp = send_message(
        chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode, disable_preview=disable_preview
    )
    try:
        set_last_bot_message(chat_id, resp["result"]["message_id"])
    except Exception:
        pass
    return resp


def reply_document(chat_id, file_path, filename):
    last_id = get_last_bot_message(chat_id)
    if last_id:
        delete_message(chat_id, last_id)
    resp = send_document(chat_id, file_path, filename)
    try:
        set_last_bot_message(chat_id, resp["result"]["message_id"])
    except Exception:
        pass
    return resp


def message_link(chat_id, message_id):
    """Ссылка на оригинальное сообщение в группе (работает для супергрупп).
    Возвращает None, если сообщения не существует (например, для /seed —
    там message_id=0, т.к. это исторические записи без привязки к чату)."""
    if not message_id:
        return None
    cid = str(chat_id)
    if cid.startswith("-100"):
        cid = cid[4:]
    elif cid.startswith("-"):
        cid = cid[1:]
    return f"https://t.me/c/{cid}/{message_id}"


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
    reply(
        chat_id,
        "Привет! Я слежу за этим чатом и автоматически раскладываю по темам все "
        "ссылки, которые сюда присылают.\n\n"
        "Команды:\n"
        "/topics — список тем со счётчиком ссылок\n"
        "/find слово — поиск по заголовкам\n"
        "/export — выгрузить всё одним markdown-файлом\n"
        "/seed — один раз загрузить историческую подборку (~70 ссылок, "
        "собранных из переписки до подключения бота)",
    )


def handle_seed(chat_id):
    conn = db()
    already = conn.execute(
        "SELECT COUNT(*) FROM links WHERE chat_id=? AND author='seed'", (chat_id,)
    ).fetchone()[0]
    if already:
        conn.close()
        reply(chat_id, "Историческая подборка уже загружена в этот чат ранее.")
        return
    with conn:
        for title, link, created_at in SEED_DATA:
            category = categorize(title)
            conn.execute(
                "INSERT INTO links (chat_id, message_id, category, title, link, author, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (chat_id, 0, category, title, link, "seed", created_at),
            )
    conn.close()
    reply(chat_id, f"Загрузил {len(SEED_DATA)} ссылок из истории. Наберите /topics, чтобы посмотреть.")


def handle_topics(chat_id):
    conn = db()
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM links WHERE chat_id=? GROUP BY category ORDER BY COUNT(*) DESC",
        (chat_id,),
    ).fetchall()
    conn.close()
    if not rows:
        reply(chat_id, "Пока ничего не сохранено — присылайте ссылки в чат.")
        return
    buttons = [[{"text": f"{cat} ({count})", "callback_data": f"cat:{cat}"}] for cat, count in rows]
    reply(chat_id, "Темы:", reply_markup={"inline_keyboard": buttons})


def handle_category(chat_id, category):
    conn = db()
    rows = conn.execute(
        "SELECT title, link, message_id FROM links WHERE chat_id=? AND category=? ORDER BY id DESC LIMIT 20",
        (chat_id, category),
    ).fetchall()
    conn.close()
    if not rows:
        reply(chat_id, f"В теме «{category}» пока пусто.")
        return
    lines = [f"*{category}*"]
    for title, link, message_id in rows:
        safe_title = title.replace("[", "(").replace("]", ")")
        # Ведём на само сообщение в группе, если оно есть (а не на внешний
        # сайт из ссылки); для исторических /seed-записей message_id=0 —
        # для них оставляем внешнюю ссылку, т.к. привязанного сообщения нет.
        target = message_link(chat_id, message_id) or link
        lines.append(f"- [{safe_title[:80]}]({target})")
    reply(chat_id, "\n".join(lines), parse_mode="Markdown", disable_preview=True)


def handle_find(chat_id, keyword):
    if not keyword:
        reply(chat_id, "Использование: /find слово")
        return
    conn = db()
    rows = conn.execute(
        "SELECT category, title, link, message_id FROM links WHERE chat_id=? AND title LIKE ? ORDER BY id DESC LIMIT 20",
        (chat_id, f"%{keyword}%"),
    ).fetchall()
    conn.close()
    if not rows:
        reply(chat_id, "Ничего не нашлось.")
        return
    lines = []
    for cat, title, link, message_id in rows:
        target = message_link(chat_id, message_id) or link
        lines.append(f"*{cat}* — [{title[:70]}]({target})")
    reply(chat_id, "\n".join(lines), parse_mode="Markdown", disable_preview=True)


def handle_export(chat_id):
    conn = db()
    rows = conn.execute(
        "SELECT category, title, link, created_at FROM links WHERE chat_id=? ORDER BY category, id",
        (chat_id,),
    ).fetchall()
    conn.close()
    if not rows:
        reply(chat_id, "Пока нечего экспортировать.")
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
    reply_document(chat_id, path, "ссылки-по-темам.md")


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
    if text.startswith("/seed"):
        handle_seed(chat_id)
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
    setup_bot_ui()
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
