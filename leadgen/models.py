
from typing import Optional
from pydantic import BaseModel


class Lead(BaseModel):
    name: str
    title: str
    company: str
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    linkedin: Optional[str] = None
    source_url: Optional[str] = None


class EmailOutreach(BaseModel):
    subject: str
    body: str
