"""Reporting periods in the company timezone (default America/New_York)."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.utils import timezone

PRESETS = {
    "today": "Today",
    "wtd": "Week to date",
    "mtd": "Month to date",
    "qtd": "Quarter to date",
    "ytd": "Year to date",
    "rolling_30": "Rolling 30 days",
    "rolling_90": "Rolling 90 days",
    "custom": "Custom range",
}


@dataclass
class Period:
    key: str
    start: date  # inclusive
    end: date  # inclusive
    tz: ZoneInfo

    @property
    def start_dt(self):
        return datetime.combine(self.start, time.min, tzinfo=self.tz)

    @property
    def end_dt(self):
        """Exclusive upper bound."""
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=self.tz)

    def contains(self, dt):
        return dt is not None and self.start_dt <= dt < self.end_dt

    @property
    def days(self):
        return (self.end - self.start).days + 1

    def as_dict(self):
        return {
            "key": self.key,
            "label": PRESETS.get(self.key, self.key),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timezone": str(self.tz),
        }


def local_today(tz):
    return timezone.now().astimezone(tz).date()


def resolve_period(key="mtd", start=None, end=None, tz_name="America/New_York"):
    tz = ZoneInfo(tz_name)
    today = local_today(tz)
    key = key if key in PRESETS else "mtd"
    if key == "custom":
        try:
            s = date.fromisoformat(start)
            e = date.fromisoformat(end) if end else today
        except (TypeError, ValueError):
            return resolve_period("mtd", tz_name=tz_name)
        if e < s:
            s, e = e, s
        return Period("custom", s, e, tz)
    if key == "today":
        s = today
    elif key == "wtd":
        s = today - timedelta(days=today.weekday())
    elif key == "qtd":
        s = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    elif key == "ytd":
        s = date(today.year, 1, 1)
    elif key == "rolling_30":
        s = today - timedelta(days=29)
    elif key == "rolling_90":
        s = today - timedelta(days=89)
    else:
        s = today.replace(day=1)
    return Period(key, s, today, tz)


def prior_period(period):
    """Comparable earlier period of the same elapsed length."""
    shift = {
        "mtd": relativedelta(months=1),
        "qtd": relativedelta(months=3),
        "ytd": relativedelta(years=1),
        "wtd": relativedelta(weeks=1),
    }.get(period.key)
    if shift:
        return Period(period.key, period.start - shift, period.end - shift, period.tz)
    length = timedelta(days=period.days)
    return Period(period.key, period.start - length, period.end - length, period.tz)


def business_days(start, end):
    """Mon–Fri days between start and end inclusive."""
    if end < start:
        return 0
    days = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            days += 1
        d += timedelta(days=1)
    return days


def month_bounds(day):
    first = day.replace(day=1)
    last = first + relativedelta(months=1) - timedelta(days=1)
    return first, last


def months_overlapping(start, end):
    m = start.replace(day=1)
    while m <= end:
        yield m
        m = m + relativedelta(months=1)
