import urllib.request
from bs4 import BeautifulSoup
import json

try:
    req = urllib.request.Request('https://www.beatport.com/top-100', headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    html = urllib.request.urlopen(req).read()
    soup = BeautifulSoup(html, 'html.parser')
    script = soup.find('script', id='__NEXT_DATA__')
    if script:
        data = json.loads(script.string)
        # We need to find the queries array
        queries = data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
        found = False
        for q in queries:
            if 'data' in q.get('state', {}) and 'results' in q['state']['data']:
                tracks = q['state']['data']['results']
                if tracks and 'name' in tracks[0]:
                    found = True
                    for t in tracks[:5]:
                        artist_names = ", ".join([a['name'] for a in t.get('artists', [])])
                        genre_name = t.get('genre', {}).get('name', 'Unknown')
                        print(f"{t['name']} by {artist_names} (Genre: {genre_name})")
                    break
        if not found:
            print("Tracks not found in JSON structure")
    else:
        print("No __NEXT_DATA__ script found")
except Exception as e:
    print(f"Error: {e}")

