"""时间工具

全项目统一使用 UTC 时间。MySQL 的 DATETIME 类型不携带时区信息，
因此这里统一返回 naive（无 tzinfo）的 UTC datetime，避免
"部分列带时区、部分不带" 导致的比较与排序错乱。

Python 3.12 起 datetime.utcnow() 已被弃用，故统一走本模块。
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时间的 naive datetime"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """把任意 datetime 归一化为 naive UTC

    - naive 输入：视为已经是 UTC，原样返回
    - aware 输入：转换为 UTC 后剥离 tzinfo
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
