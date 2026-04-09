# /// script
# dependencies = [
#   "icalendar",
#   "python-dateutil",
# ]
# ///

from icalendar import Calendar, Event
from datetime import datetime, timedelta
import os

def create_waste_calendar(sector):
    sector = sector.upper()
    cal = Calendar()
    cal.add('prodid', f'-//Pointe-Claire//Waste Collection Sector {sector}//EN')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', f'Pointe-Claire Sector {sector} Waste 2026-2027')

    # Shared boundary dates from the PDF [cite: 6, 8, 88, 90]
    end_date = datetime(2027, 3, 31, 23, 59, 59)

    # 1. Organic Waste (Matières organiques) - Every Monday [cite: 16, 17, 100, 102]
    organic = Event()
    organic.add('summary', f'Organic Waste - Sector {sector}')
    organic.add('rrule', {'freq': 'weekly', 'byday': 'MO', 'until': end_date})
    organic.add('dtstart', datetime(2026, 4, 6, 7, 0, 0))
    organic.add('duration', timedelta(hours=1))
    cal.add_component(organic)

    # 2. Recyclables (Matières recyclables) - Every Wednesday [cite: 18, 19, 112, 113]
    recycling = Event()
    recycling.add('summary', f'Recyclables - Sector {sector}')
    recycling.add('rrule', {'freq': 'weekly', 'byday': 'WE', 'until': end_date})
    recycling.add('dtstart', datetime(2026, 4, 1, 7, 0, 0))
    recycling.add('duration', timedelta(hours=1))
    cal.add_component(recycling)

    # 3. Bulky Items (Encombrants) - First Wednesday of the month [cite: 35, 36, 118, 119]
    bulky = Event()
    bulky.add('summary', f'Bulky Items - Sector {sector}')
    bulky.add('rrule', {'freq': 'monthly', 'byday': '1WE', 'until': end_date})
    bulky.add('dtstart', datetime(2026, 4, 1, 7, 0, 0))
    bulky.add('duration', timedelta(hours=1))
    cal.add_component(bulky)

    # 4. Household Waste (Déchets domestiques) - Sector Specific [cite: 32, 114, 115]
    waste = Event()
    waste.add('summary', f'Household Waste - Sector {sector}')
    if sector == 'A':
        # Every 2nd Tuesday starting April 7 
        waste.add('dtstart', datetime(2026, 4, 7, 7, 0, 0))
        waste.add('rrule', {'freq': 'weekly', 'interval': 2, 'byday': 'TU', 'until': end_date})
    else:
        # Every 2nd Thursday starting April 9 
        waste.add('dtstart', datetime(2026, 4, 9, 7, 0, 0))
        waste.add('rrule', {'freq': 'weekly', 'interval': 2, 'byday': 'TH', 'until': end_date})
    waste.add('duration', timedelta(hours=1))
    cal.add_component(waste)

    # 5. Christmas Trees (Arbres de Noël) [cite: 49, 50, 138, 139]
    tree_dates = [datetime(2027, 1, 7), datetime(2027, 1, 14)] if sector == 'A' else [datetime(2027, 1, 6), datetime(2027, 1, 13)]
    for d in tree_dates:
        tree = Event()
        tree.add('summary', f'Christmas Tree Collection - Sector {sector}')
        tree.add('dtstart', d.replace(hour=7))
        tree.add('duration', timedelta(hours=1))
        cal.add_component(tree)

    filename = f'PointeClaire_Sector_{sector}_2026.ics'
    with open(filename, 'wb') as f:
        f.write(cal.to_ical())
    print(f"✅ Created: {filename}")

if __name__ == "__main__":
    create_waste_calendar('A')
    create_waste_calendar('B')
