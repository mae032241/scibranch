"""Arithmetic for nullable global ceilings; ``None`` means unlimited."""


def remaining(limit, consumed=0):
    return None if limit is None else max(0, limit - consumed)


def minimum(*limits):
    finite = [limit for limit in limits if limit is not None]
    return min(finite) if finite else None


def reached(remaining, floor=0):
    return remaining is not None and remaining <= floor
