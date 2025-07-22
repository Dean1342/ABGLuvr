# Currency conversion integration using ExchangeRate-API
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")

async def convert_currency(amount, from_currency, to_currency):
    if not EXCHANGE_RATE_API_KEY or EXCHANGE_RATE_API_KEY == "your_exchangerate_api_key_here":
        # Use free tier endpoint if no API key
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency.upper()}"
    else:
        # Use paid tier endpoint with API key
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/{from_currency.upper()}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        
        # Check if the response is successful
        if EXCHANGE_RATE_API_KEY and EXCHANGE_RATE_API_KEY != "your_exchangerate_api_key_here":
            # Paid API response format
            if data.get('result') != 'success':
                return {"error": f"API Error: {data.get('error-type', 'Unknown error')}"}
            rates = data.get('conversion_rates', {})
        else:
            # Free API response format
            if 'rates' not in data:
                return {"error": "Invalid currency code or API response"}
            rates = data['rates']
        
        to_currency_upper = to_currency.upper()
        
        if to_currency_upper not in rates:
            return {"error": f"Currency '{to_currency}' not found"}
        
        exchange_rate = rates[to_currency_upper]
        converted_amount = round(amount * exchange_rate, 2)
        
        return {
            "original_amount": amount,
            "converted_amount": converted_amount,
            "from_currency": from_currency.upper(),
            "to_currency": to_currency_upper,
            "exchange_rate": exchange_rate,
            "success": True
        }
        
    except httpx.RequestError as e:
        return {"error": f"Network error: {str(e)}"}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP error: {e.response.status_code}"}
    except KeyError as e:
        return {"error": f"Missing key in API response: {str(e)}"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": f"Unexpected error: {str(e)}"}
