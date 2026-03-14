
from typing import Optional
from pydantic import BaseModel, HttpUrl, EmailStr


class Lead(BaseModel):
    name: str
    title: str
    company: str
    website: Optional[HttpUrl] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    linkedin: Optional[HttpUrl] = None
    source_url: Optional[HttpUrl] = None


class EmailOutreach(BaseModel):
    subject: str
    body: str
