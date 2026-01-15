#!/usr/bin/env python3
"""
Direct API Testing - Bypass test runner async issues
Tests bug fixes by making direct API calls
"""

import requests
import json
import time
from datetime import datetime

API_BASE = "https://gemini-chatbot-480267397633.us-central1.run.app"

class DirectAPITester:
    def __init__(self):
        self.session = requests.Session()
        self.session_id = f"test_{int(time.time())}"
        
    def test_bug_fixes(self):
        """Test all critical bug fixes"""
        print("=" * 80)
        print("🧪 DIRECT API TEST - Bug Fix Validation")
        print("=" * 80)
        print(f"Session ID: {self.session_id}")
        print(f"API: {API_BASE}")
        print()
        
        # Test 1: Trial Search (Bug #4 - Correct trial selection)
        print("1️⃣  Testing Bug #4 Fix: Trial Search & Selection")
        print("-" * 80)
        self.test_trial_search()
        print()
        
        # Test 2: Contact Collection (Bug #5 - Implicit consent)
        print("2️⃣  Testing Bug #5 Fix: Contact Collection")
        print("-" * 80)
        self.test_contact_collection()
        print()
        
        print("=" * 80)
        print("✅ Direct API Testing Complete!")
        print("=" * 80)
    
    def test_trial_search(self):
        """Test that correct trials are selected"""
        
        # Search for diabetes trials
        print("Searching for 'diabetes' trials in 'Atlanta'...")
        
        response = self.chat("I'm looking for diabetes trials in Atlanta")
        
        if response:
            print(f"✓ API Response received ({len(response)} chars)")
            
            # Check if response mentions diabetes trials
            response_lower = response.lower()
            if "diabetes" in response_lower or "diabetic" in response_lower:
                print("✓ Response mentions diabetes - trial filtering working!")
            else:
                print("⚠ Response doesn't mention diabetes - check trial selection")
            
            # Check if unrelated trials are shown
            unrelated = ["alopecia", "hair loss", "gout", "psoriasis"]
            found_unrelated = [term for term in unrelated if term in response_lower]
            if found_unrelated:
                print(f"❌ Bug #4 Issue: Unrelated trials found: {found_unrelated}")
            else:
                print("✓ No unrelated trials shown - Bug #4 fix working!")
        else:
            print("❌ No response from API")
    
    def test_contact_collection(self):
        """Test contact collection with implicit consent"""
        
        # Start a new session for contact testing
        test_session = f"contact_test_{int(time.time())}"
        
        print("Starting contact collection flow...")
        print("(This is a simplified test - full flow would need prescreening)")
        
        # Test that API is accessible for contact flow
        response = self.chat("yes", session_id=test_session)
        
        if response:
            print("✓ API accessible for contact collection")
            print("Note: Full Bug #5 test requires completing prescreening first")
        else:
            print("❌ API not responding")
    
    def chat(self, message, session_id=None):
        """Send a chat message to the API"""
        session_id = session_id or self.session_id
        
        try:
            response = self.session.post(
                f"{API_BASE}/api/gemini/chat",
                json={
                    "message": message,
                    "session_id": session_id
                },
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("response", "")
            else:
                print(f"❌ API Error: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Request failed: {e}")
            return None

def main():
    tester = DirectAPITester()
    tester.test_bug_fixes()

if __name__ == "__main__":
    main()
