import openai
import os
from dotenv import load_dotenv

load_dotenv()

# Your API key
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY", "939ebbeb-e6f4-402b-9b37-91e6c43dc926")

client = openai.OpenAI(
    api_key=SAMBANOVA_API_KEY,
    base_url="https://api.sambanova.ai/v1",
)

try:
    print("Testing SambaNova API...")
    response = client.chat.completions.create(
        model="Meta-Llama-3.1-8B-Instruct",
        messages=[
            {"role": "user", "content": "Say hello"}
        ],
        temperature=0.7,
        max_tokens=50
    )
    
    print("✅ API is working!")
    print(f"Response: {response.choices[0].message.content}")
    print(f"\nAPI Key (first 10 chars): {SAMBANOVA_API_KEY[:10]}...")
    
except Exception as e:
    print("❌ API Error:")
    print(str(e))
    
    if "401" in str(e) or "Unauthorized" in str(e):
        print("\n🔑 Your API key is INVALID or EXPIRED")
        print("→ You need to generate a NEW API key")
    elif "429" in str(e) or "rate_limit" in str(e):
        print("\n⚠️ Rate limit exceeded")
        print("→ Wait 5-10 minutes and try again")
    elif "404" in str(e):
        print("\n🔍 Model or endpoint not found")
        print("→ Check if the model name is correct")
    else:
        print("\n🌐 Network or other error")
        print("→ Check your internet connection")