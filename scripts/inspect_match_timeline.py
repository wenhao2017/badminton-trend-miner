import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import SessionLocal
from app.api_clients.sportradar_badminton_api import SportradarBadmintonApi

def main():
    db = SessionLocal()
    api = SportradarBadmintonApi()
    try:
        match_id = "sr:sport_event:67334642"
        print(f"Fetching timeline for match: {match_id}")
        timeline_json = api.get_match_timeline(db, match_id)
        
        timeline = timeline_json.get("timeline", [])
        print(f"Total events in timeline: {len(timeline)}")
        
        # Let's count different event types
        event_types = {}
        for ev in timeline:
            t = ev.get("type")
            event_types[t] = event_types.get(t, 0) + 1
        print("Event types breakdown:", event_types)
        
        # Print the first 40 events in detail
        print("\nFirst 40 events details:")
        for idx, ev in enumerate(timeline[:40]):
            t = ev.get("type")
            if t == "point":
                print(f"#{idx} TYPE: point | ID: {ev.get('id')} | comp: {ev.get('competitor')} | server: {ev.get('server')} | home_score: {ev.get('home_score')} | away_score: {ev.get('away_score')} | result: {ev.get('result')}")
            elif t == "period_score":
                print(f"#{idx} TYPE: period_score | period: {ev.get('period')} | home_score: {ev.get('home_score')} | away_score: {ev.get('away_score')} | match_status: {ev.get('match_status')}")
            elif t == "game_score":
                print(f"#{idx} TYPE: game_score | home_score: {ev.get('home_score')} | away_score: {ev.get('away_score')}")
            else:
                print(f"#{idx} TYPE: {t} | keys: {list(ev.keys())} | data: {ev}")

    except Exception as e:
        print(f"Error occurred: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
