from sqlalchemy import create_engine, Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from passlib.context import CryptContext
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

# Database connection
DB_URL = os.getenv("DB_URL")
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------- MODELS ----------------------

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    password = Column(String) 
    role = Column(String)  

   
    doctor = relationship("Doctor", back_populates="user", uselist=False)


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    name = Column(String, index=True)
    availability = Column(JSON, default={})  

    user = relationship("User", back_populates="doctor")
    appointments = relationship("Appointment", back_populates="doctor")


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), index=True)
    patient_name = Column(String)
    patient_email = Column(String)
    date = Column(String)  
    time = Column(String)  
    reason = Column(String)  

    doctor = relationship("Doctor", back_populates="appointments")


class PromptHistory(Base):
    __tablename__ = "prompts"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    user_email = Column(String)
    prompt_text = Column(String)
    response_text = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def seed_data():
    """Seed initial demo data"""
    with SessionLocal() as session:
        if not session.query(User).first():
            
            patient = User(
                name="Sample Patient",
                email="patient@example.com",
                password=pwd_context.hash("patient123"),
                role="patient"
            )
            
            doctor_user = User(
                name="Dr. Ahuja",
                email="ahuja@example.com",
                password=pwd_context.hash("doctor123"),
                role="doctor"
            )
            session.add_all([patient, doctor_user])
            session.commit()
            session.refresh(doctor_user)

           
            doctor = Doctor(
                user_id=doctor_user.id,
                name="Dr. Ahuja",
                availability={"2025-08-23": ["9AM-10AM", "10AM-11AM", "3PM-4PM"],
                              "2025-08-24": ["1PM-2PM", "2PM-3PM"]}
            )
            session.add(doctor)
            session.commit()


# ---------------------- INIT ----------------------
Base.metadata.create_all(bind=engine)
seed_data()
