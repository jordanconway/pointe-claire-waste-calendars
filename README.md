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
| Recyclables | Weekly Thursday | Weekly Thursday |
| Bulky items | 1st Wednesday of month (Apr–Oct) | 1st Wednesday of month (Apr–Oct) |
| Household waste | Weekly Tuesday (Jun–Aug)<br>Biweekly Tuesday (Sep–May) | Weekly Wednesday (Jun–Aug)<br>Biweekly Tuesday (Sep–May) |
| Christmas trees | January (specific dates) | January (specific dates) |
| Leaf collection | Seasonal Spring & Autumn (specific dates) | Seasonal Spring & Autumn (specific dates) |
| Mattress / Box-spring | January (specific date) | January (specific date) |
| Ecocentre drop-offs | Specific Saturdays (May, July, Sep, Oct) | Specific Saturdays (May, July, Sep, Oct) |

## Automation

A GitHub Actions workflow runs weekly on Mondays. It downloads the official PDFs from the [Pointe-Claire city website](https://www.pointe-claire.ca), parses the collection schedule, and commits updated ICS files if changes are detected.

## Local Update

```bash
pip install -r requirements.txt
python update_calendars.py
```
