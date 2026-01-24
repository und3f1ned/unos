from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from ..database import Base


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True, nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    success = Column(Boolean, default=False)
    failure_reason = Column(String, nullable=True)
    timestamp = Column(DateTime, server_default=func.now())
