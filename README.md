# Pointe-Claire Waste Calendars

ICS calendar files for Pointe-Claire residential waste collection — Sector A and Sector B.

## Calendars

Subscribe using these raw URLs:

| Sector | URL |
|--------|-----|
| A | `https://raw.githubusercontent.com/jordanconway/pointe-claire-waste-calendars/main/pointe-claire-a.ics` |
| B | `https://raw.githubusercontent.com/jordanconway/pointe-claire-waste-calendars/main/pointe-claire-b.ics` |

## Schedule

_Accurate as of September 27, 2026, based on the City of Pointe-Claire 2026–2027 collection calendar and municipal guidelines._

| Collection | Sector A | Sector B |
|------------|----------|----------|
| Organic waste | Weekly Monday (biweekly Dec–Mar) | Weekly Monday (biweekly Dec–Mar) |
| Recyclables | Weekly Thursday | Weekly Thursday |
| Bulky items | 2nd Wednesday of month (Apr–Oct) | 2nd Wednesday of month (Apr–Oct) |
| Household waste | Weekly Tuesday (Jun–Oct)<br>Biweekly Tuesday (Nov–May) | Weekly Wednesday (Jun–Oct)<br>Biweekly Tuesday (Nov–May) |
| Branch collection | May 1 – Oct 31 (curbside continuous) | May 1 – Oct 31 (curbside continuous) |
| Leaf collection | Biweekly Monday in Spring & Autumn (specific dates) | Biweekly Monday in Spring & Autumn (specific dates) |
| Mattress / Box-spring | 1st Wednesday of month (May, Jul, Oct) | 1st Thursday of month (May, Jul, Oct) |
| Christmas trees | January (specific dates) | January (specific dates) |
| Ecocentre drop-offs | Specific Saturdays (May, Jul, Sep, Oct) | Specific Saturdays (May, Jul, Sep, Oct) |

## Home Assistant Blueprint

An automation blueprint is provided in [`blueprints/automation/waste_collection_reminder.yaml`](file:///Users/jconway/git/github/jordanconway/pointe-claire-waste-calendars/blueprints/automation/waste_collection_reminder.yaml) to notify you the evening before collection day with custom icons and colors for each collection type (compost, recycling, garbage, bulky items, leaves, and Christmas trees).

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fjordanconway%2Fpointe-claire-waste-calendars%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fwaste_collection_reminder.yaml)

### Manual Import

1. In Home Assistant, navigate to **Settings > Automations & scenes > Blueprints**.
2. Click **Import Blueprint** in the bottom right corner.
3. Paste the URL:
   ```
   https://github.com/jordanconway/pointe-claire-waste-calendars/blob/main/blueprints/automation/waste_collection_reminder.yaml
   ```
4. Click **Preview** and then **Import Blueprint**.
5. Create an automation from the blueprint, selecting your waste calendar entity and desired notification destination (mobile device or notify service).


## Scheduled Updates

A GitHub Actions workflow runs weekly on Mondays. It downloads the official PDFs from the [Pointe-Claire city website](https://www.pointe-claire.ca), parses the collection schedule, and commits updated ICS files if changes are detected.

## Local Update

```bash
pip install -r requirements.txt
python update_calendars.py
```

