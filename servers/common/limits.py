"""Row caps and pagination shared by all servers."""

MAX_ROWS = 20  # most rows any tool returns in one call
MIN_GROUP_SIZE = 5  # aggregates over fewer rows are suppressed (no single record can be inferred)


def clamp_offset(offset: int | None) -> int:
    return max(0, int(offset or 0))


def page_info(total: int, offset: int, returned: int) -> dict:
    """Pagination block: the caller asks again with next_offset to get the next page."""
    has_more = offset + returned < total
    return {
        "total": total,
        "returned": returned,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + returned if has_more else None,
    }
