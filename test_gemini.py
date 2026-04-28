"""
test_gemini.py — Quick test to verify Gemini API key works.
Uses the new google-genai SDK (replaces deprecated google.generativeai).
Run: python test_gemini.py
"""
 
import os
from dotenv import load_dotenv
from google import genai
 
load_dotenv()
 
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("ERROR: GEMINI_API_KEY not found in .env file.")
    print("Make sure your .env contains: GEMINI_API_KEY=your_key_here")
    exit(1)
 
print(f"API key found: {api_key[:8]}...{api_key[-4:]}")
print("Connecting to Gemini...")
 
try:
    client   = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="In one sentence, confirm you are Gemini and ready to analyze financial data."
    )
    print(f"\n✅ Gemini connected successfully!")
    print(f"Model: gemini-2.5-flash")
    print(f"Response: {response.text}")
 
except Exception as e:
    print(f"\n❌ Connection failed: {e}")
    print("\nTrying to list available models...")
    try:
        client = genai.Client(api_key=api_key)
        for m in client.models.list():
            if "generateContent" in (m.supported_actions or []):
                print(f"  Available: {m.name}")
    except Exception as e2:
        print(f"Could not list models: {e2}")