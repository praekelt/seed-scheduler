import random

from django.conf import settings


def get_available_metrics():
    available_metrics = []
    available_metrics.extend(settings.METRICS_REALTIME)
    available_metrics.extend(settings.METRICS_SCHEDULED)

    return available_metrics


def calculate_retry_delay(attempt, max_delay=300):
    """Calculates an exponential backoff for retry attempts with a small
    amount of jitter."""
    delay = int(random.uniform(2, 4) ** attempt)
    if delay > max_delay:
        # After reaching the max delay, stop using expontential growth
        # and keep the delay nearby the max.
        delay = int(random.uniform(max_delay - 20, max_delay + 20))
    return delay
