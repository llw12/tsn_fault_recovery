# Chart map

- **Section:** TT delivery timeline
- **Question:** Which generated TT packets were delivered or lost, and what delay did delivered packets experience around the 5 ms failure and 6 ms switch?
- **Family/type:** ordered time comparison; three aligned scatter panels
- **Fields:** scenario, sequence/send time, receive status, end-to-end delay, loss eligibility
- **Supported claim:** failure stops TT delivery; manual recovery loses one eligible TT packet and resumes delivery after the profile switch.
- **Palette/non-color encoding:** blue circles for delivery, orange crosses for loss, open grey squares for tail exclusion; dashed and dotted lines distinguish failure and switch references.
- **QA surface:** `tt_timeline.png` at 2160×1476 px (12×8.2 in, 180 dpi).
