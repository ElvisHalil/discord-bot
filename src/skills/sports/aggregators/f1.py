from datetime import datetime
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator

class F1Aggregator(BaseSportsAggregator):
    """Aggregator for Formula 1 schedule, upcoming Grands Prix, and session timings."""

    def get_schedule(self, year: int = None) -> str:
        """Fetches the Formula 1 season calendar and parses upcoming & past events."""
        if not year:
            year = datetime.now().year

        print(f"  └─ 🏎️ [F1]: Fetching F1 {year} schedule...")
        url = f"https://raw.githubusercontent.com/theOehrly/f1schedule/refs/heads/master/schedule_{year}.json"
        
        try:
            r = requests.get(url, timeout=5)
            if r.status_code != 200:
                return f"Failed to fetch F1 schedule for {year}. Status code: {r.status_code}"
                
            data = r.json()
            
            events = []
            indices = list(data.get("round_number", {}).keys())
            indices.sort(key=lambda x: int(x))
            
            for i in indices:
                round_num = data["round_number"].get(i, 0)
                if round_num == 0:
                    continue
                
                event = {
                    "round": round_num,
                    "country": data["country"].get(i, ""),
                    "location": data["location"].get(i, ""),
                    "event_name": data["event_name"].get(i, ""),
                    "event_date": data["event_date"].get(i, ""),
                    "sessions": []
                }
                
                for s in range(1, 6):
                    s_name = data.get(f"session{s}", {}).get(i)
                    s_date = data.get(f"session{s}_date", {}).get(i)
                    if s_name and s_date:
                        event["sessions"].append(f"{s_name}: {s_date.replace('T', ' ')}")
                        
                events.append(event)
                
            output = f"=== F1 {year} SCHEDULE ===\n\n"
            
            now = datetime.now()
            upcoming = []
            past = []
            
            for e in events:
                try:
                    e_date = datetime.strptime(e['event_date'][:10], "%Y-%m-%d")
                    if e_date >= now:
                        upcoming.append(e)
                    else:
                        past.append(e)
                except Exception:
                    upcoming.append(e)
                    
            output += "--- NEXT UPCOMING RACE ---\n"
            if upcoming:
                next_race = upcoming[0]
                output += f"Round {next_race['round']}: {next_race['event_name']} ({next_race['location']}, {next_race['country']})\n"
                output += f"Date: {next_race['event_date'][:10]}\n"
                output += "Sessions (UTC):\n" + "\n".join([f"  - {s}" for s in next_race['sessions']]) + "\n\n"
                
                if len(upcoming) > 1:
                    output += "--- LATER UPCOMING RACES ---\n"
                    for u in upcoming[1:6]:
                        output += f"Round {u['round']}: {u['event_name']} ({u['event_date'][:10]})\n"
                    output += "\n"
            else:
                output += "No upcoming races found for this year.\n\n"
                
            output += "--- MOST RECENT PAST RACES ---\n"
            if past:
                for p in past[-3:]:
                    output += f"Round {p['round']}: {p['event_name']} ({p['event_date'][:10]})\n"
            else:
                output += "No past races yet.\n"
                
            return output
            
        except Exception as e:
            return f"Error parsing F1 schedule: {e}"
