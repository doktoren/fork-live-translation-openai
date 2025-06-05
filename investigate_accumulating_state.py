#!/usr/bin/env python3
"""
Accumulating State Investigation Script

Based on the speech detection timing analysis showing 43.3% increase in inter-cycle gaps,
this script investigates what specific state is accumulating between cycles.

The goal is to identify exactly what timing state or buffers are not being properly
reset between translation cycles, causing delays to persist through pauses.

Usage:
    python investigate_accumulating_state.py < logfile.txt
"""

import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class StateEvent:
    timestamp: datetime
    side: str
    event_type: str
    details: Dict
    cycle_number: Optional[int] = None

class AccumulatingStateInvestigator:
    def __init__(self):
        self.events: List[StateEvent] = []
        self.current_cycle = 0
        self.cycle_boundaries = {}  # cycle_number -> (start_time, end_time)
        
    def parse_log_line(self, line: str):
        """Parse log lines to extract state management events."""
        # Match timestamp pattern
        timestamp_match = re.match(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\]', line)
        if not timestamp_match:
            return
            
        timestamp_str = timestamp_match.group(1)
        try:
            timestamp = datetime.strptime(f"2025-06-05 {timestamp_str}", "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return
            
        # Look for state management events
        if "message from OpenAI:" in line:
            side = "caller" if "Caller message from OpenAI:" in line else "agent"
            
            # Extract JSON message
            json_match = re.search(r'message from OpenAI: ({.*})', line)
            if not json_match:
                return
                
            try:
                message_data = json.loads(json_match.group(1))
                event_type = message_data.get("type", "")
                
                # Track cycle boundaries
                if event_type == "input_audio_buffer.speech_started":
                    self.current_cycle += 1
                    if self.current_cycle not in self.cycle_boundaries:
                        self.cycle_boundaries[self.current_cycle] = [timestamp, None]
                        
                elif event_type == "response.done":
                    if self.current_cycle in self.cycle_boundaries:
                        self.cycle_boundaries[self.current_cycle][1] = timestamp
                
                # Track state management events
                if event_type in [
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped", 
                    "input_audio_buffer.cleared",
                    "input_audio_buffer.committed",
                    "response.created",
                    "response.done",
                    "response.audio.done",
                    "session.created",
                    "session.updated"
                ]:
                    self.events.append(StateEvent(
                        timestamp=timestamp,
                        side=side,
                        event_type=event_type,
                        details=message_data,
                        cycle_number=self.current_cycle
                    ))
                    
            except json.JSONDecodeError:
                pass
                
        # Look for buffer clearing logs
        elif "Cleared" in line and "input audio buffer" in line:
            side = "caller" if "caller" in line.lower() else "agent"
            self.events.append(StateEvent(
                timestamp=timestamp,
                side=side,
                event_type="buffer_cleared_log",
                details={"message": line.strip()},
                cycle_number=self.current_cycle
            ))
            
    def analyze_buffer_clearing_patterns(self) -> Dict:
        """Analyze if buffers are being cleared properly between cycles."""
        buffer_events = [e for e in self.events if "buffer" in e.event_type or "cleared" in e.event_type]
        
        analysis = {
            "total_buffer_events": len(buffer_events),
            "clearing_patterns": {},
            "missing_clears": [],
            "timing_issues": []
        }
        
        # Group by cycle
        cycles = {}
        for event in buffer_events:
            if event.cycle_number not in cycles:
                cycles[event.cycle_number] = []
            cycles[event.cycle_number].append(event)
            
        # Analyze each cycle's buffer management
        for cycle_num, cycle_events in cycles.items():
            cycle_events.sort(key=lambda e: e.timestamp)
            
            pattern = []
            for event in cycle_events:
                pattern.append(f"{event.side}:{event.event_type}")
                
            analysis["clearing_patterns"][cycle_num] = {
                "pattern": " -> ".join(pattern),
                "events": len(cycle_events),
                "duration_ms": int((cycle_events[-1].timestamp - cycle_events[0].timestamp).total_seconds() * 1000) if len(cycle_events) > 1 else 0
            }
            
            # Check for missing clears
            has_speech_started = any("speech_started" in e.event_type for e in cycle_events)
            has_buffer_cleared = any("cleared" in e.event_type for e in cycle_events)
            
            if has_speech_started and not has_buffer_cleared:
                analysis["missing_clears"].append(cycle_num)
                
        return analysis
        
    def analyze_inter_cycle_state_persistence(self) -> Dict:
        """Analyze what state persists between cycles."""
        analysis = {
            "cycle_gaps": [],
            "persistent_state_indicators": [],
            "state_reset_issues": []
        }
        
        # Calculate gaps between cycles
        sorted_cycles = sorted(self.cycle_boundaries.keys())
        
        for i in range(1, len(sorted_cycles)):
            prev_cycle = sorted_cycles[i-1]
            curr_cycle = sorted_cycles[i]
            
            prev_end = self.cycle_boundaries[prev_cycle][1]
            curr_start = self.cycle_boundaries[curr_cycle][0]
            
            if prev_end and curr_start:
                gap_ms = int((curr_start - prev_end).total_seconds() * 1000)
                analysis["cycle_gaps"].append({
                    "from_cycle": prev_cycle,
                    "to_cycle": curr_cycle,
                    "gap_ms": gap_ms,
                    "prev_end": prev_end.strftime("%H:%M:%S.%f")[:-3],
                    "curr_start": curr_start.strftime("%H:%M:%S.%f")[:-3]
                })
                
        # Look for events that happen between cycles (indicating persistent state)
        for gap in analysis["cycle_gaps"]:
            prev_end = datetime.strptime(f"2025-06-05 {gap['prev_end']}", "%Y-%m-%d %H:%M:%S.%f")
            curr_start = datetime.strptime(f"2025-06-05 {gap['curr_start']}", "%Y-%m-%d %H:%M:%S.%f")
            
            between_events = [
                e for e in self.events 
                if prev_end < e.timestamp < curr_start
            ]
            
            if between_events:
                analysis["persistent_state_indicators"].append({
                    "gap": gap,
                    "events_between": len(between_events),
                    "event_types": [e.event_type for e in between_events]
                })
                
        return analysis
        
    def analyze_websocket_state_accumulation(self) -> Dict:
        """Analyze WebSocket connection state over time."""
        session_events = [e for e in self.events if "session" in e.event_type]
        
        analysis = {
            "total_session_events": len(session_events),
            "session_timeline": [],
            "potential_accumulation": []
        }
        
        for event in session_events:
            analysis["session_timeline"].append({
                "timestamp": event.timestamp.strftime("%H:%M:%S.%f")[:-3],
                "side": event.side,
                "event_type": event.event_type,
                "cycle": event.cycle_number
            })
            
        # Look for signs of session state accumulation
        if len(session_events) > 2:  # More than initial setup
            analysis["potential_accumulation"].append("Multiple session events detected - may indicate state accumulation")
            
        return analysis
        
    def print_investigation_results(self):
        """Print comprehensive investigation results."""
        print("Accumulating State Investigation Results")
        print("=" * 60)
        print(f"Total events analyzed: {len(self.events)}")
        print(f"Translation cycles: {len(self.cycle_boundaries)}")
        print()
        
        # Buffer clearing analysis
        buffer_analysis = self.analyze_buffer_clearing_patterns()
        print("Buffer Clearing Analysis:")
        print("-" * 40)
        print(f"Total buffer events: {buffer_analysis['total_buffer_events']}")
        
        if buffer_analysis['missing_clears']:
            print(f"🚨 MISSING BUFFER CLEARS in cycles: {buffer_analysis['missing_clears']}")
        else:
            print("✅ All cycles have buffer clearing events")
            
        print("\nBuffer Clearing Patterns by Cycle:")
        for cycle, pattern in buffer_analysis['clearing_patterns'].items():
            status = "⚠️" if cycle in buffer_analysis['missing_clears'] else "✅"
            print(f"  Cycle {cycle}: {status} {pattern['pattern']} ({pattern['duration_ms']}ms)")
        print()
        
        # Inter-cycle state persistence analysis
        persistence_analysis = self.analyze_inter_cycle_state_persistence()
        print("Inter-Cycle State Persistence Analysis:")
        print("-" * 50)
        
        gaps = persistence_analysis['cycle_gaps']
        if gaps:
            print("Cycle Gaps (showing accumulation pattern):")
            for gap in gaps:
                trend = ""
                if gaps.index(gap) > 0:
                    prev_gap = gaps[gaps.index(gap) - 1]['gap_ms']
                    if gap['gap_ms'] > prev_gap:
                        trend = f" (+{gap['gap_ms'] - prev_gap}ms)"
                    elif gap['gap_ms'] < prev_gap:
                        trend = f" ({gap['gap_ms'] - prev_gap}ms)"
                        
                print(f"  Cycle {gap['from_cycle']} -> {gap['to_cycle']}: {gap['gap_ms']}ms{trend}")
                
        persistent_indicators = persistence_analysis['persistent_state_indicators']
        if persistent_indicators:
            print(f"\n🚨 PERSISTENT STATE DETECTED:")
            for indicator in persistent_indicators:
                gap = indicator['gap']
                print(f"  Between cycles {gap['from_cycle']}->{gap['to_cycle']}: {indicator['events_between']} events")
                print(f"    Event types: {', '.join(set(indicator['event_types']))}")
        else:
            print("\n✅ No events detected between cycles")
        print()
        
        # WebSocket state analysis
        websocket_analysis = self.analyze_websocket_state_accumulation()
        print("WebSocket State Analysis:")
        print("-" * 30)
        print(f"Session events: {websocket_analysis['total_session_events']}")
        
        if websocket_analysis['session_timeline']:
            print("Session event timeline:")
            for event in websocket_analysis['session_timeline']:
                print(f"  {event['timestamp']} - {event['side']} {event['event_type']} (cycle {event['cycle']})")
                
        if websocket_analysis['potential_accumulation']:
            print("🚨 POTENTIAL WEBSOCKET ACCUMULATION:")
            for issue in websocket_analysis['potential_accumulation']:
                print(f"  - {issue}")
        print()
        
        # Recommendations
        print("Targeted Recommendations:")
        print("-" * 30)
        
        if buffer_analysis['missing_clears']:
            print("1. 🚨 Fix missing buffer clears in cycles:", buffer_analysis['missing_clears'])
            print("   - Ensure input_audio_buffer.clear is called for every speech cycle")
            
        if persistent_indicators:
            print("2. 🚨 Investigate persistent state between cycles")
            print("   - Events occurring between cycles indicate state not being reset")
            print("   - Focus on:", set([t for i in persistent_indicators for t in i['event_types']]))
            
        if len(gaps) > 5:
            # Calculate trend
            first_half = gaps[:len(gaps)//2]
            last_half = gaps[len(gaps)//2:]
            avg_first = sum(g['gap_ms'] for g in first_half) / len(first_half)
            avg_last = sum(g['gap_ms'] for g in last_half) / len(last_half)
            
            if avg_last > avg_first * 1.2:  # 20% increase
                print("3. 🚨 Confirmed gap accumulation pattern")
                print(f"   - Average gap increased from {avg_first:.0f}ms to {avg_last:.0f}ms")
                print("   - Implement periodic state reset mechanism")
                
        if websocket_analysis['total_session_events'] > 2:
            print("4. ⚠️  Consider WebSocket connection management")
            print("   - Multiple session events may indicate connection state issues")
            print("   - Consider periodic connection reset")

def main():
    """Main function to process log input."""
    investigator = AccumulatingStateInvestigator()
    
    print("Reading log data for accumulating state investigation...")
    print("(Paste log content and press Ctrl+D when done, or pipe from file)")
    print()
    
    line_count = 0
    for line in sys.stdin:
        line = line.strip()
        if line:
            investigator.parse_log_line(line)
            line_count += 1
            
    print(f"Processed {line_count} log lines")
    print()
    
    investigator.print_investigation_results()

if __name__ == "__main__":
    main()