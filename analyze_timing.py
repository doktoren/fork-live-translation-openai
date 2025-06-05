#!/usr/bin/env python3

import re
from datetime import datetime, timedelta

# Expected timing from SSML (in seconds from start)
expected_timings = [
    (2, "Hi Thomas, thanks for calling."),
    (12, "You're saying the alarm is triggering even though the temperature is within the expected range?"),
    (19, "I will continue talking for a bit longer just to check how it works with semantic voice activity detection."),
    (23, "Got it."),
    (27, "Let me pull up the logs..."),
    (38, "Okay, I see repeated alerts for unit FZ-MJ-3, starting around six thirty this morning."),
    (44, "And you mentioned restarting—did you power-cycle just the sensor or the whole controller?"),
    (55, "Understood."),
    (63, "It might be a drift issue. I'll disable auto-escalation for this unit temporarily."),
    (76, "Can you send me a photo of the display on the controller?"),
    (87, "Received."),
    (96, "Thanks. I see the internal reading says minus twelve point seven degrees Celsius, which doesn't match your probe."),
    (115, "Yes, I'll send you a replacement sensor. In the meantime, I'll flag this unit to ignore alerts below minus ten degrees until tomorrow."),
    (128, "You're welcome, Thomas. Have a good day.")
]

# Actual timestamps from log (converted to Danish translations)
actual_log_data = [
    ("09:29:17.791", "Du siger, at alarmen går i gang, selvom temperaturen er inden for det forventede område?"),
    ("09:29:25.292", "Jeg vil fortsætte med at tale lidt længere for at tjekke, hvordan det fungerer med den semantiske stemmeaktivitetsdetektion."),
    ("09:29:27.076", "Forstået."),
    ("09:29:30.637", "Lad mig trække planen op."),
    ("09:29:44.575", "Okay, jeg kan se gentagne alarmer for enhed SDMJ3, som begynder omkring klokken 6:30 i morges."),
    ("09:29:49.113", "Når du nævner det startede, mener du så, at du genstartede kun sensoren eller hele controlleren?"),
    ("09:30:00.452", "Forstået."),
    ("09:30:08.828", "Det kan være et driftproblem. Jeg vil midlertidigt deaktivere automatisk eskalering for denne enhed."),
    ("09:30:22.219", "Jeg kan desværre ikke tage eller sende billeder."),
    ("09:30:32.298", "Forstået."),
    ("09:30:42.314", "Jeg kan se, at intern aflæsning siger minus 12,7 grader celsius, hvilket ikke stemmer overens med din forventning."),
    ("09:31:00.731", "Ja, jeg sender en erstatningssensor. I mellemtiden vil jeg markere denne enhed til at ignorere alarmer under minus 10 grader indtil i morgen."),
    ("09:31:14.015", "Selv tak, Thomas. Ha' en god dag!")
]

def parse_time(time_str):
    """Parse time string like '09:29:17.791' to datetime"""
    return datetime.strptime(time_str, "%H:%M:%S.%f")

def analyze_timing():
    print("Timing Analysis: Expected vs Actual")
    print("=" * 80)
    
    # Assume the log starts at some baseline time
    if actual_log_data:
        baseline_time = parse_time(actual_log_data[0][0])
        print(f"Baseline time (first translation): {baseline_time.strftime('%H:%M:%S.%f')[:-3]}")
        print()
    
    # Skip the first entry since we're missing it in the log
    expected_subset = expected_timings[1:]  # Skip "Hi Thomas, thanks for calling"
    
    print("1. Translation Completion Times:")
    print(f"{'Expected (s)':<12} {'Actual Time':<12} {'Actual (s)':<10} {'Drift (s)':<10} {'Translation'}")
    print("-" * 120)
    
    actual_times = []
    expected_times = []
    
    for i, ((expected_sec, expected_text), (actual_time_str, actual_text)) in enumerate(zip(expected_subset, actual_log_data)):
        actual_time = parse_time(actual_time_str)
        actual_seconds = (actual_time - baseline_time).total_seconds()
        drift = actual_seconds - expected_sec
        
        actual_times.append(actual_seconds)
        expected_times.append(expected_sec)
        
        print(f"{expected_sec:<12} {actual_time_str:<12} {actual_seconds:<10.1f} {drift:<10.1f} {actual_text[:60]}...")
    
    print("\n2. Gap Analysis (time between translations):")
    print(f"{'Segment':<20} {'Expected Gap (s)':<15} {'Actual Gap (s)':<15} {'Gap Drift (s)':<15}")
    print("-" * 80)
    
    for i in range(1, len(actual_times)):
        expected_gap = expected_times[i] - expected_times[i-1]
        actual_gap = actual_times[i] - actual_times[i-1]
        gap_drift = actual_gap - expected_gap
        
        print(f"Translation {i} -> {i+1}:{'':<12} {expected_gap:<15.1f} {actual_gap:<15.1f} {gap_drift:<15.1f}")
    
    print("\n3. Cumulative Delay Analysis:")
    print(f"{'Translation #':<15} {'Expected Cumulative':<20} {'Actual Cumulative':<18} {'Total Drift':<12}")
    print("-" * 80)
    
    for i, (expected_sec, actual_sec) in enumerate(zip(expected_times, actual_times)):
        total_drift = actual_sec - expected_sec
        print(f"{i+1:<15} {expected_sec:<20.1f} {actual_sec:<18.1f} {total_drift:<12.1f}")
    
    print()
    print("Analysis:")
    print("- Expected timing is based on SSML breaks and speech duration")
    print("- Actual timing shows when translations were completed")
    print("- Drift = Actual - Expected (positive = delay, negative = early)")
    print("- Gap Drift shows if delays are accumulating between segments")
    print("- Look for increasing gap drift to identify accumulation issues")

if __name__ == "__main__":
    analyze_timing()