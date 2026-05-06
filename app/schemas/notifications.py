from pydantic import BaseModel


class PrefUpdate(BaseModel):
    channel: str
    type: str
    enabled: bool
