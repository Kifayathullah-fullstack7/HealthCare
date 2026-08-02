import requests
import re

def test_e2e_flow():
    base_url = "http://127.0.0.1:5000"
    
    # 1. Test Intake Page Loading
    print("Testing GET / ...")
    r = requests.get(f"{base_url}/")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert "SymptomTriage AI" in r.text
    print("Intake page loads successfully.")
    
    # 2. Test Case 1: Emergency Symptom (Chest Pain)
    print("\nTesting POST /analyze (Emergency Case) ...")
    payload = {
        "symptoms_text": "sudden severe chest pain, shortness of breath, radiating to the left arm",
        "duration": "2 hours",
        "severity": "9",
        "associated_symptoms": ["Chest Tightness", "Shortness of Breath"]
    }
    
    # Send post request, do not follow redirect automatically so we can check redirect
    r = requests.post(f"{base_url}/analyze", data=payload, allow_redirects=False)
    assert r.status_code == 302, f"Expected 302 redirect, got {r.status_code}"
    redirect_url = r.headers.get("Location")
    print(f"Redirected to: {redirect_url}")
    
    # Follow redirect
    r_result = requests.get(base_url + redirect_url)
    assert r_result.status_code == 200, f"Expected 200 for result page, got {r_result.status_code}"
    
    # Check for triage band with URGENT
    assert "PRIORITY: URGENT" in r_result.text, "Expected PRIORITY: URGENT in response HTML"
    assert "triage-band high" in r_result.text, "Expected 'triage-band high' class in response HTML"
    assert "Emergency Medicine / Cardiology" in r_result.text, "Expected department in response HTML"
    assert "Always consult a licensed doctor" in r_result.text, "Expected disclaimer in response HTML"
    print("Emergency case E2E flow verified successfully!")

    # 3. Test Case 2: Routine Symptom (Dry Itchy Skin)
    print("\nTesting POST /analyze (Routine Case) ...")
    payload_routine = {
        "symptoms_text": "dry itchy skin",
        "duration": "3 days",
        "severity": "2",
        "associated_symptoms": []
    }
    
    r = requests.post(f"{base_url}/analyze", data=payload_routine, allow_redirects=False)
    assert r.status_code == 302
    redirect_url_routine = r.headers.get("Location")
    print(f"Redirected to: {redirect_url_routine}")
    
    r_result_routine = requests.get(base_url + redirect_url_routine)
    assert r_result_routine.status_code == 200
    
    # Check for triage band with ROUTINE
    assert "PRIORITY: ROUTINE" in r_result_routine.text, "Expected PRIORITY: ROUTINE in response HTML"
    assert "triage-band low" in r_result_routine.text, "Expected 'triage-band low' class in response HTML"
    print("Routine case E2E flow verified successfully!")

    print("\nAll E2E checks passed!")

if __name__ == "__main__":
    try:
        test_e2e_flow()
    except AssertionError as e:
        print(f"E2E Verification Failed: {e}")
    except Exception as e:
        print(f"Error during E2E verification: {e}")
