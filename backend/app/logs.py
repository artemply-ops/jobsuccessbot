"""Логирование: всё в stdout (docker logs) + ошибки и предупреждения в файл с ротацией.

В лог не пишем тексты резюме, вакансий и ответы модели: только id, типы ошибок и короткие сообщения.
Файл: LOG_DIR/errors.log, 5 файлов по 5 МБ. На сервере LOG_DIR лежит в томе files, переживает пересборку.
"""

import logging
from logging.handlers import RotatingFileHandler

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_done = False


def setup_logging() -> None:
    global _done
    if _done:
        return
    from app.config import get_settings

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(console)
    try:
        log_dir = get_settings().log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "errors.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.WARNING)
        fh.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(fh)
    except OSError as e:  # нет прав на папку: работаем только со stdout
        logging.getLogger(__name__).warning("Файл логов недоступен: %s", e)
    for noisy in ("httpx", "httpcore", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _done = True
