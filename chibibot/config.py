from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _available_cpus() -> int:
    """Cores this process may actually use (respects Docker --cpuset and taskset on Linux)."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # Windows / macOS
        return max(1, os.cpu_count() or 1)


def _render_workers(value: str | None) -> int:
    """RENDER_WORKERS: a number, or empty/'auto' for one process per core (at most 4)."""
    value = (value or "").strip().lower()
    if value.isdigit() and int(value) > 0:
        return int(value)
    return min(4, _available_cpus())


def _ints(value: str) -> set[int]:
    out = set()
    for part in value.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


@dataclass(frozen=True)
class Config:
    token: str
    admin_ids: set[int]
    max_packs_per_user: int
    daily_emoji_limit: int
    max_drafts: int
    max_batch_user: int
    max_batch_admin: int
    data_dir: Path
    timezone: str
    render_workers: int
    title_suffix: str = "auto"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.sqlite3"

    @property
    def skins_dir(self) -> Path:
        return self.data_dir / "skins"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан: скопируйте .env.example в .env и впишите токен")
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    cfg = Config(
        token=token,
        admin_ids=_ints(os.getenv("ADMIN_IDS", "")),
        max_packs_per_user=int(os.getenv("MAX_PACKS_PER_USER", "5")),
        daily_emoji_limit=int(os.getenv("DAILY_EMOJI_LIMIT", "150")),
        max_drafts=int(os.getenv("MAX_EMPTY_PACKS", "3")),
        max_batch_user=int(os.getenv("MAX_BATCH_USER", "50")),
        max_batch_admin=int(os.getenv("MAX_BATCH_ADMIN", "200")),
        data_dir=data_dir,
        timezone=os.getenv("TIMEZONE", "Europe/Moscow"),
        render_workers=_render_workers(os.getenv("RENDER_WORKERS")),
        title_suffix=os.getenv("TITLE_SUFFIX", "auto"),
    )
    for d in (cfg.data_dir, cfg.skins_dir, cfg.thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
