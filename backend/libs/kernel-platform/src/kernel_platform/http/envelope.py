"""The envelope of a successful API response."""

from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    """`data` is the payload; `meta` is an object, empty when there is nothing
    to add (pagination of list endpoints)."""

    data: T
    meta: dict[str, object] = {}
