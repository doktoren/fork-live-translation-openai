#!/usr/bin/env python3
"""
Raw Log Analysis Script for Translation Timing Issues

This script analyzes raw log output to extract timing information relevant
for debugging translation delay accumulation issues.

Usage:
    python analyze_raw_logs.py < logfile.txt
    or
    cat logfile.txt | python analyze_raw_logs.py
"""

import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class TimingEvent:
    timestamp: datetime
    timestamp_ms: int
    event_type: str
    event_id: str
    response_id: Optional[str] = None
    side: str = "unknown"  # caller or agent
    raw_message: str = ""

class LogAnalyzer:
    def __init__(self):
        self.events: List[TimingEvent] = []
        self.translation_cycles: List[Dict] = []
        
    def parse_log_line(self, line: str) -> Optional[TimingEvent]:
        """Parse a single log line and extract timing information."""
        # Match timestamp pattern: [HH:MM:SS.mmm]
        timestamp_match = re.match(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\]', line)
        if not timestamp_match:
            return None
            
        timestamp_str = timestamp_match.group(1)
        
        # Parse timestamp (assuming today's date)
        try:
            timestamp = datetime.strptime(f"2025-06-05 {timestamp_str}", "%Y-%m-%d %H:%M:%S.%f")
            timestamp_ms = int(timestamp.timestamp() * 1000)
        except ValueError:
            return None
            
        # Look for OpenAI messages
        if "message from OpenAI:" in line:
            # Determine side (caller or agent)
            side = "caller" if "Caller message from OpenAI:" in line else "agent"
            
            # Extract JSON message
            json_match = re.search(r'message from OpenAI: ({.*})', line)
            if not json_match:
                return None
                
            try:
                message_data = json.loads(json_match.group(1))
                event_type = message_data.get("type", "unknown")
                event_id = message_data.get("event_id", "")
                response_id = message_data.get("response_id")
                
                return TimingEvent(
                    timestamp=timestamp,
                    timestamp_ms=timestamp_ms,
                    event_type=event_type,
                    event_id=event_id,
                    response_id=response_id,
                    side=side,
                    raw_message=json_match.group(1)
                )
            except json.JSONDecodeError:
                return None
                
        # Look for other relevant timing messages
        elif "translation started at" in line:
            side = "caller" if "Caller translation started" in line else "agent"
            # Extract timestamp from message
            ts_match = re.search(r'started at (\d+)', line)
            if ts_match:
                reported_ts = int(ts_match.group(1))
                return TimingEvent(
                    timestamp=timestamp,
                    timestamp_ms=timestamp_ms,
                    event_type="translation_started_log",
                    event_id="",
                    side=side,
                    raw_message=f"reported_timestamp={reported_ts}"
                )
                
        elif "Received" in line and "translation from OpenAI" in line:
            side = "caller" if "caller translation" in line else "agent"
            return TimingEvent(
                timestamp=timestamp,
                timestamp_ms=timestamp_ms,
                event_type="translation_received_log",
                event_id="",
                side=side,
                raw_message=line.strip()
            )
            
        return None
        
    def analyze_translation_cycles(self):
        """Analyze events to identify translation cycles and timing patterns."""
        current_cycle = {}
        
        for event in self.events:
            if event.event_type == "response.created":
                # Start of new translation cycle
                if current_cycle:
                    # Finish previous cycle
                    self.translation_cycles.append(current_cycle)
                    
                current_cycle = {
                    "side": event.side,
                    "response_id": event.response_id,
                    "start_time": event.timestamp_ms,
                    "start_timestamp": event.timestamp,
                    "events": [event]
                }
                
            elif event.event_type in ["response.done", "response.audio.done"]:
                if current_cycle and event.response_id == current_cycle.get("response_id"):
                    current_cycle["end_time"] = event.timestamp_ms
                    current_cycle["end_timestamp"] = event.timestamp
                    current_cycle["duration"] = event.timestamp_ms - current_cycle["start_time"]
                    current_cycle["events"].append(event)
                    
            elif event.event_type == "response.audio.delta":
                if current_cycle and event.response_id == current_cycle.get("response_id"):
                    if "first_audio_time" not in current_cycle:
                        current_cycle["first_audio_time"] = event.timestamp_ms
                        current_cycle["first_audio_timestamp"] = event.timestamp
                        current_cycle["time_to_first_audio"] = event.timestamp_ms - current_cycle["start_time"]
                    current_cycle["events"].append(event)
                    
            elif current_cycle:
                current_cycle["events"].append(event)
                
        # Add final cycle if exists
        if current_cycle:
            self.translation_cycles.append(current_cycle)
            
    def calculate_gaps(self) -> List[Tuple[int, int, int]]:
        """Calculate gaps between translation cycles."""
        gaps = []
        
        for i in range(1, len(self.translation_cycles)):
            prev_cycle = self.translation_cycles[i-1]
            curr_cycle = self.translation_cycles[i]
            
            if "end_time" in prev_cycle:
                gap = curr_cycle["start_time"] - prev_cycle["end_time"]
                gaps.append((i, gap, curr_cycle["start_time"]))
                
        return gaps
        
    def print_analysis(self):
        """Print comprehensive analysis of timing data."""
        print("Raw Log Timing Analysis")
        print("=" * 80)
        print(f"Total events parsed: {len(self.events)}")
        print(f"Translation cycles identified: {len(self.translation_cycles)}")
        
        # Check for incomplete cycles
        incomplete_cycles = [c for c in self.translation_cycles if "end_time" not in c]
        if incomplete_cycles:
            print(f"⚠️  Incomplete cycles: {len(incomplete_cycles)} (missing response.done/response.audio.done)")
        print()
        
        # Event type summary
        event_types = {}
        for event in self.events:
            key = f"{event.side}:{event.event_type}"
            event_types[key] = event_types.get(key, 0) + 1
            
        print("Event Type Summary:")
        print("-" * 40)
        for event_type, count in sorted(event_types.items()):
            print(f"{event_type:35} {count:3}")
        print()
        
        # Translation cycle analysis
        if self.translation_cycles:
            print("Translation Cycle Analysis:")
            print("-" * 80)
            print(f"{'#':<3} {'Side':<6} {'Start Time':<12} {'Duration':<8} {'To Audio':<8} {'Response ID':<20}")
            print("-" * 80)
            
            for i, cycle in enumerate(self.translation_cycles, 1):
                start_time = cycle["start_timestamp"].strftime("%H:%M:%S.%f")[:-3]
                duration = cycle.get("duration", "N/A")
                if duration != "N/A":
                    duration = f"{duration}ms"
                    
                to_audio = cycle.get("time_to_first_audio", "N/A")
                if to_audio != "N/A":
                    to_audio = f"{to_audio}ms"
                    
                response_id = (cycle.get("response_id") or "")[:20]
                
                print(f"{i:<3} {cycle['side']:<6} {start_time:<12} {duration:<8} {to_audio:<8} {response_id:<20}")
            print()
            
            # Detailed timing within cycles
            print("Detailed Timing Within Translation Cycles:")
            print("-" * 80)
            for i, cycle in enumerate(self.translation_cycles, 1):
                print(f"\nCycle {i} ({cycle['side']}) - Response ID: {cycle.get('response_id', 'N/A')}")
                print(f"{'Event':<30} {'Time':<12} {'Offset (ms)':<10} {'Delta (ms)':<10}")
                print("-" * 65)
                
                start_time = cycle["start_time"]
                prev_time = start_time
                
                for event in cycle["events"]:
                    offset = event.timestamp_ms - start_time
                    delta = event.timestamp_ms - prev_time
                    time_str = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
                    
                    print(f"{event.event_type:<30} {time_str:<12} {offset:<10} {delta:<10}")
                    prev_time = event.timestamp_ms
            print()
            
            # Gap analysis
            gaps = self.calculate_gaps()
            if gaps:
                print("Gap Analysis Between Translation Cycles:")
                print("-" * 60)
                print(f"{'Gap #':<6} {'Gap (ms)':<10} {'Start Time':<12} {'Trend':<10}")
                print("-" * 60)
                
                prev_gap = None
                for gap_num, gap_ms, start_time in gaps:
                    start_dt = datetime.fromtimestamp(start_time / 1000)
                    start_str = start_dt.strftime("%H:%M:%S.%f")[:-3]
                    
                    trend = ""
                    if prev_gap is not None:
                        if gap_ms > prev_gap:
                            trend = f"+{gap_ms - prev_gap}ms"
                        elif gap_ms < prev_gap:
                            trend = f"{gap_ms - prev_gap}ms"
                        else:
                            trend = "same"
                    
                    print(f"{gap_num:<6} {gap_ms:<10} {start_str:<12} {trend:<10}")
                    prev_gap = gap_ms
                print()
                
                # Gap statistics
                gap_values = [gap for _, gap, _ in gaps]
                if gap_values:
                    avg_gap = sum(gap_values) / len(gap_values)
                    min_gap = min(gap_values)
                    max_gap = max(gap_values)
                    gap_range = max_gap - min_gap
                    
                    print("Gap Statistics:")
                    print("-" * 30)
                    print(f"Average gap: {avg_gap:.1f}ms")
                    print(f"Min gap:     {min_gap}ms")
                    print(f"Max gap:     {max_gap}ms")
                    print(f"Range:       {gap_range}ms")
                    
                    if gap_range > 1000:  # More than 1 second variation
                        print(f"⚠️  WARNING: Large gap variation detected ({gap_range}ms)")
                        print("   This indicates inconsistent timing that could cause delay accumulation")
                    print()
        
        # Timing discrepancy analysis
        print("Timing Discrepancy Analysis:")
        print("-" * 50)
        translation_started_events = [e for e in self.events if e.event_type == "translation_started_log"]
        response_created_events = [e for e in self.events if e.event_type == "response.created"]
        
        for i, (started_event, created_event) in enumerate(zip(translation_started_events, response_created_events)):
            # Extract reported timestamp from the log message
            if "reported_timestamp=" in started_event.raw_message:
                reported_ts_str = started_event.raw_message.split("reported_timestamp=")[1]
                reported_ts = int(reported_ts_str)
                actual_ts = started_event.timestamp_ms
                discrepancy = actual_ts - reported_ts
                
                print(f"Translation {i+1} ({started_event.side}):")
                print(f"  Reported timestamp: {reported_ts}")
                print(f"  Actual timestamp:   {actual_ts}")
                print(f"  Discrepancy:        {discrepancy}ms")
                
                if abs(discrepancy) > 100:  # More than 100ms difference
                    print(f"  ⚠️  WARNING: Large timing discrepancy detected!")
                print()
        
        # Detailed event timeline for debugging
        print("Detailed Event Timeline (last 20 events):")
        print("-" * 100)
        print(f"{'Time':<12} {'Side':<6} {'Event Type':<25} {'Response ID':<20} {'Details':<30}")
        print("-" * 100)
        
        for event in self.events[-20:]:
            time_str = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
            response_id = (event.response_id or "")[:20]
            details = event.raw_message[:30] if event.raw_message else ""
            
            print(f"{time_str:<12} {event.side:<6} {event.event_type:<25} {response_id:<20} {details:<30}")

def main():
    """Main function to process log input."""
    analyzer = LogAnalyzer()
    
    print("Reading log data from stdin...")
    print("(Paste log content and press Ctrl+D when done, or pipe from file)")
    print()
    
    line_count = 0
    for line in sys.stdin:
        line = line.strip()
        if line:
            event = analyzer.parse_log_line(line)
            if event:
                analyzer.events.append(event)
            line_count += 1
            
    print(f"Processed {line_count} log lines")
    print()
    
    if analyzer.events:
        analyzer.analyze_translation_cycles()
        analyzer.print_analysis()
    else:
        print("No timing events found in the log data.")
        print("Make sure the log contains OpenAI message events with timestamps.")

if __name__ == "__main__":
    main()