from sqlalchemy import create_engine, Column, Integer, String, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from sqlalchemy import Column, Integer, String
from passlib.context import CryptContext
from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime, timezone

load_dotenv()

# Database connection
DB_URL = os.getenv("DB_URL")
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Doctor table
class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    availability = Column(JSON)  # e.g., {"2025-08-23": ["9AM-10AM", "10AM-11AM"]}

# Appointment table
class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, index=True)
    patient_email = Column(String)
    date = Column(String)  # YYYY-MM-DD
    time = Column(String)  # e.g., "9AM-10AM"
    reason = Column(String)  # e.g., "fever"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)  # Hashed
    role = Column(String)  # "patient" or "doctor"
    
class PromptHistory(Base):
         __tablename__ = "prompts"
         id = Column(Integer, primary_key=True, index=True)
         session_id = Column(String, index=True)
         user_email = Column(String)
         prompt_text = Column(String)
         response_text = Column(String)
         created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
# Create tables
Base.metadata.create_all(bind=engine)

# Seed initial data
def seed_data():
         with SessionLocal() as session:
             if not session.query(Doctor).first():
                 doctor = Doctor(
                     name="Dr. Ahuja",
                     availability={"2025-08-23": ["9AM-10AM", "10AM-11AM", "3PM-4PM"], "2025-08-24": ["1PM-2PM", "2PM-3PM"]}
                 )
                 session.add(doctor)
             if not session.query(User).first():
                 # Add sample users
                 session.add_all([
                     User(email="patient@example.com", password=pwd_context.hash("patient123"), role="patient"),
                     User(email="ahuja@example.com", password=pwd_context.hash("doctor123"), role="doctor")
                 ])
             session.commit()