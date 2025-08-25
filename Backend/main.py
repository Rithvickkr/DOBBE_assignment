from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from langchain_together import ChatTogether
from langchain.agents import initialize_agent, AgentType
from langchain.tools import tool
from langchain.memory import ConversationBufferMemory
from db import SessionLocal, Doctor, Appointment, User, PromptHistory
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import Optional
import googleapiclient.discovery
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from slack_sdk import WebClient
import logging
from sqlalchemy.orm.attributes import flag_modified

from email.mime.text import MIMEText
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth setup
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

class Token(BaseModel):
    access_token: str
    token_type: str

class UserLogin(BaseModel):
    name: str
    email: str
    password: str
    role: str = "patient"

class CurrentUser(BaseModel):
    email: str
    role: str
    name: Optional[str] = None
    
def verify_user(email: str, password: str, name: str = None, role: str = "patient"):
    with SessionLocal() as session:
        user = session.query(User).filter(User.email == email).first()
        if user:
            if pwd_context.verify(password, user.password):
                return user
            else:
                return None
        else:
            new_user = User(
                name=name if name else email.split("@")[0],
                email=email,
                password=pwd_context.hash(password),
                role=role
            )
            session.add(new_user)
            session.commit()
            session.refresh(new_user)
            if role == "doctor":
                doctor = Doctor(user_id=new_user.id, name=new_user.name, availability={})
                session.add(doctor)
                session.commit()
                session.refresh(doctor)
            return new_user

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
        name: str = payload.get("name")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return CurrentUser(email=email, role=role, name=name)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# AI LLM setup
llm = ChatTogether(
    together_api_key=os.getenv("TOGETHER_API_KEY") or "missing-key",
    model=os.getenv("TOGETHER_MODEL")
    # "lgai/exaone-3-5-32b-instruct"
)

# Google API setup
SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/gmail.send']
creds = None
if os.path.exists('token.json'):
    try:
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        if not creds.valid:
            creds.refresh(Request())
    except Exception as e:
        print(f"Token refresh failed: {e}")
        creds = None

if not creds or not creds.valid:
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json"), 
            SCOPES
        )
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    except Exception as e:
        print(f"Google OAuth setup failed: {e}")
        creds = None

calendar_service = None
gmail_service = None
if creds and creds.valid:
    try:
        calendar_service = googleapiclient.discovery.build('calendar', 'v3', credentials=creds)
        gmail_service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)
    except Exception as e:
        print(f"Google API service initialization failed: {e}")

# Function to send confirmation email using Gmail API
def send_confirmation_email(patient_email: str, patient_name: str, doctor_name: str, date: str, time: str, reason: str):
    """Send a confirmation email to the patient using Gmail API."""
    try:
        if not gmail_service:
            return "Gmail service unavailable."
        
        message = MIMEText(
            f"Dear {patient_name},\n\n"
            f"Your appointment with {doctor_name} on {date} at {time} for {reason} has been confirmed.\n\n"
            f"Thank you,\nMedical Assistant Team"
        )
        message['to'] = patient_email
        message['from'] = 'me' 
        message['subject'] = 'Appointment Confirmation'
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        gmail_service.users().messages().send(userId='me', body={'raw': raw_message}).execute()
        return "Confirmation email sent successfully."
    except Exception as e:
        return f"Failed to send confirmation email: {str(e)}"

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
        
        doctor_name = parts[0].strip().strip("'\"").strip()
        date = parts[1].strip().strip("'\"").strip()
        
        print(f"Checking availability for '{doctor_name}' on '{date}'")

        with SessionLocal() as session:
            doctor = session.query(Doctor).filter(Doctor.name == doctor_name).first()
            if not doctor:
                return f"Doctor '{doctor_name}' not found."
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
        appointment_details: String with format 'doctor_name, date, time, patient_email, patient_name, reason' 
                           e.g., Dr. Ahuja, 2025-08-23, 9AM-10AM, patient@email.com, John Doe, checkup
    Returns:
        Booking confirmation or error message
    """
    try:
        parts = [p.strip().strip("'\"").strip() for p in appointment_details.split(',')]
        if len(parts) != 6:
            return "Please provide: doctor_name, date, time, patient_email, patient_name, reason separated by commas"

        doctor_name, date, time, patient_email, patient_name, reason = parts
        
        print(f"Booking appointment for '{doctor_name}' on '{date}' at '{time}' for '{patient_name}'")

        with SessionLocal() as session:
            doctor = session.query(Doctor).filter(Doctor.name == doctor_name).first()
            if not doctor:
                return f"Doctor '{doctor_name}' not found."
            
            available_slots = doctor.availability.get(date, [])
            if time not in available_slots:
                if available_slots:
                    return f"Slot {time} on {date} unavailable. Available slots: {', '.join(available_slots)}"
                else:
                    return f"No slots available for {doctor_name} on {date}"
            
            appt = Appointment(
                doctor_id=doctor.id, 
                patient_email=patient_email, 
                patient_name=patient_name,
                date=date, 
                time=time, 
                reason=reason
            )
            session.add(appt)

            if date in doctor.availability and time in doctor.availability[date]:
                doctor.availability[date].remove(time)
                flag_modified(doctor, 'availability')
                session.commit()
            else:
                print(f"Time slot {time} not found for {date}")

            # Google Calendar
            try:
                if calendar_service:
                    start_hour = time.split('-')[0]
                    end_hour = time.split('-')[1]
                    
                    if 'PM' in start_hour and start_hour.replace('PM', '') != '12':
                        start_24 = str(int(start_hour.replace('PM', '')) + 12)
                    elif 'AM' in start_hour and start_hour.replace('AM', '') == '12':
                        start_24 = '00'
                    else:
                        start_24 = start_hour.replace('AM', '').replace('PM', '').zfill(2)
                    
                    if 'PM' in end_hour and end_hour.replace('PM', '') != '12':
                        end_24 = str(int(end_hour.replace('PM', '')) + 12)
                    elif 'AM' in end_hour and end_hour.replace('AM', '') == '12':
                        end_24 = '00'
                    else:
                        end_24 = end_hour.replace('AM', '').replace('PM', '').zfill(2)
                    
                    start_time = f"{date}T{start_24.zfill(2)}:00:00+05:30"
                    end_time = f"{date}T{end_24.zfill(2)}:00:00+05:30"
                    
                    event = {
                        'summary': f'Appointment: {patient_name} - {reason}',
                        'start': {'dateTime': start_time, 'timeZone': 'Asia/Kolkata'},
                        'end': {'dateTime': end_time, 'timeZone': 'Asia/Kolkata'}
                    }
                    
                    created_event = calendar_service.events().insert(calendarId='primary', body=event).execute()
                    calendar_link = created_event.get('htmlLink', 'N/A')
                    print(f"Calendar event created successfully: {calendar_link}")
                    
                    # Send confirmation email to patient
                    email_result = send_confirmation_email(patient_email, patient_name, doctor_name, date, time, reason)
                    print(email_result)
                    
                    return (f"Appointment booked successfully for {patient_name} ({patient_email}) with {doctor_name} "
                            f"on {date} at {time} for {reason}. Calendar event created: {calendar_link}. {email_result}")
                else:
                    # Send confirmation email even if calendar service is unavailable
                    email_result = send_confirmation_email(patient_email, patient_name, doctor_name, date, time, reason)
                    print(email_result)
                    
                    return (f"Appointment booked successfully for {patient_name} ({patient_email}) with {doctor_name} "
                            f"on {date} at {time} for {reason}. Note: Calendar service unavailable. {email_result}")
                
            except Exception as calendar_error:
                print(f"Calendar booking failed: {calendar_error}")
                #  Send confirmation email even if calendar booking fails
                email_result = send_confirmation_email(patient_email, patient_name, doctor_name, date, time, reason)
                print(email_result)
                
                return (f"Appointment booked successfully for {patient_name} ({patient_email}) with {doctor_name} "
                        f"on {date} at {time} for {reason}. Note: Calendar event creation failed. {email_result}")
            
    except Exception as e:
        return f"Booking failed: {str(e)}"

@tool
def query_stats(query_type_and_doctor: str):
    """Query appointment statistics for a specific doctor with privacy protection.
    Args:
        query_type_and_doctor: A string with one of these formats:
            - For date queries: 'YYYY-MM-DD,doctor_name' (e.g., '2025-08-24,Dr. Ahuja')
            - For relative queries: 'appointments_today,doctor_name' or 'appointments_tomorrow,doctor_name' or 'appointments_yesterday,doctor_name'
            - For patient-specific queries: 'query_type,doctor_name,patient_email' (e.g., 'appointments_today,Dr. Ahuja,raju@example.com')
            - For doctor queries: 'query_type,doctor_name,DOCTOR_VIEW' (e.g., 'appointments_today,Dr. Ahuja,DOCTOR_VIEW')
            - For specific appointments: 'date,doctor_name,patient_name,reason' (e.g., '2025-08-23,Dr. Ahuja,John Doe,checkup')
        
        Valid query types:
            - YYYY-MM-DD: Get appointments for specific date (e.g., '2025-08-24')
            - appointments_today: Get today's appointments for the doctor
            - appointments_tomorrow: Get tomorrow's appointments for the doctor  
            - appointments_yesterday: Get yesterday's appointments for the doctor
            - YYYY-MM-DD: Get appointments for a specific date
            
    Returns:
        Statistical information about appointments for the specified doctor.
        Privacy protected: Only shows available slots for general queries, 
        only shows patient's own appointments when patient email is specified,
        shows full patient details when DOCTOR_VIEW flag is used.
    """
    try:
        parts = query_type_and_doctor.split(',')
        if len(parts) < 2:
            return "Please provide at least query type and doctor name separated by comma"
        
        is_doctor_view = False
        if len(parts) == 2:
            query_type = parts[0].strip().strip("'\"").strip()
            doctor_name = parts[1].strip().strip("'\"").strip()
            patient_email = None
            patient_name = None
            reason = None
        elif len(parts) == 3:
            query_type = parts[0].strip().strip("'\"").strip()
            doctor_name = parts[1].strip().strip("'\"").strip()
            third_param = parts[2].strip().strip("'\"").strip()
            if third_param == "DOCTOR_VIEW":
                is_doctor_view = True
                patient_email = None
            else:
                patient_email = third_param
            patient_name = None
            reason = None
        elif len(parts) == 4:
            query_type = parts[0].strip().strip("'\"").strip() 
            doctor_name = parts[1].strip().strip("'\"").strip()
            patient_name = parts[2].strip().strip("'\"").strip()
            reason = parts[3].strip().strip("'\"").strip()
            patient_email = None
        else:
            return "Invalid format. Use 'query_type,doctor_name' for stats, 'query_type,doctor_name,patient_email' for patient-specific queries, or 'date,doctor_name,patient_name,reason' for specific appointments"
        
        print(f"Query stats for '{doctor_name}' with query type '{query_type}' (Doctor view: {is_doctor_view})")
        
        with SessionLocal() as session:
            doctor = session.query(Doctor).filter(Doctor.name == doctor_name).first()
            if not doctor:
                return f"Doctor '{doctor_name}' not found."
            
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            if len(parts) == 2 or len(parts) == 3:
                if query_type.startswith("appointments_"):
                    date_part = query_type.replace("appointments_", "")
                    if date_part in ["yesterday", "today", "tomorrow"]:
                        if date_part == "yesterday":
                            target_date = yesterday
                        elif date_part == "today":
                            target_date = today
                        elif date_part == "tomorrow":
                            target_date = tomorrow
                    else:
                        target_date = date_part
                elif query_type == "appointments_yesterday":
                    target_date = yesterday
                elif query_type == "appointments_today":
                    target_date = today
                elif query_type == "appointments_tomorrow":
                    target_date = tomorrow
                else:
                    target_date = query_type
                
                query = session.query(Appointment).filter(
                    Appointment.doctor_id == doctor.id,
                    Appointment.date == target_date
                )
                
                if patient_email:
                    query = query.filter(Appointment.patient_email == patient_email)
                
                appointments = query.all()
                
                count = len(appointments)
                
                if patient_email:
                    if count > 0:
                        details = f"{count} appointment(s) for {patient_email} with {doctor_name} on {target_date}:\n"
                        for appt in appointments:
                            details += f"- {appt.time}: {appt.patient_name} - {appt.reason}\n"
                        return details.strip()
                    else:
                        return f"No appointments for {patient_email} with {doctor_name} on {target_date}."
                elif is_doctor_view:
                    if count > 0:
                        details = f"{count} appointment(s) for {doctor_name} on {target_date}:\n"
                        for appt in appointments:
                            details += f"- {appt.time}: {appt.patient_name} ({appt.patient_email}) - {appt.reason}\n"
                        return details.strip()
                    else:
                        return f"No appointments for {doctor_name} on {target_date}."
                else:
                    available_slots = doctor.availability.get(target_date, [])
                    if available_slots:
                        return f"Available slots for {doctor_name} on {target_date}: {', '.join(available_slots)}"
                    else:
                        if count > 0:
                            return f"No available slots for {doctor_name} on {target_date}. ({count} appointments already booked)"
                        else:
                            return f"No slots available for {doctor_name} on {target_date}."
            
            elif len(parts) == 4:
                target_date = query_type  
                appointments = session.query(Appointment).filter(
                    Appointment.doctor_id == doctor.id,
                    Appointment.date == target_date,
                    Appointment.patient_name == patient_name,
                    Appointment.reason == reason
                ).all()
                
                if appointments:
                    return f"Found appointment for {patient_name} with {doctor_name} on {target_date} for {reason}."
                else:
                    return f"No appointment found for {patient_name} with {doctor_name} on {target_date} for {reason}."
                
    except Exception as e:
        return f"Error querying stats: {str(e)}"

#agent initialization   
memory = ConversationBufferMemory(return_messages=True, memory_key="chat_history")
tools = [check_availability, book_appointment, query_stats]

agent = initialize_agent(
    tools=tools,
    llm=llm,
    agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    memory=memory,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=5,
    agent_kwargs={
        "prefix": f"""You are a medical assistant agent that helps with doctor appointments. 

Current date: {datetime.now().strftime('%Y-%m-%d')}
Current time: {datetime.now().strftime('%H:%M')}

You have access to the following tools:

1. check_availability: Check a doctor's availability on a specific date
   - Input format: 'Doctor Name, YYYY-MM-DD' (e.g., 'Dr. Ahuja, 2025-08-23')
   - IMPORTANT: Always use the exact doctor name mentioned in the user's current message

2. book_appointment: Book an appointment
   - Input format: 'Doctor Name, YYYY-MM-DD, Time, Email, Patient Name, Reason' (e.g., 'Dr. Ahuja, 2025-08-23, 9AM-10AM, patient@email.com, John Doe, checkup')
   - IMPORTANT: Always use the exact doctor name mentioned in the user's current message

3. query_stats: Get appointment statistics for a doctor
   - For available slots: 'YYYY-MM-DD, Doctor Name' (e.g., '2025-08-24, Dr. Ahuja')
   - For relative dates: 'appointments_today, Doctor Name' or 'appointments_yesterday, Doctor Name' or 'appointments_tomorrow, AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    Doctor Name'
   - For patient's own appointments: 'query_type, Doctor Name, Patient Email' (e.g., 'appointments_today, Dr. Ahuja, raju@example.com')
   - For doctors to see patient details: 'query_type, Doctor Name, DOCTOR_VIEW' (e.g., 'appointments_today, Dr. Ahuja, DOCTOR_VIEW')
   - IMPORTANT: Always use the exact doctor name mentioned in the user's current message
   - PRIVACY RULE: General queries only show available slots. Patient-specific queries only when email is provided. Doctors can see all details using DOCTOR_VIEW.

CRITICAL RULES: 
1. When a user mentions a specific doctor name (like "Dr. Rithvick", "Dr. Smith", etc.) in their message, 
   ALWAYS use that exact doctor name in your tool calls. Do NOT substitute it with any other doctor name.
2. EXCEPTION: If the current user is a doctor asking about "my appointments", "my patients", or "my schedule",
   use the CURRENT USER'S doctor name, not any other doctor name from the conversation.
3. NEVER show other patients' appointment details to regular users. Only show available time slots for general queries.
4. Only show a patient's own appointment details when their email is specifically included in the query.
5. For doctor users requesting patient details, use 'DOCTOR_VIEW' as the third parameter to see all patient information.

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
async def login(data: UserLogin):
    name = data.name
    if data.role == "doctor" and name and not name.strip().lower().startswith("dr."):
        name = f"Dr. {name.strip()}"
    
    user = verify_user(
        email=data.email,
        password=data.password,
        name=name,
        role=data.role
    )

    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    access_token = create_access_token(
        data={"sub": user.email, "role": user.role, "name": user.name}
    )

    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=CurrentUser)
async def get_current_user_info(current_user: CurrentUser = Depends(get_current_user)):
    return current_user

class AppointmentSlots(BaseModel):
    slots: dict  

@app.post("/appointments")
async def add_appointment_slots(appointment_slots: AppointmentSlots, current_user: CurrentUser = Depends(get_current_user)):
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        with SessionLocal() as session:
            doctor = session.query(Doctor).join(User).filter(User.email == current_user.email).first()
            if not doctor:
                raise HTTPException(status_code=404, detail="Doctor record not found")
            
            for date, slots in appointment_slots.slots.items():
                if date in doctor.availability:
                    existing_slots = set(doctor.availability[date])
                    new_slots = set(slots)
                    doctor.availability[date] = list(existing_slots.union(new_slots))
                else:
                    doctor.availability[date] = slots
            
            flag_modified(doctor, 'availability')
            session.commit()
            print("Updated availability:", doctor.availability)
            return {"detail": "Appointment slots added successfully", "updated_availability": doctor.availability}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add appointment slots: {str(e)}")


@app.post("/process_prompt")
async def process_prompt(prompt: Prompt, current_user: CurrentUser = Depends(get_current_user)):
    text = prompt.text.strip()
    logger.info(f"Processing prompt: {text}")
    logger.info(f"User role: {current_user.role}")
    
    try:
        
        session_key = f"{current_user.email}_{prompt.session_id}"
        if not hasattr(process_prompt, 'memory_buffers'):
            process_prompt.memory_buffers = {}
        
        if session_key not in process_prompt.memory_buffers:
            process_prompt.memory_buffers[session_key] = ConversationBufferMemory(return_messages=True, memory_key="chat_history")
        
        memory = process_prompt.memory_buffers[session_key]
        
        
        conversation_history = memory.load_memory_variables({}).get("chat_history", [])
        
        
        current_time = datetime.now()
        
        
        if not hasattr(process_prompt, 'session_timestamps'):
            process_prompt.session_timestamps = {}
        
       
        process_prompt.session_timestamps[session_key] = current_time
        
       
        sessions_to_remove = []
        for key in list(process_prompt.memory_buffers.keys()):
            last_accessed = process_prompt.session_timestamps.get(key, current_time)
            if (current_time - last_accessed).total_seconds() > 3600:
                sessions_to_remove.append(key)
        
        for key in sessions_to_remove:
            if key in process_prompt.memory_buffers:
                del process_prompt.memory_buffers[key]
            if key in process_prompt.session_timestamps:
                del process_prompt.session_timestamps[key]
        
        
        conversation_context = ""
        if conversation_history:
            conversation_context = "\nConversation History:\n"
            
            recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
            for msg in recent_history:
                if msg.type == "human":
                    conversation_context += f"User: {msg.content}\n"
                elif msg.type == "ai":
                    conversation_context += f"Assistant: {msg.content}\n\n"
        
       
        if conversation_context:
            logger.info(f"Conversation context for {current_user.email}: {conversation_context}")
            if current_user.role == "doctor":
                enhanced_text = f"""Previous conversation context:
             {conversation_context}

Based on the conversation history above, please respond to the current user message.
You are a doctor user with elevated privileges.

DOCTOR PRIVILEGES:
- You can see patient details by using 'DOCTOR_VIEW' as the third parameter in query_stats
- When asking about YOUR OWN appointments, use: 'appointments_today, {current_user.name}, DOCTOR_VIEW'
- When asking about another doctor's appointments, use: 'appointments_today, Other Doctor Name, DOCTOR_VIEW'
- Example: 'appointments_tomorrow, {current_user.name}, DOCTOR_VIEW' to see all YOUR patient details for tomorrow

IMPORTANT: You are Dr. {current_user.name}. When you ask about "my appointments" or "my patients", 
always use YOUR name ({current_user.name}) in the query, not any other doctor name mentioned in conversation.

Current doctor name: {current_user.name}
Current doctor email: {current_user.email}
Current user role: {current_user.role}
Current message: {text}
Note: Only use query_stats tool and check_availability tool and don't use any other tool."""
            else:
                enhanced_text = f"""Previous conversation context:
        {conversation_context}

        Based on the conversation history above, please respond to the current user message. 
        IMPORTANT: Always pay attention to doctor names mentioned in the current message first. 
        If a doctor name is explicitly mentioned in the current message (like "Dr. Rithvick", "Dr. Smith", etc.), 
        use that doctor name for the query, NOT any doctor from previous conversations.
        Only use conversation history for context about appointment times, dates, or other details.

        PRIVACY RULE: When checking appointments or availability, only show available time slots to users.
        Never show other patients' appointment details. If the user asks about their own appointments,
        include their email ({current_user.email}) in the query to get their specific appointments only.

        Current user name: {current_user.name}
        Current user email: {current_user.email}
        Current user role: {current_user.role}
        Current message: {text}"""
        else:
            logger.info(f"Conversation context for {current_user.email}: {conversation_context}")
            if current_user.role == "doctor":
                enhanced_text = f"""Doctor query for statistics only. 
You are Dr. {current_user.name} (email: {current_user.email}).
When asking about YOUR appointments/patients, use your name: {current_user.name}
Query: {text}
Note: Only use query_stats tool and check_availability tool. For patient details, use DOCTOR_VIEW flag.
Example for your appointments: 'appointments_tomorrow, {current_user.name}, DOCTOR_VIEW'"""
            else:
                enhanced_text = f"Patient query . User name: {current_user.name}\nUser email: {current_user.email}\nUser role: {current_user.role}\nMessage: {text}"

        
        agent_response = agent.invoke({"input": enhanced_text, "chat_history": conversation_history})
        response = agent_response["output"] if "output" in agent_response else str(agent_response)
        
        
        memory.save_context({"input": text}, {"output": response})
        
        
        with SessionLocal() as session:
            history = PromptHistory(
                session_id=prompt.session_id,
                user_email=current_user.email,
                prompt_text=text,
                response_text=str(response)
            )
            session.add(history)
            session.commit()
        
        
        if current_user.role == "doctor":
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
async def get_prompt_history(current_user: CurrentUser = Depends(get_current_user)):
    with SessionLocal() as session:
        history = session.query(PromptHistory).filter(PromptHistory.user_email == current_user.email).all()
        return [{"prompt": h.prompt_text, "response": h.response_text, "created_at": h.created_at} for h in history]