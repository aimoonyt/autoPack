import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    webapp_url: str
    host: str
    port: int
    db_path: str
    default_emoji: str


def load_config() -> Config:
    _load_dotenv(Path(".env"))
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")
    return Config(
        bot_token=token,
        webapp_url=os.environ.get("WEBAPP_URL", "").strip(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        db_path=os.environ.get("DB_PATH", "data/bot.db"),
        default_emoji=os.environ.get("DEFAULT_EMOJI", "🙂") or "🙂",
    )
