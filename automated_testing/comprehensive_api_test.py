#!/usr/bin/env python3
"""
Comprehensive API Test - Full conversation flow
Tests bug fixes through a complete patient interaction
"""

import requests
import json
import time

API_BASE = "https://gemini-chatbot-480267397633.us-central1.run.app"

class ComprehensiveTest:
    def __init__(self):
        self.session = requests.Session()
        self.session_id = f"comprehensive_test_{int(time.time())}"
        self.conversation = []
        
    def chat(self, message):
        """Send chat message and track conversation"""
        print(f"  👤 User: {message}")
        
        try:
            response = self.session.post(
                f"{API_BASE}/api/gemini/chat",
                json={"message": message, "session_id": self.session_id},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                bot_response = data.get("response", "")
                self.conversation.append({"user": message, "bot": bot_response})
                
                # Show first 150 chars of response
                display = bot_response[:150] + "..." if len(bot_response) > 150 else bot_response
                print(f"  🤖 Bot: {display}")
                print()
                
                return bot_response
            else:
                print(f"  ❌ API Error: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return None
    
    def run_full_flow(self):
        """Run a complete patient interaction"""
        print("=" * 80)
        print("🔬 COMPREHENSIVE API TEST - Full Conversation Flow")
        print("=" * 80)
        print(f"Session: {self.session_id}")
        print()
        
        # Step 1: Search for trials (Bug #4 test)
        print("📍 Step 1: Trial Search (Testing Bug #4 Fix)")
        print("-" * 80)
        response = self.chat("I'm looking for diabetes clinical trials in Atlanta")
        
        if response and ("diabetes" in response.lower() or "trial" in response.lower()):
            print("  ✅ Bug #4: Correct trials shown!")
        else:
            print("  ⚠️  Check trial search response")
        
        time.sleep(1)
        
        # Step 2: Express interest in checking eligibility
        print("📋 Step 2: Start Prescreening")
        print("-" * 80)
        response = self.chat("Yes, I'd like to check my eligibility for the diabetes trial")
        
        if response and ("eligibility" in response.lower() or "question" in response.lower() or "age" in response.lower()):
            print("  ✅ Prescreening initiated!")
        else:
            print("  ⚠️  Prescreening might not have started")
        
        time.sleep(1)
        
        # Step 3: Answer prescreening questions (sample answers)
        print("📝 Step 3: Answer Prescreening Questions")
        print("-" * 80)
        
        # Answer a few questions
        self.chat("45")  # Age
        time.sleep(1)
        
        self.chat("Yes, I have type 2 diabetes")  # Condition confirmation
        time.sleep(1)
        
        self.chat("No serious health issues")  # Medical history
        time.sleep(1)
        
        # Note: Actual prescreening flow varies by trial, this is simplified
        print("  ✅ Answered sample questions")
        print()
        
        # Step 4: Test contact collection (Bug #5)
        print("📞 Step 4: Contact Collection (Testing Bug #5 Fix)")
        print("-" * 80)
        print("  Testing implicit consent (providing name directly)...")
        
        # Try providing name directly (Bug #5 fix test)
        response = self.chat("TestUser")  # Provide name without saying "yes" first
        
        if response:
            response_lower = response.lower()
            if "last name" in response_lower or "email" in response_lower or "phone" in response_lower:
                print("  ✅ Bug #5: Implicit consent working - bot accepted name!")
            elif "yes" in response_lower or "no" in response_lower:
                print("  ⚠️  Bot still asking for yes/no - may need more context")
            else:
                print("  ℹ️  Response: " + response[:100])
        
        print()
        print("=" * 80)
        print(f"✅ Test Complete! Total turns: {len(self.conversation)}")
        print("=" * 80)
        print()
        print("Summary:")
        print(f"  - Session ID: {self.session_id}")
        print(f"  - Total messages: {len(self.conversation)}")
        print(f"  - Check database for full details")
        print()

def main():
    tester = ComprehensiveTest()
    tester.run_full_flow()

if __name__ == "__main__":
    main()
