import unittest
import json
from app import app, get_fallback_mock_response

class SymptomTriageTestCase(unittest.TestCase):
    def setUp(self):
        # Configure the Flask app for testing
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()

    def test_intake_page_loads(self):
        """Test that the homepage/intake page loads successfully."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SymptomTriage AI', response.data)
        self.assertIn(b'Patient Symptom Intake Chart', response.data)

    def test_fallback_urgency_high(self):
        """Test fallback heuristic logic correctly flags emergency symptoms as high urgency."""
        res_chest_pain = get_fallback_mock_response("sudden sharp chest pain", 9, [])
        self.assertEqual(res_chest_pain["urgency_level"], "high")
        self.assertIn("emergency", res_chest_pain["explanation"].lower())
        self.assertIn("sbar_text", res_chest_pain)
        self.assertIn("red_flags", res_chest_pain)
        self.assertIn("co_occurring", res_chest_pain)
        self.assertTrue(len(res_chest_pain["red_flags"]) > 0)
        
        res_sob = get_fallback_mock_response("difficulty breathing since an hour", 7, ["Shortness of Breath"])
        self.assertEqual(res_sob["urgency_level"], "high")

    def test_fallback_urgency_medium(self):
        """Test fallback heuristic logic correctly flags medium urgency symptoms."""
        res_fever = get_fallback_mock_response("mild fever and headache", 5, ["Fever"])
        self.assertEqual(res_fever["urgency_level"], "medium")
        self.assertIn("moderate", res_fever["explanation"].lower())
        self.assertIn("sbar_text", res_fever)
        self.assertTrue(len(res_fever["co_occurring"]) > 0)

    def test_fallback_urgency_low(self):
        """Test fallback heuristic logic correctly flags low urgency symptoms."""
        res_itch = get_fallback_mock_response("slightly itchy arm", 2, [])
        self.assertEqual(res_itch["urgency_level"], "low")
        self.assertIn("routine", res_itch["explanation"].lower())
        self.assertIn("sbar_text", res_itch)
        self.assertTrue(isinstance(res_itch["red_flags"], list))

if __name__ == '__main__':
    unittest.main()
