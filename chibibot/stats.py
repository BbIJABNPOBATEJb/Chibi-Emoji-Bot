"""Admin statistics: activity, growth and output over time — chart specs and summary numbers.

All times in the database are UTC ISO strings; buckets (hours, days, weeks, months) follow
the bot's time zone so a "day" on the chart is a local calendar day.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from .db import Database
from .kinds import EMOJI, STICKERS, kind_of

UTC = timezone.utc
HOUR = timedelta(hours=1)
SAMPLES = 180          # points on a running-total line
MAX_LABELS = 8         # x-axis labels per chart

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


@dataclass(frozen=True)
class Period:
    key: str
    button: str
    title: str          # "за неделю"
    hours: int | None   # None: since the first user
    prev: str           # what the change is compared to


PERIODS = [
    Period("day", "24 ч", "за 24 часа", 24, "к предыдущим 24 часам"),
    Period("week", "Неделя", "за неделю", 24 * 7, "к прошлой неделе"),
    Period("month", "Месяц", "за месяц", 24 * 30, "к прошлому месяцу"),
    Period("year", "Год", "за год", 24 * 365, "к прошлому году"),
    Period("all", "Всё", "за всё время", None, ""),
]


@dataclass(frozen=True)
class Metric:
    key: str
    button: str
    title: str


METRICS = [
    Metric("active", "👥 Активность", "Активные пользователи"),
    Metric("new", "🆕 Новые", "Новые пользователи"),
    Metric("made", "🎨 Создано", "Новые эмодзи и стикеры"),
    Metric("users", "📈 Всего юзеров", "Всего пользователей"),
    Metric("items", "📦 Всего стикеров", "Всего эмодзи и стикеров"),
]


def num(n: int) -> str:
    """1234567 -> '1 234 567' (no-break spaces)."""
    return f"{n:,}".replace(",", " ")


def _ts(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _hour(key: str) -> datetime:
    """'2026-10-03T19' (UTC) -> aware datetime."""
    return datetime.fromisoformat(key + ":00:00").replace(tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ buckets

class Buckets:
    """Consecutive calendar units (hour/day/week/month) in the bot's time zone, the last one current."""

    STEPS = {"hour": (1, 2, 3, 4, 6, 12, 24), "day": (1, 2, 3, 5, 7, 10, 14, 30),
             "week": (1, 2, 4, 8, 13, 26, 52), "month": (1, 2, 3, 6, 12, 24)}
    WORDS = {"hour": "по часам", "day": "по дням", "week": "по неделям", "month": "по месяцам"}

    def __init__(self, unit: str, first: datetime, now: datetime, tz: tzinfo):
        self.unit, self.tz = unit, tz
        loc = first.astimezone(tz)
        if unit == "hour":
            self.start = first.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        elif unit == "day":
            self.d0 = loc.date()
        elif unit == "week":
            self.d0 = loc.date() - timedelta(days=loc.weekday())
        else:
            self.m0 = loc.year * 12 + loc.month - 1
        self.n = self.index(now) + 1

    def index(self, dt: datetime) -> int:
        if self.unit == "hour":
            return int((dt.astimezone(UTC) - self.start) // HOUR)
        d = dt.astimezone(self.tz).date()
        if self.unit == "day":
            return (d - self.d0).days
        if self.unit == "week":
            return (d - self.d0).days // 7
        return d.year * 12 + d.month - 1 - self.m0

    def begin(self, i: int) -> datetime:
        """Start of bucket i, local time."""
        if self.unit == "hour":
            return (self.start + i * HOUR).astimezone(self.tz)
        if self.unit in ("day", "week"):
            d = self.d0 + timedelta(days=i * (7 if self.unit == "week" else 1))
        else:
            m = self.m0 + i
            d = date(m // 12, m % 12 + 1, 1)
        return datetime.combine(d, time(0), self.tz)

    def label(self, i: int) -> str:
        b = self.begin(i)
        if self.unit == "hour":
            return b.strftime("%d.%m") if b.hour == 0 else b.strftime("%H:00")
        if self.unit == "day":
            return f"{WEEKDAYS[b.weekday()]} {b:%d.%m}" if self.n <= 8 else b.strftime("%d.%m")
        if self.unit == "week":
            return b.strftime("%d.%m")
        return f"{MONTHS[b.month - 1]} {b.year}" if b.month == 1 or i == 0 else MONTHS[b.month - 1]

    def labels(self) -> list[tuple[float, str]]:
        """Every k-th bucket, k picked so at most MAX_LABELS fit; hours on round clock values,
        everything else counted back from the current bucket so it is always labelled."""
        k = next((s for s in self.STEPS[self.unit] if -(-self.n // s) <= MAX_LABELS), self.STEPS[self.unit][-1])
        if self.unit == "hour":
            picked = [i for i in range(self.n) if self.begin(i).hour % k == 0]
        else:
            picked = list(range(self.n - 1, -1, -k))[::-1]
        return [(float(i), self.label(i)) for i in picked]

    @property
    def word(self) -> str:
        return self.WORDS[self.unit]


def buckets_for(period: Period, now: datetime, first: datetime | None, tz: tzinfo) -> Buckets:
    if period.key == "day":
        return Buckets("hour", now - 23 * HOUR, now, tz)
    if period.key == "week":
        return Buckets("day", now - timedelta(days=6), now, tz)
    if period.key == "month":
        return Buckets("day", now - timedelta(days=29), now, tz)
    if period.key == "year":
        return Buckets("week", now - timedelta(weeks=52), now, tz)
    first = min(first or now, now)
    span = now - first
    unit = ("hour" if span <= timedelta(days=3) else "day" if span <= timedelta(days=120)
            else "week" if span <= timedelta(days=3 * 365) else "month")
    return Buckets(unit, first, now, tz)


def window(period: Period, now: datetime, first: datetime | None) -> datetime:
    if period.hours is None:
        return min(first or now - HOUR, now - HOUR)
    return now - timedelta(hours=period.hours)


# ------------------------------------------------------------------ running totals

def _time_labels(t0: datetime, t1: datetime, step: timedelta, tz: tzinfo) -> list[tuple[float, str]]:
    """Round calendar moments between t0 and t1 as fractional sample positions."""
    span = t1 - t0
    out: list[tuple[float, str]] = []

    def add(dt: datetime, text: str) -> None:
        pos = (dt - t0) / step
        if 0 <= pos <= (t1 - t0) / step:
            out.append((pos, text))

    loc0 = t0.astimezone(tz)
    if span <= timedelta(days=3):
        k = next(s for s in (1, 2, 3, 4, 6, 12, 24) if span / timedelta(hours=s) <= MAX_LABELS)
        t = t0.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        while t <= t1:
            loc = t.astimezone(tz)
            if loc.minute == 0 and loc.hour % k == 0:
                add(t, loc.strftime("%d.%m") if loc.hour == 0 else loc.strftime("%H:00"))
            t += HOUR
    elif span <= timedelta(days=120):
        k = next(s for s in (1, 2, 3, 5, 7, 10, 14, 30) if span / timedelta(days=s) <= MAX_LABELS)
        d = loc0.date()
        while True:
            dt = datetime.combine(d, time(0), tz)
            if dt > t1:
                break
            if (k == 7 and d.weekday() == 0) or (k != 7 and d.toordinal() % k == 0):
                add(dt, f"{WEEKDAYS[d.weekday()]} {d:%d.%m}" if k == 1 else d.strftime("%d.%m"))
            d += timedelta(days=1)
    else:
        months = span.days / 30.4
        k = next((s for s in (1, 2, 3, 6, 12, 24, 60) if months / s <= MAX_LABELS), 120)
        m = loc0.year * 12 + loc0.month - 1
        while True:
            d = date(m // 12, m % 12 + 1, 1)
            dt = datetime.combine(d, time(0), tz)
            if dt > t1:
                break
            if m % k == 0:
                add(dt, str(d.year) if d.month == 1 or k >= 12 else MONTHS[d.month - 1])
            m += 1
    return out


def running_total(times: list[datetime], before: int, t0: datetime, t1: datetime) -> list[int]:
    step = (t1 - t0) / (SAMPLES - 1)
    return [before + bisect.bisect_right(times, t0 + j * step) for j in range(SAMPLES)]


# ------------------------------------------------------------------ the dashboard

@dataclass
class Summary:
    active: int
    active_prev: int | None
    creators: int
    creators_prev: int | None
    new_users: int
    new_users_prev: int | None
    made: int
    made_prev: int | None
    users_total: int
    creators_total: int
    made_total: int
    in_packs: dict[str, int]
    packs: int
    published: int


def _change(cur: int, prev: int | None) -> str:
    if prev is None:
        return ""
    if prev == 0:
        return " · было 0" if cur else ""
    pct = round((cur - prev) * 100 / prev)
    if pct == 0:
        return " · без изменений"
    return f" · {'▲' if pct > 0 else '▼'} {abs(pct)}%"


def caption(period: Period, s: Summary) -> str:
    head = f"📊 <b>Статистика {period.title}</b>"
    if s.active_prev is not None:
        head += f"\n<i>стрелки — изменение {period.prev}</i>"
    e, st = kind_of(EMOJI), kind_of(STICKERS)
    return "\n".join([
        head, "",
        f"👥 Писали боту: <b>{num(s.active)}</b>{_change(s.active, s.active_prev)}",
        f"🎨 Создавали: <b>{num(s.creators)}</b>{_change(s.creators, s.creators_prev)}",
        f"🆕 Новых пользователей: <b>{num(s.new_users)}</b>{_change(s.new_users, s.new_users_prev)}",
        f"🖼 Создано эмодзи и стикеров: <b>{num(s.made)}</b>{_change(s.made, s.made_prev)}",
        "",
        f"👤 Всего пользователей: <b>{num(s.users_total)}</b>, хоть раз создавали: <b>{num(s.creators_total)}</b>",
        f"📦 Сейчас в паках: <b>{num(sum(s.in_packs.values()))}</b> ({e.icon} {num(s.in_packs.get(EMOJI, 0))} · "
        f"{st.icon} {num(s.in_packs.get(STICKERS, 0))}), создано за всё время: <b>{num(s.made_total)}</b>",
        f"🗂 Паков: <b>{num(s.packs)}</b>, опубликовано: <b>{num(s.published)}</b>",
    ])


async def build(db: Database, tz: tzinfo | None, metric: int, period: int,
                now: datetime | None = None) -> tuple[dict, str]:
    """-> (chart spec for charts.render, HTML caption)."""
    tz = tz or UTC
    m = METRICS[max(0, min(metric, len(METRICS) - 1))]
    p = PERIODS[max(0, min(period, len(PERIODS) - 1))]
    now = (now or datetime.now(UTC)).astimezone(UTC)
    first_iso = await db.first_seen()
    first = _ts(first_iso) if first_iso else None
    t0 = window(p, now, first)
    b = buckets_for(p, now, first, tz)
    chart_from = min(t0, b.begin(0).astimezone(UTC))
    # compare with the period before only when the bot already existed for all of it
    prev_from = t0 - (now - t0) if p.hours is not None else None
    if prev_from is not None and (first is None or prev_from < first):
        prev_from = None
    data_from = min(chart_from, prev_from or chart_from)

    # raw rows, enough for the chart, the period and the period before it
    users = [_ts(x) for x in await db.user_times()]
    items = [(_ts(ts), uid) for ts, uid in await db.item_times(_iso(data_from))]
    # making something is activity too (a long upload can run past the hour it was started in)
    activity = [(_hour(h), uid) for h, uid in await db.activity_since(_iso(data_from)[:13])] + items
    creators = [_ts(x) for x in await db.creator_times()]

    def distinct(rows, a: datetime, z: datetime) -> int:
        return len({uid for t, uid in rows if a <= t < z})

    def between(times, a: datetime, z: datetime) -> int:
        return bisect.bisect_left(times, z) - bisect.bisect_left(times, a)

    end = now + timedelta(seconds=1)
    # an activity hour counts for the window if it overlaps it
    act_from = t0.replace(minute=0, second=0, microsecond=0)
    in_packs = await db.items_by_kind()
    packs, published = await db.count_packs_published()
    made_total = await db.count_items_before(_iso(end))
    item_t = [t for t, _ in items]
    s = Summary(
        active=distinct(activity, act_from, end),
        active_prev=distinct(activity, prev_from.replace(minute=0, second=0, microsecond=0), act_from)
        if prev_from else None,
        creators=distinct(items, t0, end),
        creators_prev=distinct(items, prev_from, t0) if prev_from else None,
        new_users=between(users, t0, end),
        new_users_prev=between(users, prev_from, t0) if prev_from else None,
        made=between(item_t, t0, end),
        made_prev=between(item_t, prev_from, t0) if prev_from else None,
        users_total=len(users), creators_total=len(creators), made_total=made_total,
        in_packs=in_packs, packs=packs, published=published,
    )

    spec: dict = {"title": m.title}
    if m.key in ("active", "new", "made"):
        def per_bucket(rows, users_only: bool) -> list[int]:
            if users_only:
                sets: list[set] = [set() for _ in range(b.n)]
                for t, uid in rows:
                    i = b.index(t)
                    if 0 <= i < b.n:
                        sets[i].add(uid)
                return [len(x) for x in sets]
            out = [0] * b.n
            for t in rows:
                i = b.index(t)
                if 0 <= i < b.n:
                    out[i] += 1
            return out

        unit = {"hour": "за час", "day": "за день", "week": "за неделю", "month": "за месяц"}[b.unit]
        if m.key == "active":
            series = [("Писали боту", per_bucket(activity, True), num(s.active)),
                      ("Создавали", per_bucket(items, True), num(s.creators))]
            spec["subtitle"] = f"{p.title} · {b.word} · сколько человек {unit}"
        elif m.key == "new":
            series = [("Новые пользователи", per_bucket(users, False), num(s.new_users)),
                      ("Новые авторы", per_bucket(creators, False), num(between(creators, t0, end)))]
            spec["subtitle"] = f"{p.title} · {b.word} · авторы — сделали первый эмодзи или стикер"
        else:
            series = [("Создано", per_bucket(item_t, False), "")]
            spec["subtitle"] = f"{p.title} · {b.word} · всего {num(s.made)}"
        spec.update(n=b.n, labels=b.labels(), markers=b.n <= 40, peak=True)
    else:
        t1 = now
        a = chart_from if p.hours is None else t0
        step = (t1 - a) / (SAMPLES - 1)
        if m.key == "users":
            series = [("Писали боту", running_total(users, 0, a, t1), ""),
                      ("Хоть раз создавали", running_total(creators, 0, a, t1), "")]
            spec["subtitle"] = f"{p.title} · нарастающим итогом"
        else:
            before = await db.count_items_before(_iso(a))
            series = [("Создано", running_total([t for t in item_t if t >= a], before, a, t1), "")]
            spec["subtitle"] = (f"{p.title} · нарастающим итогом, удалённые тоже · сейчас в паках "
                                f"{num(sum(in_packs.values()))}")
        for i, (name, vals, _) in enumerate(series):
            series[i] = (name, vals, num(vals[-1]))
        spec.update(n=SAMPLES, labels=_time_labels(a, t1, step, tz), markers=False, ends=True)
    spec["series"] = [{"name": n, "values": v, "total": tot} for n, v, tot in series]
    return spec, caption(p, s)
