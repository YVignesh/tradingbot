import yfinance as yf
import pandas as pd
from google import genai
from datetime import datetime, timedelta

# --- CONFIGURATION ---
GEMINI_API_KEY = "AIzaSyD4dVUStb3XqKzJn_o_bOVhMYSYx9yjCyg"
# For the full Nifty 200, you'd load a CSV. Here's a sample subset:
TICKERS = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "TATAMOTORS.NS", "ITC.NS"]
MODEL_ID = "gemini-3-pro" # Use Flash for speed and cost-efficiency

client = genai.Client(api_key=GEMINI_API_KEY)

def fetch_stock_news(ticker):
    """Fetches latest headlines using yfinance."""
    print(f"Fetching news for {ticker}...")
    stock = yf.Ticker(ticker)
    news = stock.news
    
    # Extract only the title and publisher from the last 5 news items
    headlines = []
    for item in news[:5]:
        headlines.append(f"- {item['title']} (Source: {item['publisher']})")
    
    return "\n".join(headlines) if headlines else "No recent news found."

def analyze_and_rank(all_news_data):
    """Sends news data to Gemini for scoring and ranking."""
    
    system_prompt = """
    You are a Senior Equity Research Analyst for the Indian Market. 
    Analyze the provided news for Nifty 200 stocks.
    
    TASK:
    1. Assign a Sentiment Score (-10 to +10) based on financial impact.
    2. Identify the 'Primary Catalyst' (e.g., Earnings, Order Win, Regulatory).
    3. Rank the Top 3 stocks for a potential bullish move.
    
    SCORING LOGIC:
    - Focus on fundamental changes (profits, new contracts, management changes).
    - Ignore 'price action' news (e.g., "Stock hits 52-week high").
    
    OUTPUT:
    Return ONLY a Markdown table with: | Ticker | Score | Catalyst | Reasoning |
    """

    prompt = f"Here is the latest news data for analysis:\n\n{all_news_data}"
    
    response = client.models.generate_content(
        model=MODEL_ID,
        config={'system_instruction': system_prompt, 'temperature': 0.1},
        contents=prompt
    )
    
    return response.text

def main():
    # 1. Gather News
    compiled_data = ""
    for ticker in TICKERS:
        news_summary = fetch_stock_news(ticker)
        compiled_data += f"\n--- {ticker} ---\n{news_summary}\n"
    
    # 2. Run Analysis
    print("\n--- Running AI Sentiment Analysis ---\n")
    report = analyze_and_rank(compiled_data)
    
    # 3. Output Result
    print(report)

if __name__ == "__main__":
    main()