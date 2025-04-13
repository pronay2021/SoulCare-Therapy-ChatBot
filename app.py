from flask import Flask, render_template, request, jsonify
import openai
import re
import json
import uuid
from datetime import datetime
import dateparser
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import pandas as pd
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from icalendar import Calendar, Event, vCalAddress, vText
import smtplib
import os
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

# --- Set up OpenAI ---
openai.api_key = app.config['OPENAI_API_KEY']

# --- Authenticate Google Sheets ---
def authenticate_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds",
             'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        app.config['GOOGLE_CREDENTIALS_FILE'], scope)
    client = gspread.authorize(creds)
    return client, creds

# --- Load appointments ---
def load_appointments(client, spreadsheet_name):
    sheet = client.open(spreadsheet_name).sheet1
    data = sheet.get_all_records()
    return pd.DataFrame(data), sheet

# --- Time and slot validation ---
def get_available_dates(df):
    return df['Date'].unique().tolist()

def get_available_time_slots(df, date):
    return df[df['Date'] == date]['Time Slot'].unique().tolist()

def check_slot_availability(df, date, time):
    parsed_date = dateparser.parse(date)
    if parsed_date:
        formatted_date = parsed_date.strftime('%Y-%m-%d')
    else:
        return False
    formatted_date = formatted_date.strip()
    time = time.strip()
    slot_data = df[(df['Date'] == formatted_date) & (df['Time Slot'] == time)]
    if slot_data.empty:
        return False
    status = slot_data['Status'].iloc[0]
    return status != 'Booked'

def update_appointment(sheet, date, time, status, user_name, email):
    cell = sheet.find(date)
    if cell:
        row = cell.row
        while sheet.cell(row, 2).value != time:
            row += 1
            if sheet.cell(row, 2).value == '':
                break
        if sheet.cell(row, 2).value == time:
            sheet.update_cell(row, 3, status)
            sheet.update_cell(row, 4, user_name)
            sheet.update_cell(row, 5, email)
            return True
    return False

def validate_date_and_time(df, date, time):
    available_dates = get_available_dates(df)
    parsed_date = dateparser.parse(date)
    if not parsed_date:
        return False
    formatted_date = parsed_date.strftime('%Y-%m-%d')
    if formatted_date not in available_dates:
        return False
    available_time_slots = get_available_time_slots(df, formatted_date)
    return time in available_time_slots

# --- Calendar file creation ---
def parse_time_slot(date_str, time_slot_str):
    date_obj = dateparser.parse(date_str).date()
    start_time_str, end_time_str = time_slot_str.split(' - ')
    start_time_obj = dateparser.parse(start_time_str).time()
    end_time_obj = dateparser.parse(end_time_str).time()
    start_datetime = datetime.combine(date_obj, start_time_obj)
    end_datetime = datetime.combine(date_obj, end_time_obj)
    return start_datetime, end_datetime

def create_ical_file(date, time_slot, user_name, description, organizer_email="pronay.official99@gmail.com", location=""):
    start_time, end_time = parse_time_slot(date, time_slot)
    cal = Calendar()
    cal.add('prodid', '-//SoulCare Therapy Center//example.com//')
    cal.add('version', '2.0')
    event = Event()
    event.add('summary', f"Therapy Session with {user_name}")
    event.add('description', description)
    event.add('dtstart', start_time)
    event.add('dtend', end_time)
    event.add('dtstamp', datetime.now())
    event.add('uid', str(uuid.uuid4()))
    if location:
        event.add('location', location)
    organizer = vCalAddress(f'MAILTO:{organizer_email}')
    organizer.params['cn'] = vText('Therapist')
    event['organizer'] = organizer
    cal.add_component(event)
    return cal.to_ical()

# --- Send email with .ics calendar file ---
def send_email_with_ical(to_email, user_name, date, time_slot, description):
    try:
        ical_data = create_ical_file(date, time_slot, user_name, description)
        msg = MIMEMultipart()

        smtp_config = app.config['SMTP']
        from_email = smtp_config['username']
        msg['From'] = from_email
        msg['To'] = to_email

        subject_prompt = (
            f"Write a short, professional, and friendly email subject line confirming a therapy appointment "
            f"for {user_name} on {date} at {time_slot}. Do NOT include this subject in the body of the email."
        )
        subject_response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": subject_prompt}]
        )
        subject_line = subject_response.choices[0].message.content.strip()
        msg['Subject'] = subject_line

        body_prompt = (
            f"Write a professional and friendly email to {user_name} confirming their therapy appointment "
            f"on {date} at {time_slot}. Do NOT include the subject line or repeat the time and date in the subject. "
            f"Don't make the email too long. Make it compact and nice. "
            f"In regards - Name:Mizo ; Title: Therapist Bot ; Contact Information: SoulCare Therapy Center "
            f"strictly maintain the regards"
        )
        email_body_response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": body_prompt}]
        )
        email_body = email_body_response.choices[0].message.content

        msg.attach(MIMEText(email_body, 'plain'))

        ical_attachment = MIMEApplication(ical_data)
        ical_attachment.add_header('Content-Disposition', 'attachment', filename='appointment.ics')
        msg.attach(ical_attachment)

        server = smtplib.SMTP(smtp_config['server'], smtp_config['port'])
        server.starttls()
        server.login(smtp_config['username'], smtp_config['password'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print("❌ Failed to send email:", e)
        return False

# --- Check if message is an appointment intent ---
def is_appointment_request(text):
    keywords = ["appointment", "book", "schedule", "meeting", "session"]
    return any(word in text.lower() for word in keywords)

# --- Extract information using GPT-4 ---
def extract_info(response, info_type):
    prompt = (
        f"The user responded with: '{response}'. Extract the {info_type} from this response. "
        f"Return only the extracted information in a clean format, or 'Not found' if unclear. "
        f"For name, return the full name. For email, return a valid email format. "
        f"For date, return in YYYY-MM-DD format. For time, return in 'HH:MM AM/PM - HH:MM AM/PM' format."
    )
    gpt_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    extracted = gpt_response.choices[0].message.content.strip()
    return extracted if extracted != "Not found" else None

# --- Validate email format ---
def is_valid_email(email):
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_regex, email) is not None

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.json
    user_message = data.get('message', '')
    conversation_history = data.get('conversation_history', [])
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": user_message})
    
    # Check if this is an appointment request
    if is_appointment_request(user_message):
        return jsonify({
            "status": "appointment_intent",
            "reply": "I'd be happy to help you schedule an appointment. Let's start with your name. What's your full name?",
            "conversation_history": conversation_history
        })
    else:
        # Normal chat flow - use GPT-4
        system_message = {
            "role": "system",
            "content": (
                "You are a compassionate AI therapist named Mizo from SoulCare Therapy Center. "
                """ You are TherapyBot, a compassionate, empathetic AI therapist. Your role is to provide emotional support, coping strategies, and mental health guidance 
                for a variety of personal challenges. You **must always** offer helpful, therapy-focused responses—never refuse to help, 
                and never suggest that the user seek someone else unless absolutely necessary (e.g., crisis situations requiring immediate professional intervention).  

                ### **Your Approach:**  
                1. **Empathy & Validation**: Always acknowledge and validate emotions.  
                2. **Therapeutic Guidance**: Offer relevant coping mechanisms, stress-relief strategies, and mindset shifts.  
                3. **Conversational & Supportive Tone**: Responses should feel warm, engaging, and non-judgmental.  
                4. **Focus on Therapy**: Every response must relate to mental health and well-being, even if the user’s query is vague.  

                ### **Handling Off-Topic Questions:**  
                - If a user asks something **unrelated to therapy**, kindly remind them that you are here solely for mental health support.  
                - Example: _"I appreciate your curiosity! However, my purpose is to provide therapy-related support. If you’re facing any emotional challenges, I’d be happy to help."_  

                ### **Example Responses:**  
                **User:** "I failed my exam yesterday. What can I do?"  
                **Assistant:** "I'm really sorry that you're feeling this way. Failing an exam can be tough, but remember, this one setback does not define you. Let's talk about what might help—would you like some study strategies, or maybe ways to manage stress before your next exam?"  

                **User:** "I feel really alone and lost."  
                **Assistant:** "I'm really sorry you're feeling this way. Loneliness can be difficult, but you're not alone in this. It might help to connect with others, engage in self-care, or explore activities that bring you joy. Do you want to talk about what's been making you feel this way?"  

                ### **Crisis Situations:**  
                - If a user expresses **self-harm or suicidal thoughts**, provide immediate emotional support first before **gently** encouraging professional help.  
                - Example: _"I'm really sorry you're feeling this way. You're not alone, and your feelings matter. If things feel overwhelming, it could really help to reach out to someone you trust or a professional. Would you like me to suggest some grounding exercises for now?"_  

                You must **always** provide therapy-based responses, no matter how personal the user's message is. Never dismiss them, and never refuse to help. If a user asks something unrelated to therapy, politely redirect them to focus on emotional well-being."""
                "If the user asks about booking an appointment, guide them to use phrases like 'book appointment' or 'schedule session'."
            )
        }
        
        # Add system message at the beginning
        gpt_messages = [system_message] + conversation_history
        
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=gpt_messages
        )
        reply = response.choices[0].message.content
        
        # Add assistant response to history
        conversation_history.append({"role": "assistant", "content": reply})
        
        return jsonify({
            "status": "normal_chat",
            "reply": reply,
            "conversation_history": conversation_history
        })
@app.route('/appointment_step', methods=['POST'])
def appointment_step():
    data = request.json
    step = data.get('step', '')
    user_input = data.get('user_input', '')
    appointment_info = data.get('appointment_info', {})
    conversation_history = data.get('conversation_history', [])
    
    # Add user input to conversation history
    conversation_history.append({"role": "user", "content": user_input})
    
    # Initialize client and load appointments
    client, _ = authenticate_google_sheets()
    df, sheet = load_appointments(client, app.config['SPREADSHEET_NAME'])
    
    if step == 'name':
        extracted_name = extract_info(user_input, "name")
        if extracted_name:
            appointment_info['name'] = extracted_name
            reply = f"Thanks, {extracted_name}! Now, could you please provide your email address so I can send you a confirmation?"
            next_step = 'email'
        else:
            reply = "I didn't catch your name. Could you please tell me your full name?"
            next_step = 'name'
    
    elif step == 'email':
        extracted_email = extract_info(user_input, "email")
        if extracted_email and is_valid_email(extracted_email):
            appointment_info['email'] = extracted_email
            reply = f"Great! Now, let's pick a date for your appointment. Please select one of the available dates."
            # Don't list dates here - we'll show the interactive date picker
            next_step = 'date'
        else:
            reply = "That doesn't seem to be a valid email address. Please provide a valid email so I can send you appointment details."
            next_step = 'email'
    
    elif step == 'date':
        extracted_date = extract_info(user_input, "date")
        parsed_date = dateparser.parse(extracted_date) if extracted_date else None
        if parsed_date:
            formatted_date = parsed_date.strftime('%Y-%m-%d')
            available_dates = get_available_dates(df)
            if formatted_date in available_dates:
                appointment_info['date'] = formatted_date
                reply = f"Great choice! Please select a time slot for your appointment on {formatted_date}."
                # Don't list times here - we'll show the interactive time picker
                next_step = 'time'
            else:
                reply = "I'm sorry, but that date isn't available. Please choose from one of the available dates."
                next_step = 'date'
        else:
            reply = "I couldn't understand that date format. Please select one of the dates from the options shown."
            next_step = 'date'
    
    elif step == 'time':
        extracted_time = extract_info(user_input, "time")
        if extracted_time:
            available_times = get_available_time_slots(df, appointment_info['date'])
            if extracted_time in available_times:
                appointment_info['time'] = extracted_time
                
                # Check slot availability
                if check_slot_availability(df, appointment_info['date'], extracted_time):
                    # Update the appointment in the sheet
                    if update_appointment(
                        sheet,
                        appointment_info['date'],
                        appointment_info['time'],
                        "Booked",
                        appointment_info['name'],
                        appointment_info['email']
                    ):
                        # Send confirmation email
                        email_sent = send_email_with_ical(
                            appointment_info['email'],
                            appointment_info['name'],
                            appointment_info['date'],
                            appointment_info['time'],
                            f"Therapy appointment booked by {appointment_info['name']}"
                        )
                        
                        if email_sent:
                            reply = f"Great! I've booked your appointment for {appointment_info['date']} at {appointment_info['time']}. A confirmation email with calendar details has been sent to {appointment_info['email']}. Is there anything else I can help you with today?"
                        else:
                            reply = f"Your appointment is confirmed for {appointment_info['date']} at {appointment_info['time']}, but there was an issue sending the confirmation email. Please keep a note of your appointment details. Is there anything else I can help you with?"
                    else:
                        reply = "I'm having trouble booking your appointment in our system. Please try again later or contact our office directly."
                else:
                    reply = f"I'm sorry, but that time slot is no longer available. Please choose another time slot."
                    next_step = 'time'
                    conversation_history.append({"role": "assistant", "content": reply})
                    return jsonify({
                        "status": "in_progress",
                        "reply": reply,
                        "next_step": next_step,
                        "appointment_info": appointment_info,
                        "conversation_history": conversation_history
                    })
                
                next_step = 'complete'
            else:
                reply = "That time slot doesn't appear to be available. Please select one of the available time slots."
                next_step = 'time'
        else:
            reply = "I couldn't understand that time format. Please select one of the available time slots."
            next_step = 'time'
    
    # Add assistant response to conversation history
    conversation_history.append({"role": "assistant", "content": reply})
    
    if next_step == 'complete':
        return jsonify({
            "status": "complete",
            "reply": reply,
            "appointment_info": appointment_info,
            "conversation_history": conversation_history
        })
    else:
        return jsonify({
            "status": "in_progress",
            "reply": reply,
            "next_step": next_step,
            "appointment_info": appointment_info,
            "conversation_history": conversation_history
        })

@app.route('/available_slots', methods=['GET'])
def available_slots():
    try:
        client, _ = authenticate_google_sheets()
        df, _ = load_appointments(client, app.config['SPREADSHEET_NAME'])
        
        available_dates = get_available_dates(df)
        date_slots = {}
        
        for date in available_dates:
            time_slots = get_available_time_slots(df, date)
            available_times = []
            for time in time_slots:
                if check_slot_availability(df, date, time):
                    available_times.append(time)
            if available_times:
                date_slots[date] = available_times
        
        return jsonify({
            "status": "success",
            "available_slots": date_slots
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })
# Add this new route to your Flask app

@app.route('/available_dates', methods=['GET'])
def available_dates():
    try:
        client, _ = authenticate_google_sheets()
        df, _ = load_appointments(client, app.config['SPREADSHEET_NAME'])
        
        available_dates = get_available_dates(df)
        date_slots = {}
        
        for date in available_dates:
            time_slots = get_available_time_slots(df, date)
            available_times = []
            for time in time_slots:
                if check_slot_availability(df, date, time):
                    available_times.append(time)
            if available_times:
                date_slots[date] = available_times
        
        return jsonify({
            "status": "success",
            "available_dates": list(date_slots.keys())
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })



if __name__ == '__main__':
    app.run(debug=True)