# Pointe-Claire Waste Calendars

ICS calendar files for Pointe-Claire residential waste collection — Sector A and Sector B.

## Calendars

Subscribe using these raw URLs:

| Sector | URL |
|--------|-----|
| A | `https://raw.githubusercontent.com/jordanconway/pointe-claire-waste-calendars/main/pointe-claire-a.ics` |
| B | `https://raw.githubusercontent.com/jordanconway/pointe-claire-waste-calendars/main/pointe-claire-b.ics` |

## Schedule

| Collection | Sector A | Sector B |
|------------|----------|----------|
| Organic waste | Weekly Monday | Weekly Monday |
| Recyclables | Weekly Wednesday | Weekly Wednesday |
| Bulky items | 1st Wednesday of month | 1st Wednesday of month |
| Household waste | Biweekly Tuesday | Biweekly Thursday |
| Christmas trees | January (specific dates) | January (specific dates) |

## Automation

A GitHub Actions workflow runs weekly on Mondays. It downloads the official PDFs from the [Pointe-Claire city website](https://www.pointe-claire.ca), parses the collection schedule, and commits updated ICS files if changes are detected.

## Local Update

```bash
pip install -r requirements.txt
python update_calendars.py
```
