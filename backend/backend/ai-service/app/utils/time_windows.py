from datetime import datetime, timedelta, timezone


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_window(window_minutes: int, end_time: datetime | None) -> tuple[datetime, datetime]:
    if window_minutes <= 0:
        raise ValueError("window_minutes must be greater than 0")

    end = ensure_utc(end_time) if end_time else datetime.now(timezone.utc)
    start = end - timedelta(minutes=window_minutes)
    return start, end
