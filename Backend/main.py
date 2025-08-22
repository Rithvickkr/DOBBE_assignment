from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from langchain_together import ChatTogether
from langchain.agents import initialize_agent, AgentType
from langchain.tools import tool
from db import SessionLocal, Doctor, Appointment, User, PromptHistory
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import googleapiclient.discovery
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from email.mime.text import MIMEText
import base64
from slack_sdk import WebClient
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

# Auth setup
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

class Token(BaseModel):
    access_token: str
    token_type: str

class UserLogin(BaseModel):
    email: str
    role: str

def verify_user(email: str, password: str):
    with SessionLocal() as session:
        user = session.query(User).filter(User.email == email).first()
        if user and pwd_context.verify(password, user.password):
            return user
        return None

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=30)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        role: str = payload.get("role")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return UserLogin(email=email, role=role)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Together AI LLM setup
llm = ChatTogether(
    together_api_key=os.getenv("TOGETHER_API_KEY") or "missing-key",
    model=os.getenv("TOGETHER_MODEL", "lgai/exaone-3-5-32b-instruct")
)

# Google API setup
SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/gmail.send']
creds = None
if os.path.exists('token.json'):
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
else:
    flow = InstalledAppFlow.from_client_secrets_file(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"), SCOPES)
    creds = flow.run_local_server(port=0)
    with open('token.json', 'w') as token:
        token.write(creds.to_json())
calendar_service = googleapiclient.discovery.build('calendar', 'v3', credentials=creds)
gmail_service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)

# MCP Tools
@tool
def check_availability(doctor_name_and_date: str):
    """Check doctor's availability on a date. 
    Args:
        doctor_name_and_date: A string containing doctor name and date separated by comma, e.g., Dr. Ahuja, 2025-08-23
    Returns:
        Available time slots for the doctor on that date
    """
    try:
        parts = doctor_name_and_date.split(',')
        if len(parts) != 2:
            return "Please provide doctor name and date separated by comma, e.g., Dr. Ahuja, 2025-08-23"
        
        doctor_name = parts[0].strip()
        date = parts[1].strip()
        print(f"Checking availability for {doctor_name} on {date}")

        with SessionLocal() as session:
            doctor = session.query(Doctor).filter(Doctor.name == doctor_name).first()
            if not doctor:
                return f"Doctor {doctor_name} not found."
            slots = doctor.availability.get(date, [])
            if slots:
                return f"Available slots for {doctor_name} on {date}: {', '.join(slots)}"
            else:
                return f"No available slots for {doctor_name} on {date}"
    except Exception as e:
        return f"Error checking availability: {str(e)}"

@tool
def book_appointment(appointment_details: str):
    """Book an appointment for a patient.
    Args:
        appointment_details: String with format 'doctor_name, date, time, patient_email, reason' 
                           e.g., 'Dr. Ahuja, 2025-08-23, 9AM-10AM, patient@email.com, checkup'
    Returns:
        Booking confirmation or error message
    """
    try:
        parts = [p.strip() for p in appointment_details.split(',')]
        if len(parts) != 5:
            return "Please provide: doctor_name, date, time, patient_email, reason separated by commas"
        
        doctor_name, date, time, patient_email, reason = parts
        
        with SessionLocal() as session:
            doctor = session.query(Doctor).filter(Doctor.name == doctor_name).first()
            if not doctor:
                return f"Doctor {doctor_name} not found."
            
            available_slots = doctor.availability.get(date, [])
            if time not in available_slots:
                if available_slots:
                    return f"Slot {time} on {date} unavailable. Available slots: {', '.join(available_slots)}"
                else:
                    return f"No slots available for {doctor_name} on {date}"
            
            # Book the appointment
            appt = Appointment(
                doctor_id=doctor.id, 
                patient_email=patient_email, 
                date=date, 
                time=time, 
                reason=reason
            )
            session.add(appt)
            
            # Remove the booked slot
            doctor.availability[date].remove(time)
            session.commit()
            
            # Add to Google Calendar
            try:
                start_time = f"{date}T{time.split('-')[0].replace('AM', '').replace('PM', '').zfill(2)}:00+05:30"
                end_time = f"{date}T{time.split('-')[1].replace('AM', '').replace('PM', '').zfill(2)}:00+05:30"
                event = {
                    'summary': f'Appointment with {patient_email}',
                    'start': {'dateTime': start_time, 'timeZone': 'Asia/Kolkata'},
                    'end': {'dateTime': end_time, 'timeZone': 'Asia/Kolkata'}
                }
                calendar_service.events().insert(calendarId='primary', body=event).execute()
            except:
                pass  # Calendar booking is optional
            
            return f"Appointment booked successfully for {patient_email} with {doctor_name} on {date} at {time}"
            
    except Exception as e:
        return f"Booking failed: {str(e)}"

@tool
def query_stats(query_type: str):
    """Query appointment statistics.
    Args:
        query_type: Type of query - 'patients_yesterday', 'appointments_today', or 'fever_patients'
    Returns:
        Statistical information about appointments
    """
    try:
        with SessionLocal() as session:
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            if query_type == "patients_yesterday":
                count = session.query(Appointment).filter(Appointment.date == yesterday).count()
                return f"{count} patients visited yesterday."
            elif query_type == "appointments_today":
                count = session.query(Appointment).filter(Appointment.date == today).count()
                return f"{count} appointments today."
            elif query_type == "fever_patients":
                count = session.query(Appointment).filter(Appointment.reason.ilike("%fever%")).count()
                return f"{count} patients with fever-related appointments."
            else:
                return "Unknown query type. Use: patients_yesterday, appointments_today, or fever_patients"
    except Exception as e:
        return f"Error querying stats: {str(e)}"

# Agent setup
tools = [check_availability, book_appointment, query_stats]
agent = initialize_agent(
    tools=tools,
    llm=llm,
    agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=3,
    agent_kwargs={
        "prefix": """You are a medical assistant agent that helps with doctor appointments. You have access to the following tools:

1. check_availability: Check a doctor's availability on a specific date
   - Input format: 'Doctor Name, YYYY-MM-DD' (e.g., 'Dr. Ahuja, 2025-08-23')
   
2. book_appointment: Book an appointment 
   - Input format: 'Doctor Name, YYYY-MM-DD, Time, Email, Reason' (e.g., 'Dr. Ahuja, 2025-08-23, 9AM-10AM, patient@email.com, checkup')
   
3. query_stats: Get appointment statistics
   - Input format: 'patients_yesterday', 'appointments_today', or 'fever_patients'

Always use the exact format specified for each tool. If the user's request is unclear, ask for clarification.""",
        "format_instructions": """Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [check_availability, book_appointment, query_stats]
Action Input: the input to the action (follow the exact format for each tool)
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question""",
        "suffix": """Begin!

Question: {input}
Thought:{agent_scratchpad}"""
    }
)

# FastAPI endpoints
class Prompt(BaseModel):
    text: str
    session_id: str

@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = verify_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    logger.info(f"User logged in: {user.email}")
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserLogin)
async def get_current_user(current_user: UserLogin = Depends(get_current_user)):
    return current_user

@app.post("/process_prompt")
async def process_prompt(prompt: Prompt, current_user: UserLogin = Depends(get_current_user)):
    text = prompt.text.strip()
    lower_text = text.lower()
    logger.info(f"Processing prompt: {text}")
    
    # Only doctors can request stats
    if any(keyword in lower_text for keyword in ["patients yesterday", "appointments today", "fever patients", "stats", "statistics"]) and current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors can access statistical queries")
    
    try:
        # Call agent with just the input text
        agent_response = agent.invoke({"input": text})
        response = agent_response["output"] if "output" in agent_response else str(agent_response)
        
        # Save prompt history
        with SessionLocal() as session:
            history = PromptHistory(
                session_id=prompt.session_id,
                user_email=current_user.email,
                prompt_text=text,
                response_text=str(response)
            )
            session.add(history)
            session.commit()
        
        # Send stats to Slack if needed
        if any(x in lower_text for x in ["patients", "appointments", "fever"]):
            try:
                slack_token = os.getenv("SLACK_TOKEN")
                if slack_token and slack_token != "missing-slack-token":
                    slack_client = WebClient(token=slack_token)
                    slack_client.chat_postMessage(channel="#doctor-reports", text=str(response))
            except Exception as slack_error:
                logger.warning(f"Failed to send to Slack: {slack_error}")
        
        return {"response": response}
        
    except Exception as e:
        logger.error(f"Agent error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

@app.get("/prompt_history")
async def get_prompt_history(current_user: UserLogin = Depends(get_current_user)):
    with SessionLocal() as session:
        history = session.query(PromptHistory).filter(PromptHistory.user_email == current_user.email).all()
        return [{"prompt": h.prompt_text, "response": h.response_text, "created_at": h.created_at} for h in history]