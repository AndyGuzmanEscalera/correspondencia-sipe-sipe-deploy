from calendar import monthrange
from datetime import date, timedelta


def period_for(year, month, cutoff_day):
    """The named month ends on cutoff; consecutive periods never overlap.

    Returns [start, end_exclusive). A cutoff of 31 clamps to each month's last day.
    No policy is selected or seeded before the institution confirms its rules.
    """
    if not 1 <= cutoff_day <= 31:
        raise ValueError("Invalid cutoff day")
    previous_year, previous_month = (year - 1, 12) if month == 1 else (year, month - 1)
    previous_cutoff = date(previous_year, previous_month,
                           min(cutoff_day, monthrange(previous_year, previous_month)[1]))
    cutoff = date(year, month, min(cutoff_day, monthrange(year, month)[1]))
    return previous_cutoff + timedelta(days=1), cutoff + timedelta(days=1)
