import os
import uuid
import json
import io
import mysql.connector
from mysql.connector import pooling
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
from dotenv import load_dotenv
from groq import Groq
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, ListFlowable, ListItem
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "symptom_triage_secret_key_123")

# Connection Pool Variable
db_pool = None

HOSPITAL_FACILITIES = {
    "Emergency Medicine / Cardiology": [
        {"name": "City General Hospital (Emergency Room)", "capacity": 94},
        {"name": "Mercy Medical Center (Cardiac Clinic)", "capacity": 38}
    ],
    "General Physician / Family Medicine": [
        {"name": "Westside Family Practice Center", "capacity": 22},
        {"name": "Metro Health Clinic", "capacity": 74}
    ],
    "General Medicine / Internal Medicine": [
        {"name": "St. Jude Outpatient Clinic", "capacity": 85},
        {"name": "Community Health Center", "capacity": 15}
    ],
    "Pulmonology": [
        {"name": "County Pulmonary Specialty Wing", "capacity": 62},
        {"name": "Chest Disease Outpatient Center", "capacity": 45}
    ],
    "Neurology": [
        {"name": "Neurological Diagnostic Institute", "capacity": 78},
        {"name": "Neuromed Family Specialty Practice", "capacity": 30}
    ],
    "Default": [
        {"name": "Metro Health Specialist Wing", "capacity": 42},
        {"name": "County Health Outpatient Wing", "capacity": 88}
    ]
}

def get_db_connection():
    """Get a database connection from the pool, or create a new connection if pooling is not initialized."""
    global db_pool
    if db_pool is None:
        try:
            db_pool = pooling.MySQLConnectionPool(
                pool_name="triage_pool",
                pool_size=5,
                host=os.getenv("DB_HOST", "localhost"),
                user=os.getenv("DB_USER", "root"),
                password=os.getenv("DB_PASSWORD", ""),
                database=os.getenv("DB_NAME", "symptom_triage"),
                port=int(os.getenv("DB_PORT", 3306))
            )
        except Exception as e:
            print(f"Error creating connection pool: {e}")
            # Fallback to direct connection if pool fails to initialize
            return mysql.connector.connect(
                host=os.getenv("DB_HOST", "localhost"),
                user=os.getenv("DB_USER", "root"),
                password=os.getenv("DB_PASSWORD", ""),
                database=os.getenv("DB_NAME", "symptom_triage"),
                port=int(os.getenv("DB_PORT", 3306))
            )
    
    return db_pool.get_connection()

def init_db():
    """Initialize database and create reports table if it doesn't exist."""
    try:
        # First connect without database context to create database if needed
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            port=int(os.getenv("DB_PORT", 3306))
        )
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {os.getenv('DB_NAME', 'symptom_triage')}")
        cursor.close()
        conn.close()

        # Connect with database to create the table
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            port=int(os.getenv("DB_PORT", 3306)),
            database=os.getenv("DB_NAME", "symptom_triage")
        )
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                uuid VARCHAR(36) UNIQUE NOT NULL,
                symptoms_text TEXT NOT NULL,
                duration VARCHAR(50) NOT NULL,
                severity INT NOT NULL,
                recommended_department VARCHAR(100) NOT NULL,
                urgency_level ENUM('low', 'medium', 'high') NOT NULL,
                ai_explanation TEXT NOT NULL,
                heart_rate INT DEFAULT NULL,
                sbar_text TEXT DEFAULT NULL,
                red_flags TEXT DEFAULT NULL,
                co_occurring TEXT DEFAULT NULL,
                assigned_facility VARCHAR(150) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # Check and add columns if they don't exist (for existing tables)
        columns_to_add = {
            "heart_rate": "INT DEFAULT NULL",
            "sbar_text": "TEXT DEFAULT NULL",
            "red_flags": "TEXT DEFAULT NULL",
            "co_occurring": "TEXT DEFAULT NULL",
            "assigned_facility": "VARCHAR(150) DEFAULT NULL"
        }
        for col_name, col_def in columns_to_add.items():
            cursor.execute(f"""
                SELECT COUNT(*) FROM information_schema.COLUMNS 
                WHERE TABLE_SCHEMA = '{os.getenv('DB_NAME', 'symptom_triage')}' 
                AND TABLE_NAME = 'reports' 
                AND COLUMN_NAME = '{col_name}'
            """)
            if cursor.fetchone()[0] == 0:
                print(f"Altering table reports to add column: {col_name}")
                cursor.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_def}")
                conn.commit()

        cursor.close()
        conn.close()
        print("Database schema successfully verified/created/migrated.")
    except Exception as e:
        print(f"Error during database initialization: {e}")

def get_fallback_mock_response(symptoms_text, severity, associated_symptoms):
    """Fallback generator for routing/urgency assessment if API key is missing or fails."""
    emergency_keywords = ["chest pain", "difficulty breathing", "shortness of breath", "severe bleeding", "stroke", "heart attack", "unconscious", "head injury"]
    symptoms_lower = symptoms_text.lower()
    
    is_emergency = any(kw in symptoms_lower for kw in emergency_keywords) or \
                     "Chest Tightness" in associated_symptoms or \
                     "Shortness of Breath" in associated_symptoms or \
                     severity >= 9
                     
    matched_emergency = [kw for kw in emergency_keywords if kw in symptoms_lower]

    if is_emergency:
        return {
            "department": "Emergency Medicine / Cardiology",
            "confidence": 92,
            "differential_department": "Pulmonology" if "breathing" in symptoms_lower or "shortness of breath" in symptoms_lower else "Neurology",
            "differential_confidence": 40,
            "urgency_level": "high",
            "explanation": "Your reported symptoms, such as chest tightness, difficulty breathing, or high pain severity, suggest a potential medical emergency. You should seek immediate professional medical attention at the nearest emergency department.",
            "reasoning_steps": [
                f"Severity reported at {severity}/10, above the high-risk threshold" if severity >= 9 else f"Emergency keyword match: {', '.join(matched_emergency) if matched_emergency else 'critical symptom pattern'}",
                "Cross-checked against emergency symptom cluster (cardiac/respiratory)",
                "Escalation rule triggered: routed directly to emergency care"
            ],
            "discuss_with_doctor": [
                "Exact timestamp when the discomfort or pain started",
                "Any prior history of heart problems or blood pressure concerns",
                "Whether you are experiencing radiating pain, dizziness, or sweatiness"
            ],
            "sbar_text": "Situation: Patient presenting with acute chest tightness/difficulty breathing. Background: Symptoms reported as severe. Assessment: High probability of acute coronary or respiratory distress. Recommendation: Urgent transfer to Emergency Department for immediate stabilization.",
            "red_flags": [
                "Radiating pain to left arm, neck, or jaw",
                "Sudden loss of consciousness or fainting",
                "Cold sweats combined with confusion"
            ],
            "co_occurring": [
                {"symptom": "Shortness of Breath", "weight": 85},
                {"symptom": "Sweating", "weight": 70},
                {"symptom": "Palpitations", "weight": 60}
            ]
        }
    
    moderate_keywords = ["fever", "pain", "nausea", "vomiting", "dizziness", "cough", "headache", "rash", "infection"]
    matched_moderate = [kw for kw in moderate_keywords if kw in symptoms_lower]
    is_moderate = any(kw in symptoms_lower for kw in moderate_keywords) or \
                    len(associated_symptoms) >= 2 or \
                    severity >= 5
                    
    if is_moderate:
        return {
            "department": "General Medicine / Internal Medicine",
            "confidence": 78,
            "differential_department": "Gastroenterology" if any(k in symptoms_lower for k in ["nausea", "vomiting"]) else "ENT",
            "differential_confidence": 32,
            "urgency_level": "medium",
            "explanation": "Your symptoms represent a moderate concern. While not an immediate emergency, they require evaluation by a physician within the next 24-48 hours to prevent potential worsening.",
            "reasoning_steps": [
                f"Symptom keywords matched: {', '.join(matched_moderate) if matched_moderate else 'multiple associated symptoms'}",
                f"Severity {severity}/10 and {len(associated_symptoms)} associated symptom(s) weighed together",
                "No emergency-pattern markers found, routed to standard evaluation window"
            ],
            "discuss_with_doctor": [
                "Any fluctuations in body temperature or fever readings",
                "Whether over-the-counter pain relievers or treatments have provided relief",
                "Any relevant pre-existing conditions or recent travel history"
            ],
            "sbar_text": "Situation: Patient presenting with moderate localized symptoms. Background: Severity moderate, no active red flags. Assessment: Sub-acute infection or systemic inflammation. Recommendation: Route to outpatient Internal Medicine clinic for evaluation.",
            "red_flags": [
                "Fever spike exceeding 103°F (39.4°C)",
                "Inability to keep liquids down for 24 hours",
                "Severe worsening of pain or local spreading"
            ],
            "co_occurring": [
                {"symptom": "Nausea", "weight": 55},
                {"symptom": "Fatigue", "weight": 65},
                {"symptom": "Chills", "weight": 40}
            ]
        }
    
    # Defaults to Low
    return {
        "department": "General Physician / Family Medicine",
        "confidence": 70,
        "differential_department": None,
        "differential_confidence": None,
        "urgency_level": "low",
        "explanation": "Your symptoms appear routine and low-urgency. You should monitor your condition, rest, and schedule a routine check-up with a family doctor if symptoms persist.",
        "reasoning_steps": [
            f"Severity reported at {severity}/10, below moderate-risk threshold",
            "No emergency or moderate-risk keywords detected in symptom text",
            "Classified as routine self-monitoring case"
        ],
        "discuss_with_doctor": [
            "How long these mild symptoms have been present",
            "Any minor home remedies or adjustments you have tried",
            "Under what conditions the symptoms seem to improve or worsen"
        ],
        "sbar_text": "Situation: Patient presenting with minor, low-urgency symptoms. Background: Low severity. Assessment: Self-limiting routine concern. Recommendation: Monitor at home, recommend GP visit if symptoms persist.",
        "red_flags": [
            "Persistent symptoms lasting over 10 days",
            "Development of shortness of breath or dizziness",
            "High fever developing later"
        ],
        "co_occurring": [
            {"symptom": "Mild Fatigue", "weight": 30},
            {"symptom": "Minor Headache", "weight": 45},
            {"symptom": "Muscle Aches", "weight": 25}
        ]
    }

def analyze_symptoms_ai(symptoms_text, duration, severity, associated_symptoms, heart_rate=None):
    """Call the Groq API to analyze patient symptoms and return a structured JSON response."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("Warning: GROQ_API_KEY is not configured. Falling back to local heuristics.")
        res = get_fallback_mock_response(symptoms_text, severity, associated_symptoms)
        res["heart_rate"] = heart_rate
        return res
    
    try:
        client = Groq(api_key=api_key)
        
        hr_str = f"{heart_rate} BPM" if heart_rate else "Not measured"
        prompt = f"""You are a medical triage assistant. Based on the patient's reported symptoms and vitals, recommend which hospital department/specialist they should consult and how urgent their situation is. You are NOT diagnosing a condition — you are routing them to the right care.
 
Patient input:
- Symptoms: {symptoms_text}
- Duration: {duration}
- Severity (1-10): {severity}
- Associated symptoms: {", ".join(associated_symptoms) if associated_symptoms else "None"}
- Measured Heart Rate (Vital): {hr_str}

Respond ONLY in this JSON format, no markdown, no preamble:
{{
  "department": "string, must be EXACTLY one of: 'Emergency Medicine / Cardiology', 'General Physician / Family Medicine', 'General Medicine / Internal Medicine', 'Pulmonology', 'Neurology'",
  "confidence": integer 0-100, how confident you are this is the right department given the input,
  "differential_department": "string, a second department worth considering if symptoms are ambiguous, or null if not applicable",
  "differential_confidence": integer 0-100, confidence in the differential department, or null,
  "urgency_level": "low" | "medium" | "high",
  "explanation": "2-3 sentences in plain language explaining why this department and urgency level, written for a worried patient — calm, clear, no medical jargon",
  "reasoning_steps": ["3-4 short phrases (max 12 words each) showing your analytical process, e.g. how severity/duration/keywords were weighted"],
  "discuss_with_doctor": ["short phrase 1", "short phrase 2", "short phrase 3"],
  "sbar_text": "A professional clinical handoff brief in SBAR (Situation, Background, Assessment, Recommendation) format, 2-3 sentences long, describing the key clinical presentation and urgency.",
  "red_flags": ["specific alert symptom 1", "specific alert symptom 2", "specific alert symptom 3"],
  "co_occurring": [
    {{"symptom": "symptom name 1", "weight": integer 0-100}},
    {{"symptom": "symptom name 2", "weight": integer 0-100}},
    {{"symptom": "symptom name 3", "weight": integer 0-100}}
  ]
}}

Rules:
- Never state a specific diagnosis or condition name as fact
- If symptoms or vitals suggest a medical emergency (chest pain, difficulty breathing, severe bleeding, stroke signs, head trauma, or severe tachycardia/bradycardia matching heart rate), set urgency_level to "high" and explanation should recommend immediate emergency care
- Keep explanation reassuring but honest, not alarming
- reasoning_steps and sbar_text should read like genuine clinical triage logic, not generic filler"""

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        
        response_text = chat_completion.choices[0].message.content
        data = json.loads(response_text.strip())
        
        # Validations and fallbacks for response format
        if "department" not in data or "urgency_level" not in data or "explanation" not in data:
            raise ValueError("Response JSON is missing required fields.")
        
        if data["urgency_level"] not in ["low", "medium", "high"]:
            data["urgency_level"] = "medium"
            
        if "discuss_with_doctor" not in data or not isinstance(data["discuss_with_doctor"], list):
            data["discuss_with_doctor"] = [
                "How these symptoms affect your daily routines",
                "Timeline of when they peak or lessen",
                "Any similar historic symptoms"
            ]

        if "confidence" not in data or not isinstance(data["confidence"], (int, float)):
            data["confidence"] = 75

        if "reasoning_steps" not in data or not isinstance(data["reasoning_steps"], list):
            data["reasoning_steps"] = []

        if "differential_department" not in data:
            data["differential_department"] = None
        if "differential_confidence" not in data:
            data["differential_confidence"] = None

        if "sbar_text" not in data:
            data["sbar_text"] = f"Situation: Patient presenting with {symptoms_text[:50]}... Assessment: Urgency classified as {data['urgency_level']}. Recommendation: Consult {data['department']}."

        if "red_flags" not in data or not isinstance(data["red_flags"], list):
            data["red_flags"] = [
                "Sudden worsening of symptoms",
                "Difficulty breathing or shortness of breath",
                "Chest pain, pressure or tightness"
            ]

        if "co_occurring" not in data or not isinstance(data["co_occurring"], list):
            data["co_occurring"] = [
                {"symptom": "Fatigue", "weight": 60},
                {"symptom": "Nausea", "weight": 40},
                {"symptom": "Headache", "weight": 50}
            ]

        return data
    except Exception as e:
        print(f"Error invoking Groq API: {e}. Falling back to mock heuristics.")
        res = get_fallback_mock_response(symptoms_text, severity, associated_symptoms)
        res["heart_rate"] = heart_rate
        return res

@app.route('/')
def intake():
    """Render the patient symptoms intake page."""
    return render_template('intake.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    """Handle intake form submissions, trigger triage analysis, write to DB, and redirect to report."""
    symptoms_text = request.form.get('symptoms_text', '').strip()
    duration = request.form.get('duration', '').strip()
    
    try:
        severity = int(request.form.get('severity', '5'))
    except ValueError:
        severity = 5
        
    associated_symptoms = request.form.getlist('associated_symptoms')
    
    heart_rate_str = request.form.get('heart_rate', '').strip()
    heart_rate = int(heart_rate_str) if heart_rate_str.isdigit() else None
    
    if not symptoms_text or not duration:
        flash("Please fill in both the symptom description and duration fields.", "error")
        return redirect(url_for('intake'))
        
    # Analyze symptoms using AI (or fallback)
    analysis = analyze_symptoms_ai(symptoms_text, duration, severity, associated_symptoms, heart_rate=heart_rate)
    
    # Capacity-Aware Routing logic
    dept = analysis.get("department", "General Physician / Family Medicine")
    facilities = HOSPITAL_FACILITIES.get(dept, HOSPITAL_FACILITIES["Default"])
    assigned_facility = facilities[0]["name"]
    facility_cap = facilities[0]["capacity"]
    
    if len(facilities) > 1:
        # If primary has critical congestion (>=80%) and secondary is clear (<50%), route to secondary
        if facilities[0]["capacity"] >= 80 and facilities[1]["capacity"] < 50:
            assigned_facility = facilities[1]["name"]
            facility_cap = facilities[1]["capacity"]
            analysis["explanation"] += f" [System Alert: Routed to {assigned_facility} due to critical congestion ({facilities[0]['capacity']}% occupancy) at {facilities[0]['name']}]."
        else:
            if facilities[0]["capacity"] <= facilities[1]["capacity"]:
                assigned_facility = facilities[0]["name"]
                facility_cap = facilities[0]["capacity"]
            else:
                assigned_facility = facilities[1]["name"]
                facility_cap = facilities[1]["capacity"]

    # Package explanation and discussion points together into ai_explanation column as JSON
    combined_explanation = json.dumps({
        "explanation": analysis["explanation"],
        "discuss_with_doctor": analysis["discuss_with_doctor"],
        "confidence": analysis.get("confidence", 75),
        "differential_department": analysis.get("differential_department"),
        "differential_confidence": analysis.get("differential_confidence"),
        "reasoning_steps": analysis.get("reasoning_steps", [])
    })
    
    report_uuid = str(uuid.uuid4())
    
    # Store in database
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reports (uuid, symptoms_text, duration, severity, recommended_department, urgency_level, ai_explanation, heart_rate, sbar_text, red_flags, co_occurring, assigned_facility)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            report_uuid,
            symptoms_text,
            duration,
            severity,
            analysis["department"],
            analysis["urgency_level"],
            combined_explanation,
            heart_rate,
            analysis.get("sbar_text"),
            json.dumps(analysis.get("red_flags", [])),
            json.dumps(analysis.get("co_occurring", [])),
            assigned_facility
        ))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database insertion failed: {e}")
        flash("An error occurred saving your assessment. Please try again.", "error")
        return redirect(url_for('intake'))
        
    return redirect(url_for('result', report_uuid=report_uuid))

@app.route('/result/<report_uuid>')
def result(report_uuid):
    """Retrieve and display a triage report by its unique UUID."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT uuid, symptoms_text, duration, severity, recommended_department, urgency_level, ai_explanation, created_at, heart_rate, sbar_text, red_flags, co_occurring, assigned_facility
            FROM reports
            WHERE uuid = %s
        """, (report_uuid,))
        report = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database query failed: {e}")
        report = None
        
    if not report:
        flash("The requested triage report could not be found.", "error")
        return redirect(url_for('intake'))
        
    # Parse the combined JSON explanation and discuss list
    ai_explanation = report["ai_explanation"]
    discuss_list = []
    
    confidence = 75
    differential_department = None
    differential_confidence = None
    reasoning_steps = []

    try:
        parsed_explanation = json.loads(ai_explanation)
        report["ai_explanation"] = parsed_explanation.get("explanation", ai_explanation)
        discuss_list = parsed_explanation.get("discuss_with_doctor", [])
        confidence = parsed_explanation.get("confidence", 75)
        differential_department = parsed_explanation.get("differential_department")
        differential_confidence = parsed_explanation.get("differential_confidence")
        reasoning_steps = parsed_explanation.get("reasoning_steps", [])
    except json.JSONDecodeError:
        # Fallback if explanation was stored as plain text
        report["ai_explanation"] = ai_explanation
        discuss_list = [
            "Timeline and progression of these symptoms",
            "Any historical instances of similar symptoms",
            "Recommended follow-ups and specialist testing"
        ]
        
    # Format date nicely
    if report.get("created_at"):
        report["created_at"] = report["created_at"].strftime("%Y-%m-%d %H:%M UTC")

    # Deserialize lists from database fields or provide fallbacks
    try:
        red_flags = json.loads(report.get("red_flags")) if report.get("red_flags") else []
    except Exception:
        red_flags = []
        
    try:
        co_occurring = json.loads(report.get("co_occurring")) if report.get("co_occurring") else []
    except Exception:
        co_occurring = []

    # Get the capacity level of the assigned facility to display
    facility_name = report.get("assigned_facility")
    facility_capacity = 50
    dept = report["recommended_department"]
    facilities = HOSPITAL_FACILITIES.get(dept, HOSPITAL_FACILITIES["Default"])
    for f in facilities:
        if f["name"] == facility_name:
            facility_capacity = f["capacity"]
            break
        
    return render_template(
        'result.html',
        report=report,
        discuss_list=discuss_list,
        confidence=confidence,
        differential_department=differential_department,
        differential_confidence=differential_confidence,
        reasoning_steps=reasoning_steps,
        red_flags=red_flags,
        co_occurring=co_occurring,
        facility_capacity=facility_capacity
    )

@app.route('/result/<report_uuid>/pdf')
def result_pdf(report_uuid):
    """Generate and return a downloadable PDF version of the triage report."""
    # --- Fetch report (same logic as /result/<uuid>) ---
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT uuid, symptoms_text, duration, severity, recommended_department, urgency_level, ai_explanation, created_at
            FROM reports
            WHERE uuid = %s
        """, (report_uuid,))
        report = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database query failed for PDF: {e}")
        report = None

    if not report:
        flash("The requested triage report could not be found.", "error")
        return redirect(url_for('intake'))

    # Parse stored JSON
    explanation_text = report["ai_explanation"]
    discuss_list = []
    confidence = 75
    reasoning_steps = []

    try:
        parsed = json.loads(explanation_text)
        explanation_text = parsed.get("explanation", explanation_text)
        discuss_list = parsed.get("discuss_with_doctor", [])
        confidence = parsed.get("confidence", 75)
        reasoning_steps = parsed.get("reasoning_steps", [])
    except json.JSONDecodeError:
        pass

    created_at = report.get("created_at")
    if created_at:
        created_at = created_at.strftime("%Y-%m-%d %H:%M UTC")
    else:
        created_at = "N/A"

    # --- Urgency color mapping ---
    urgency_colors = {
        "low": HexColor("#4A7856"),
        "medium": HexColor("#B8862E"),
        "high": HexColor("#B23A32"),
    }
    urgency_labels = {
        "low": "ROUTINE",
        "medium": "MODERATE",
        "high": "URGENT",
    }
    urgency_level = report["urgency_level"]
    urgency_color = urgency_colors.get(urgency_level, HexColor("#5C6478"))
    urgency_label = urgency_labels.get(urgency_level, urgency_level.upper())

    # --- Build PDF ---
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
    )

    # Styles
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "PDFTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=HexColor("#3D6B63"),
        spaceAfter=4,
        alignment=TA_CENTER,
    )
    style_subtitle = ParagraphStyle(
        "PDFSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=HexColor("#5C6478"),
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    style_meta = ParagraphStyle(
        "PDFMeta",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=8,
        textColor=HexColor("#5C6478"),
        spaceAfter=6,
    )
    style_section_label = ParagraphStyle(
        "PDFSectionLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=HexColor("#5C6478"),
        spaceAfter=3,
        textTransform="uppercase",
    )
    style_department = ParagraphStyle(
        "PDFDepartment",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=HexColor("#3D6B63"),
        spaceAfter=4,
    )
    style_body = ParagraphStyle(
        "PDFBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=HexColor("#1C2438"),
        leading=14,
        spaceAfter=8,
    )
    style_urgency = ParagraphStyle(
        "PDFUrgency",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=urgency_color,
        spaceAfter=10,
    )
    style_list_item = ParagraphStyle(
        "PDFListItem",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        textColor=HexColor("#1C2438"),
        leading=13,
    )
    style_disclaimer = ParagraphStyle(
        "PDFDisclaimer",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=7.5,
        textColor=HexColor("#5C6478"),
        spaceBefore=16,
        leading=10,
    )

    # Flowable elements
    elements = []

    # Header
    elements.append(Paragraph("SymptomTriage AI — Triage Report", style_title))
    elements.append(Paragraph("Clinical Specialist Recommendation &amp; Urgency Triage", style_subtitle))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#E2DED4"), spaceAfter=10))

    # Meta
    elements.append(Paragraph(f"REPORT ID: {report['uuid']}", style_meta))
    elements.append(Paragraph(f"DATE: {created_at}", style_meta))
    elements.append(Spacer(1, 6))

    # Urgency
    elements.append(Paragraph(f"PRIORITY: {urgency_label}", style_urgency))

    # Department
    elements.append(Paragraph("RECOMMENDED SPECIALIST / DEPARTMENT", style_section_label))
    elements.append(Paragraph(report["recommended_department"], style_department))
    elements.append(Paragraph(f"Confidence: {confidence}%", style_meta))
    elements.append(Spacer(1, 6))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#E2DED4"), spaceAfter=10))

    # Explanation
    elements.append(Paragraph("TRIAGE EVALUATION &amp; REASONING", style_section_label))
    elements.append(Paragraph(explanation_text, style_body))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#E2DED4"), spaceAfter=10))

    # Reasoning steps
    if reasoning_steps:
        elements.append(Paragraph("AI CLINICAL REASONING", style_section_label))
        numbered_items = []
        for i, step in enumerate(reasoning_steps, 1):
            numbered_items.append(
                ListItem(Paragraph(step, style_list_item), bulletColor=HexColor("#3D6B63"))
            )
        elements.append(ListFlowable(numbered_items, bulletType='1', start=1, bulletFontSize=9))
        elements.append(Spacer(1, 6))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#E2DED4"), spaceAfter=10))

    # Discuss with doctor
    if discuss_list:
        elements.append(Paragraph("SUGGESTED TOPICS TO DISCUSS WITH A DOCTOR", style_section_label))
        bullet_items = []
        for item in discuss_list:
            bullet_items.append(
                ListItem(Paragraph(item, style_list_item), bulletColor=HexColor("#3D6B63"))
            )
        elements.append(ListFlowable(bullet_items, bulletType='bullet', bulletFontSize=6))
        elements.append(Spacer(1, 6))

    # Disclaimer
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#E2DED4"), spaceAfter=6))
    elements.append(Paragraph(
        "IMPORTANT DISCLAIMER: This tool suggests which specialist to consult and does not provide a medical "
        "diagnosis. Always consult a licensed doctor.",
        style_disclaimer
    ))

    # Build
    doc.build(elements)
    buf.seek(0)

    response = make_response(buf.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="triage-report-{report_uuid}.pdf"'
    return response

@app.route('/dashboard')
def dashboard():
    """Admin impact dashboard showing aggregate triage statistics."""
    stats = {
        "total_reports": 0,
        "today_reports": 0,
        "urgency_counts": {"low": 0, "medium": 0, "high": 0},
        "top_departments": [],
        "most_common_dept": "N/A",
    }

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Total reports
        cursor.execute("SELECT COUNT(*) AS cnt FROM reports")
        stats["total_reports"] = cursor.fetchone()["cnt"]

        # Reports in last 24 hours
        cursor.execute("SELECT COUNT(*) AS cnt FROM reports WHERE created_at >= NOW() - INTERVAL 1 DAY")
        stats["today_reports"] = cursor.fetchone()["cnt"]

        # Urgency breakdown
        cursor.execute("SELECT urgency_level, COUNT(*) AS cnt FROM reports GROUP BY urgency_level")
        for row in cursor.fetchall():
            if row["urgency_level"] in stats["urgency_counts"]:
                stats["urgency_counts"][row["urgency_level"]] = row["cnt"]

        # Top 5 departments
        cursor.execute("""
            SELECT recommended_department, COUNT(*) AS cnt
            FROM reports
            GROUP BY recommended_department
            ORDER BY cnt DESC
            LIMIT 5
        """)
        stats["top_departments"] = cursor.fetchall()

        if stats["top_departments"]:
            stats["most_common_dept"] = stats["top_departments"][0]["recommended_department"]

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Dashboard query failed: {e}")

    return render_template('dashboard.html', stats=stats)

if __name__ == '__main__':
    # Initialize DB schema before starting Flask
    init_db()
    # Start web app
    app.run(host='0.0.0.0', port=5000)