#!/usr/bin/env python3
"""
Incomplete Translation Analysis Script

Based on the finding that inter-cycle gaps accumulate despite proper buffer clearing
and no persistent state between cycles, this script investigates incomplete or failed
translation cycles that may be causing OpenAI API state accumulation.

The hypothesis is that some translation cycles don't complete properly, leaving the
OpenAI API in an inconsistent state that causes increasing delays over time.

Usage:
    python analyze_incomplete_translations.py < logfile.txt
"""

import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class TranslationCycle:
    cycle_number: int
    side: str
    response_id: Optional[str] = None
    
    # Lifecycle events
    speech_started: Optional[datetime] = None
    speech_stopped: Optional[datetime] = None
    response_created: Optional[datetime] = None
    first_audio: Optional[datetime] = None
    response_done: Optional[datetime] = None
    audio_done: Optional[datetime] = None
    
    # Completion status
    is_complete: bool = False
    missing_events: List[str] = None
    
    def __post_init__(self):
        if self.missing_events is None:
            self.missing_events = []

class IncompleteTranslationAnalyzer:
    def __init__(self):
        self.cycles: List[TranslationCycle] = []
        self.current_cycles: Dict[str, TranslationCycle] = {}  # side -> current cycle
        self.cycle_counter = 0
        
    def parse_log_line(self, line: str):
        """Parse log lines to track translation cycle completion."""
        # Match timestamp pattern
        timestamp_match = re.match(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\]', line)
        if not timestamp_match:
            return
            
        timestamp_str = timestamp_match.group(1)
        try:
            timestamp = datetime.strptime(f"2025-06-05 {timestamp_str}", "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return
            
        # Look for OpenAI messages
        if "message from OpenAI:" in line:
            side = "caller" if "Caller message from OpenAI:" in line else "agent"
            
            # Extract JSON message
            json_match = re.search(r'message from OpenAI: ({.*})', line)
            if not json_match:
                return
                
            try:
                message_data = json.loads(json_match.group(1))
                event_type = message_data.get("type", "")
                response_id = message_data.get("response_id", "")
                
                if event_type == "input_audio_buffer.speech_started":
                    # Start new translation cycle
                    self.cycle_counter += 1
                    cycle = TranslationCycle(
                        cycle_number=self.cycle_counter,
                        side=side,
                        speech_started=timestamp
                    )
                    
                    # Finish previous cycle if exists
                    if side in self.current_cycles:
                        prev_cycle = self.current_cycles[side]
                        self._finalize_cycle(prev_cycle)
                        self.cycles.append(prev_cycle)
                    
                    self.current_cycles[side] = cycle
                    
                elif event_type == "input_audio_buffer.speech_stopped":
                    if side in self.current_cycles:
                        self.current_cycles[side].speech_stopped = timestamp
                        
                elif event_type == "response.created":
                    if side in self.current_cycles:
                        cycle = self.current_cycles[side]
                        cycle.response_created = timestamp
                        cycle.response_id = message_data.get("response", {}).get("id", "")
                        
                elif event_type == "response.audio.delta":
                    if side in self.current_cycles:
                        cycle = self.current_cycles[side]
                        if not cycle.first_audio:
                            cycle.first_audio = timestamp
                            
                elif event_type == "response.done":
                    if side in self.current_cycles:
                        cycle = self.current_cycles[side]
                        if not cycle.response_id or response_id == cycle.response_id:
                            cycle.response_done = timestamp
                            
                elif event_type == "response.audio.done":
                    if side in self.current_cycles:
                        cycle = self.current_cycles[side]
                        if not cycle.response_id or response_id == cycle.response_id:
                            cycle.audio_done = timestamp
                            
            except json.JSONDecodeError:
                pass
                
    def _finalize_cycle(self, cycle: TranslationCycle):
        """Finalize a cycle and determine if it's complete."""
        # Check for required events
        required_events = [
            ("speech_started", cycle.speech_started),
            ("speech_stopped", cycle.speech_stopped),
            ("response_created", cycle.response_created),
            ("response_done", cycle.response_done)
        ]
        
        for event_name, event_time in required_events:
            if event_time is None:
                cycle.missing_events.append(event_name)
                
        # Cycle is complete if all required events are present
        cycle.is_complete = len(cycle.missing_events) == 0
        
    def finalize_analysis(self):
        """Finalize analysis by processing any remaining cycles."""
        for side, cycle in self.current_cycles.items():
            self._finalize_cycle(cycle)
            self.cycles.append(cycle)
            
    def analyze_completion_patterns(self) -> Dict:
        """Analyze translation completion patterns."""
        complete_cycles = [c for c in self.cycles if c.is_complete]
        incomplete_cycles = [c for c in self.cycles if not c.is_complete]
        
        analysis = {
            "total_cycles": len(self.cycles),
            "complete_cycles": len(complete_cycles),
            "incomplete_cycles": len(incomplete_cycles),
            "completion_rate": len(complete_cycles) / len(self.cycles) * 100 if self.cycles else 0,
            "incomplete_details": [],
            "completion_degradation": False
        }
        
        # Analyze incomplete cycles
        for cycle in incomplete_cycles:
            analysis["incomplete_details"].append({
                "cycle": cycle.cycle_number,
                "side": cycle.side,
                "missing_events": cycle.missing_events,
                "response_id": cycle.response_id
            })
            
        # Check for completion degradation over time
        if len(self.cycles) >= 6:
            first_half = self.cycles[:len(self.cycles)//2]
            last_half = self.cycles[len(self.cycles)//2:]
            
            first_half_complete = sum(1 for c in first_half if c.is_complete)
            last_half_complete = sum(1 for c in last_half if c.is_complete)
            
            first_half_rate = first_half_complete / len(first_half) * 100
            last_half_rate = last_half_complete / len(last_half) * 100
            
            if last_half_rate < first_half_rate - 10:  # 10% degradation
                analysis["completion_degradation"] = True
                analysis["first_half_rate"] = first_half_rate
                analysis["last_half_rate"] = last_half_rate
                
        return analysis
        
    def analyze_api_state_correlation(self) -> Dict:
        """Analyze correlation between incomplete cycles and API state."""
        analysis = {
            "incomplete_cycle_gaps": [],
            "api_state_indicators": [],
            "response_id_issues": []
        }
        
        # Look for patterns in incomplete cycles
        incomplete_cycles = [c for c in self.cycles if not c.is_complete]
        
        for cycle in incomplete_cycles:
            # Find the next complete cycle to measure gap impact
            next_cycles = [c for c in self.cycles if c.cycle_number > cycle.cycle_number]
            if next_cycles:
                next_cycle = next_cycles[0]
                if cycle.speech_stopped and next_cycle.speech_started:
                    gap = int((next_cycle.speech_started - cycle.speech_stopped).total_seconds() * 1000)
                    analysis["incomplete_cycle_gaps"].append({
                        "incomplete_cycle": cycle.cycle_number,
                        "next_cycle": next_cycle.cycle_number,
                        "gap_ms": gap,
                        "missing_events": cycle.missing_events
                    })
                    
            # Check for response ID issues
            if cycle.response_created and not cycle.response_id:
                analysis["response_id_issues"].append({
                    "cycle": cycle.cycle_number,
                    "issue": "response.created without response ID"
                })
                
        return analysis
        
    def print_analysis(self):
        """Print comprehensive incomplete translation analysis."""
        print("Incomplete Translation Analysis")
        print("=" * 50)
        print(f"Total translation cycles: {len(self.cycles)}")
        print()
        
        # Completion patterns
        completion_analysis = self.analyze_completion_patterns()
        print("Translation Completion Analysis:")
        print("-" * 40)
        print(f"Complete cycles: {completion_analysis['complete_cycles']}")
        print(f"Incomplete cycles: {completion_analysis['incomplete_cycles']}")
        print(f"Completion rate: {completion_analysis['completion_rate']:.1f}%")
        
        if completion_analysis['completion_degradation']:
            print(f"🚨 COMPLETION DEGRADATION DETECTED!")
            print(f"   First half: {completion_analysis['first_half_rate']:.1f}% complete")
            print(f"   Last half: {completion_analysis['last_half_rate']:.1f}% complete")
        print()
        
        # Incomplete cycle details
        if completion_analysis['incomplete_details']:
            print("Incomplete Cycle Details:")
            print("-" * 30)
            for detail in completion_analysis['incomplete_details']:
                missing = ", ".join(detail['missing_events'])
                print(f"  Cycle {detail['cycle']} ({detail['side']}): Missing {missing}")
                if detail['response_id']:
                    print(f"    Response ID: {detail['response_id']}")
            print()
            
        # API state correlation
        api_analysis = self.analyze_api_state_correlation()
        
        if api_analysis['incomplete_cycle_gaps']:
            print("Incomplete Cycle Impact on Gaps:")
            print("-" * 35)
            for gap_info in api_analysis['incomplete_cycle_gaps']:
                missing = ", ".join(gap_info['missing_events'])
                print(f"  Incomplete cycle {gap_info['incomplete_cycle']} -> next cycle {gap_info['next_cycle']}: {gap_info['gap_ms']}ms gap")
                print(f"    Missing: {missing}")
            print()
            
        if api_analysis['response_id_issues']:
            print("🚨 RESPONSE ID ISSUES:")
            for issue in api_analysis['response_id_issues']:
                print(f"  Cycle {issue['cycle']}: {issue['issue']}")
            print()
            
        # Cycle completion timeline
        print("Cycle Completion Timeline:")
        print("-" * 30)
        for cycle in self.cycles:
            status = "✅ Complete" if cycle.is_complete else f"❌ Incomplete ({', '.join(cycle.missing_events)})"
            response_info = f" [ID: {cycle.response_id[:8]}...]" if cycle.response_id else ""
            print(f"  Cycle {cycle.cycle_number} ({cycle.side}): {status}{response_info}")
        print()
        
        # Recommendations
        print("Recommendations:")
        print("-" * 20)
        
        if completion_analysis['incomplete_cycles'] > 0:
            print("1. 🚨 Incomplete translation cycles detected")
            print("   - Some translations are not completing properly")
            print("   - This may be causing OpenAI API state accumulation")
            print("   - Consider implementing response timeout handling")
            
        if completion_analysis['completion_degradation']:
            print("2. 🚨 Translation completion rate is degrading over time")
            print("   - API reliability decreases during conversation")
            print("   - Consider periodic connection reset or session management")
            
        if api_analysis['incomplete_cycle_gaps']:
            print("3. 🚨 Incomplete cycles correlate with larger gaps")
            print("   - Failed translations may be causing timing drift")
            print("   - Implement proper error handling and recovery")
            
        if api_analysis['response_id_issues']:
            print("4. ⚠️  Response ID tracking issues detected")
            print("   - May indicate API communication problems")
            print("   - Verify response ID handling in event processing")

def main():
    """Main function to process log input."""
    analyzer = IncompleteTranslationAnalyzer()
    
    print("Reading log data for incomplete translation analysis...")
    print("(Paste log content and press Ctrl+D when done, or pipe from file)")
    print()
    
    line_count = 0
    for line in sys.stdin:
        line = line.strip()
        if line:
            analyzer.parse_log_line(line)
            line_count += 1
            
    analyzer.finalize_analysis()
    
    print(f"Processed {line_count} log lines")
    print()
    
    analyzer.print_analysis()

if __name__ == "__main__":
    main()