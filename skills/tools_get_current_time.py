from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_current_time(
    fmt: str = "%Y-%m-%dT%H:%M:%S",
    tz: str | None = None,
    as_dict: bool = False,
) -> str | dict:
    """
    Return the current date and time as a formatted string or a component dictionary.

    Parameters
    ----------
    fmt : str, optional
        A :func:`datetime.strftime` format string used when ``as_dict`` is ``False``.
        Defaults to ``"%Y-%m-%dT%H:%M:%S"`` (ISO-8601-like).
    tz : str or None, optional
        IANA timezone identifier (e.g. ``"UTC"``, ``"America/New_York"``).
        When *None* the local system timezone is used.
    as_dict : bool, optional
        When ``True`` return a dictionary with individual datetime components
        instead of a formatted string.  Defaults to ``False``.

    Returns
    -------
    str
        Formatted datetime string when ``as_dict`` is ``False``.
    dict
        Dictionary with keys ``year``, ``month``, ``day``, ``hour``, ``minute``,
        ``second``, ``microsecond``, and ``tzinfo`` when ``as_dict`` is ``True``.

    Raises
    ------
    ValueError
        If *tz* is not a recognised IANA timezone identifier.

    Examples
    --------
    >>> get_current_time()
    '2024-05-15T13:45:00'

    >>> get_current_time(fmt="%Y-%m-%d %H:%M:%S", tz="UTC")
    '2024-05-15 13:45:00'

    >>> get_current_time(tz="America/New_York", as_dict=True)
    {'year': 2024, 'month': 5, 'day': 15, 'hour': 9, 'minute': 45,
     'second': 0, 'microsecond': 123456, 'tzinfo': 'America/New_York'}
    """
    if tz is None:
        tz_obj = datetime.now().astimezone().tzinfo
    else:
        try:
            tz_obj = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"Unknown timezone: {tz!r}. "
                "Please supply a valid IANA timezone identifier such as "
                "'UTC' or 'America/New_York'."
            )

    now = datetime.now(tz=tz_obj)

    if as_dict:
        return {
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
            "minute": now.minute,
            "second": now.second,
            "microsecond": now.microsecond,
            "tzinfo": str(now.tzinfo),
        }

    return now.strftime(fmt)


if __name__ == "__main__":
    print("Default (local time, ISO-8601 format):")
    print(get_current_time())
    print()

    print("UTC time with a custom format:")
    print(get_current_time(fmt="%Y-%m-%d %H:%M:%S", tz="UTC"))
    print()

    print("New York time as a dictionary:")
    print(get_current_time(tz="America/New_York", as_dict=True))
    print()

    print("Local time as a dictionary:")
    print(get_current_time(as_dict=True))