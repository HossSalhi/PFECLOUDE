from datetime import datetime, timedelta, timezone

# Defence-in-depth cap. The API schemas already bound window_minutes (predict ≤120,
# correlate/proactive ≤60), but this shared util must stay safe for any internal caller
# so an absurd window can never trigger a year-long Loki/Prometheus range scan on .160.
MAX_WINDOW_MINUTES = 1440  # 24h


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_window(window_minutes: int, end_time: datetime | None) -> tuple[datetime, datetime]:
    if window_minutes <= 0:
        raise ValueError("window_minutes must be greater than 0")
    window_minutes = min(window_minutes, MAX_WINDOW_MINUTES)

    end = ensure_utc(end_time) if end_time else datetime.now(timezone.utc)
    start = end - timedelta(minutes=window_minutes)
    return start, end
