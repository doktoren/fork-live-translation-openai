#!/usr/bin/env python3
"""
Translation Interruption Analysis Script

Based on the finding that 66.7% of translations are incomplete (missing response.done),
this script investigates whether these incomplete translations are caused by interruptions
- i.e., new speech starting before the previous translation completes.

The hypothesis is that when users interrupt ongoing translations by speaking again,
the OpenAI API cancels the first translation (no response.done) and starts a new one,
causing API state accumulation and increasing delays.

Usage:
    python analyze_translation_interruptions.py < logfile.txt
"""

import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class TranslationEvent:
    timestamp: datetime
    side: str
    event_type: str
    response_id: Optional[str] = None
    cycle_number: Optional[int] = None

class InterruptionAnalyzer:
    def __init__(self):
        self.events: List[TranslationEvent] = []
        self.active_translations: Dict[str, TranslationEvent] = {}  # response_id -> response.created event
        self.cycle_counter = 0
        
    def parse_log_line(self, line: str):
        """Parse log lines to track translation events and interruptions."""
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
                    self.cycle_counter += 1
                    event = TranslationEvent(
                        timestamp=timestamp,
                        side=side,
                        event_type=event_type,
                        cycle_number=self.cycle_counter
                    )
                    self.events.append(event)
                    
                elif event_type == "response.created":
                    response_id = message_data.get("response", {}).get("id", "")
                    event = TranslationEvent(
                        timestamp=timestamp,
                        side=side,
                        event_type=event_type,
                        response_id=response_id,
                        cycle_number=self.cycle_counter
                    )
                    self.events.append(event)
                    
                    # Track active translation
                    if response_id:
                        self.active_translations[response_id] = event
                        
                elif event_type == "response.done":
                    event = TranslationEvent(
                        timestamp=timestamp,
                        side=side,
                        event_type=event_type,
                        response_id=response_id,
                        cycle_number=self.cycle_counter
                    )
                    self.events.append(event)
                    
                    # Remove from active translations
                    if response_id in self.active_translations:
                        del self.active_translations[response_id]
                        
            except json.JSONDecodeError:
                pass
                
    def analyze_interruption_patterns(self) -> Dict:
        """Analyze interruption patterns in translation cycles."""
        analysis = {
            "total_speech_events": 0,
            "total_response_created": 0,
            "total_response_done": 0,
            "interruptions": [],
            "interruption_rate": 0,
            "timing_analysis": []
        }
        
        speech_events = [e for e in self.events if e.event_type == "input_audio_buffer.speech_started"]
        response_created_events = [e for e in self.events if e.event_type == "response.created"]
        response_done_events = [e for e in self.events if e.event_type == "response.done"]
        
        analysis["total_speech_events"] = len(speech_events)
        analysis["total_response_created"] = len(response_created_events)
        analysis["total_response_done"] = len(response_done_events)
        
        # Find interruptions: speech_started while previous translation is still active
        for i, speech_event in enumerate(speech_events):
            # Find active translations at the time of this speech
            active_at_speech = []
            
            for response_event in response_created_events:
                if response_event.timestamp < speech_event.timestamp:
                    # Check if this response was completed before the speech
                    response_completed = False
                    for done_event in response_done_events:
                        if (done_event.response_id == response_event.response_id and 
                            done_event.timestamp < speech_event.timestamp):
                            response_completed = True
                            break
                            
                    if not response_completed:
                        active_at_speech.append(response_event)
                        
            # If there are active translations when speech starts, it's an interruption
            if active_at_speech:
                interruption = {
                    "speech_cycle": speech_event.cycle_number,
                    "speech_timestamp": speech_event.timestamp.strftime("%H:%M:%S.%f")[:-3],
                    "speech_side": speech_event.side,
                    "interrupted_translations": []
                }
                
                for active_response in active_at_speech:
                    # Calculate how long the translation had been running
                    duration_ms = int((speech_event.timestamp - active_response.timestamp).total_seconds() * 1000)
                    
                    interruption["interrupted_translations"].append({
                        "response_id": active_response.response_id,
                        "side": active_response.side,
                        "duration_ms": duration_ms,
                        "started_at": active_response.timestamp.strftime("%H:%M:%S.%f")[:-3]
                    })
                    
                analysis["interruptions"].append(interruption)
                
        analysis["interruption_rate"] = len(analysis["interruptions"]) / len(speech_events) * 100 if speech_events else 0
        
        return analysis
        
    def analyze_incomplete_translation_correlation(self) -> Dict:
        """Analyze correlation between interruptions and incomplete translations."""
        analysis = {
            "interrupted_response_ids": set(),
            "completed_response_ids": set(),
            "incomplete_response_ids": set(),
            "interruption_completion_correlation": {}
        }
        
        # Get all response IDs that were interrupted
        interruption_analysis = self.analyze_interruption_patterns()
        for interruption in interruption_analysis["interruptions"]:
            for interrupted in interruption["interrupted_translations"]:
                analysis["interrupted_response_ids"].add(interrupted["response_id"])
                
        # Get all response IDs that completed
        response_done_events = [e for e in self.events if e.event_type == "response.done"]
        for event in response_done_events:
            if event.response_id:
                analysis["completed_response_ids"].add(event.response_id)
                
        # Get all response IDs that were created
        response_created_events = [e for e in self.events if e.event_type == "response.created"]
        all_response_ids = set()
        for event in response_created_events:
            if event.response_id:
                all_response_ids.add(event.response_id)
                
        # Find incomplete response IDs
        analysis["incomplete_response_ids"] = all_response_ids - analysis["completed_response_ids"]
        
        # Calculate correlation
        interrupted_and_incomplete = analysis["interrupted_response_ids"] & analysis["incomplete_response_ids"]
        interrupted_but_completed = analysis["interrupted_response_ids"] & analysis["completed_response_ids"]
        not_interrupted_but_incomplete = analysis["incomplete_response_ids"] - analysis["interrupted_response_ids"]
        
        analysis["interruption_completion_correlation"] = {
            "total_interrupted": len(analysis["interrupted_response_ids"]),
            "interrupted_and_incomplete": len(interrupted_and_incomplete),
            "interrupted_but_completed": len(interrupted_but_completed),
            "not_interrupted_but_incomplete": len(not_interrupted_but_incomplete),
            "correlation_percentage": len(interrupted_and_incomplete) / len(analysis["interrupted_response_ids"]) * 100 if analysis["interrupted_response_ids"] else 0
        }
        
        return analysis
        
    def print_analysis(self):
        """Print comprehensive interruption analysis."""
        print("Translation Interruption Analysis")
        print("=" * 50)
        
        # Basic statistics
        interruption_analysis = self.analyze_interruption_patterns()
        print("Basic Statistics:")
        print("-" * 20)
        print(f"Total speech events: {interruption_analysis['total_speech_events']}")
        print(f"Total response.created: {interruption_analysis['total_response_created']}")
        print(f"Total response.done: {interruption_analysis['total_response_done']}")
        completion_rate = (interruption_analysis['total_response_done'] / interruption_analysis['total_response_created'] * 100) if interruption_analysis['total_response_created'] > 0 else 0
        print(f"Completion rate: {completion_rate:.1f}%")
        print()
        
        # Interruption analysis
        print("Interruption Analysis:")
        print("-" * 25)
        print(f"Total interruptions detected: {len(interruption_analysis['interruptions'])}")
        print(f"Interruption rate: {interruption_analysis['interruption_rate']:.1f}%")
        print()
        
        if interruption_analysis['interruptions']:
            print("Interruption Details:")
            print("-" * 20)
            for i, interruption in enumerate(interruption_analysis['interruptions'], 1):
                print(f"Interruption {i}:")
                print(f"  Speech cycle {interruption['speech_cycle']} ({interruption['speech_side']}) at {interruption['speech_timestamp']}")
                print(f"  Interrupted {len(interruption['interrupted_translations'])} active translation(s):")
                
                for interrupted in interruption['interrupted_translations']:
                    print(f"    - {interrupted['side']} translation {interrupted['response_id'][:12]}...")
                    print(f"      Running for {interrupted['duration_ms']}ms (started at {interrupted['started_at']})")
                print()
                
        # Correlation analysis
        correlation_analysis = self.analyze_incomplete_translation_correlation()
        print("Interruption-Incompletion Correlation:")
        print("-" * 40)
        
        corr = correlation_analysis["interruption_completion_correlation"]
        print(f"Total interrupted translations: {corr['total_interrupted']}")
        print(f"Interrupted AND incomplete: {corr['interrupted_and_incomplete']}")
        print(f"Interrupted but completed: {corr['interrupted_but_completed']}")
        print(f"Not interrupted but incomplete: {corr['not_interrupted_but_incomplete']}")
        print(f"Correlation rate: {corr['correlation_percentage']:.1f}%")
        print()
        
        # Response ID analysis
        print("Response ID Analysis:")
        print("-" * 25)
        print(f"Total response IDs created: {len(correlation_analysis['interrupted_response_ids']) + len(correlation_analysis['completed_response_ids'] - correlation_analysis['interrupted_response_ids']) + len(correlation_analysis['incomplete_response_ids'] - correlation_analysis['interrupted_response_ids'])}")
        print(f"Interrupted response IDs: {len(correlation_analysis['interrupted_response_ids'])}")
        print(f"Completed response IDs: {len(correlation_analysis['completed_response_ids'])}")
        print(f"Incomplete response IDs: {len(correlation_analysis['incomplete_response_ids'])}")
        print()
        
        # Recommendations
        print("Analysis Results:")
        print("-" * 20)
        
        if interruption_analysis['interruption_rate'] > 50:
            print("🚨 HIGH INTERRUPTION RATE DETECTED!")
            print("   - More than 50% of speech events interrupt active translations")
            print("   - This is likely the primary cause of incomplete translations")
            
        if corr['correlation_percentage'] > 80:
            print("🚨 STRONG CORRELATION: Interruptions → Incomplete Translations")
            print(f"   - {corr['correlation_percentage']:.1f}% of interrupted translations fail to complete")
            print("   - This confirms the interruption hypothesis")
            
        if corr['not_interrupted_but_incomplete'] > 0:
            print(f"⚠️  {corr['not_interrupted_but_incomplete']} translations incomplete without interruption")
            print("   - May indicate other causes (timeouts, API errors)")
            
        print("\nRecommendations:")
        print("1. 🎯 Implement interruption handling in OpenAI API calls")
        print("2. 🔄 Add proper cleanup for interrupted translations")
        print("3. 📊 Add logging for interruption detection and handling")
        print("4. ⏱️  Consider translation timeout mechanisms")

def main():
    """Main function to process log input."""
    analyzer = InterruptionAnalyzer()
    
    print("Reading log data for translation interruption analysis...")
    print("(Paste log content and press Ctrl+D when done, or pipe from file)")
    print()
    
    line_count = 0
    for line in sys.stdin:
        line = line.strip()
        if line:
            analyzer.parse_log_line(line)
            line_count += 1
            
    print(f"Processed {line_count} log lines")
    print()
    
    analyzer.print_analysis()

if __name__ == "__main__":
    main()