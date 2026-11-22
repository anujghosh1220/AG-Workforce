import math
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> "Page[T]":
        return cls(items=items, total=total, page=page, page_size=page_size,
                   pages=math.ceil(total / page_size) if page_size else 0)


def paginate(db: Session, query: select, page: int = 1, page_size: int = 25):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    count_q = select(func.count()).select_from(query.order_by(None).subquery())
    total = db.scalar(count_q) or 0
    stmt = query.offset((page - 1) * page_size).limit(page_size)
    items = db.scalars(stmt).all()
    return items, total, page, page_size
