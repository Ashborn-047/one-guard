import urllib.request
import json
import sys

def test_endpoint(url):
    print(f"Testing URL: {url}")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            status_code = response.getcode()
            body = response.read().decode('utf-8')
            print(f"  Status code: {status_code}")
            # Try parsing as JSON
            data = json.loads(body)
            print(f"  Parsed JSON successfully! Keys/Length: {list(data.keys()) if isinstance(data, dict) else len(data)}")
            if isinstance(data, dict):
                for k, v in list(data.items())[:3]:
                    val_str = str(v)[:60] + ('...' if len(str(v)) > 60 else '')
                    print(f"    - {k}: {val_str}")
            elif isinstance(data, list):
                print(f"    - List size: {len(data)}")
                if len(data) > 0:
                    print(f"    - First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0])}")
            return True
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

def main():
    endpoints = [
        "http://localhost:8000/api/status",
        "http://localhost:8000/api/metrics",
        "http://localhost:8000/api/positions",
        "http://localhost:8000/api/chart?symbol=BTC/USDT",
        "http://localhost:8000/api/trades"
    ]
    
    all_ok = True
    for ep in endpoints:
        ok = test_endpoint(ep)
        if not ok:
            all_ok = False
        print("-" * 50)
        
    if all_ok:
        print("All endpoints tested successfully!")
        sys.exit(0)
    else:
        print("Some endpoints failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
