#!/usr/bin/env python3
"""
Speech Detection Timing Analysis Script

This script analyzes speech detection timing to identify if delay accumulation
is caused by issues with when speech is detected vs when translation starts.

The hypothesis is that delays persist through pauses because there's a timing
drift in speech detection or translation triggering that doesn't get reset.

Usage:
    python analyze_speech_detection_timing.py < logfile.txt
"""

import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class SpeechCycle:
    cycle_number: int
    side: str
    
    # Speech detection events
    speech_started_time: Optional[datetime] = None
    speech_stopped_time: Optional[datetime] = None
    
    # Translation events  
    response_created_time: Optional[datetime] = None
    first_audio_time: Optional[datetime] = None
    response_done_time: Optional[datetime] = None
    
    # Buffer events
    buffer_cleared_time: Optional[datetime] = None
    buffer_committed_time: Optional[datetime] = None
    
    # Calculated delays (in ms)
    speech_duration: Optional[int] = None
    speech_to_translation_delay: Optional[int] = None
    translation_processing_time: Optional[int] = None
    total_cycle_time: Optional[int] = None

class SpeechDetectionAnalyzer:
    def __init__(self):
        self.cycles: List[SpeechCycle] = []
        self.current_cycle: Optional[SpeechCycle] = None
        self.cycle_counter = 0
        
    def parse_log_line(self, line: str):
        """Parse a single log line and extract speech detection timing."""
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
                
                if event_type == "input_audio_buffer.speech_started":
                    # Start new speech cycle
                    self.cycle_counter += 1
                    if self.current_cycle:
                        # Finish previous cycle
                        self.cycles.append(self.current_cycle)
                    
                    self.current_cycle = SpeechCycle(
                        cycle_number=self.cycle_counter,
                        side=side,
                        speech_started_time=timestamp
                    )
                    
                elif event_type == "input_audio_buffer.speech_stopped":
                    if self.current_cycle and self.current_cycle.side == side:
                        self.current_cycle.speech_stopped_time = timestamp
                        if self.current_cycle.speech_started_time:
                            self.current_cycle.speech_duration = int(
                                (timestamp - self.current_cycle.speech_started_time).total_seconds() * 1000
                            )
                            
                elif event_type == "response.created":
                    if self.current_cycle and self.current_cycle.side == side:
                        self.current_cycle.response_created_time = timestamp
                        if self.current_cycle.speech_stopped_time:
                            self.current_cycle.speech_to_translation_delay = int(
                                (timestamp - self.current_cycle.speech_stopped_time).total_seconds() * 1000
                            )
                            
                elif event_type == "response.audio.delta":
                    if self.current_cycle and self.current_cycle.side == side:
                        if not self.current_cycle.first_audio_time:
                            self.current_cycle.first_audio_time = timestamp
                            
                elif event_type == "response.done":
                    if self.current_cycle and self.current_cycle.side == side:
                        self.current_cycle.response_done_time = timestamp
                        if self.current_cycle.response_created_time:
                            self.current_cycle.translation_processing_time = int(
                                (timestamp - self.current_cycle.response_created_time).total_seconds() * 1000
                            )
                        if self.current_cycle.speech_started_time:
                            self.current_cycle.total_cycle_time = int(
                                (timestamp - self.current_cycle.speech_started_time).total_seconds() * 1000
                            )
                            
                elif event_type == "input_audio_buffer.cleared":
                    if self.current_cycle and self.current_cycle.side == side:
                        self.current_cycle.buffer_cleared_time = timestamp
                        
                elif event_type == "input_audio_buffer.committed":
                    if self.current_cycle and self.current_cycle.side == side:
                        self.current_cycle.buffer_committed_time = timestamp
                        
            except json.JSONDecodeError:
                pass
                
    def finalize_analysis(self):
        """Finalize the analysis by adding any remaining cycle."""
        if self.current_cycle:
            self.cycles.append(self.current_cycle)
            
    def calculate_inter_cycle_gaps(self) -> List[Tuple[int, int, str]]:
        """Calculate gaps between speech cycles."""
        gaps = []
        
        for i in range(1, len(self.cycles)):
            prev_cycle = self.cycles[i-1]
            curr_cycle = self.cycles[i]
            
            # Calculate gap from end of previous cycle to start of current cycle
            if prev_cycle.response_done_time and curr_cycle.speech_started_time:
                gap = int((curr_cycle.speech_started_time - prev_cycle.response_done_time).total_seconds() * 1000)
                gaps.append((i, gap, "response_done_to_speech_start"))
            elif prev_cycle.speech_stopped_time and curr_cycle.speech_started_time:
                gap = int((curr_cycle.speech_started_time - prev_cycle.speech_stopped_time).total_seconds() * 1000)
                gaps.append((i, gap, "speech_stop_to_speech_start"))
                
        return gaps
        
    def detect_timing_drift(self) -> Dict:
        """Detect timing drift patterns in speech detection."""
        if len(self.cycles) < 3:
            return {"pattern": "insufficient_data"}
            
        # Analyze speech-to-translation delays
        speech_to_translation_delays = [c.speech_to_translation_delay for c in self.cycles if c.speech_to_translation_delay]
        
        # Analyze inter-cycle gaps
        gaps = self.calculate_inter_cycle_gaps()
        gap_values = [gap for _, gap, _ in gaps]
        
        def analyze_trend(values, name):
            if len(values) < 3:
                return {"trend": "insufficient_data"}
                
            # Calculate trend
            first_half = values[:len(values)//2]
            last_half = values[len(values)//2:]
            
            avg_first = sum(first_half) / len(first_half)
            avg_last = sum(last_half) / len(last_half)
            
            increase = avg_last - avg_first
            increase_pct = (increase / avg_first) * 100 if avg_first > 0 else 0
            
            return {
                "avg_first_half": avg_first,
                "avg_last_half": avg_last,
                "increase_ms": increase,
                "increase_pct": increase_pct,
                "min": min(values),
                "max": max(values),
                "range": max(values) - min(values)
            }
            
        return {
            "speech_to_translation_delays": analyze_trend(speech_to_translation_delays, "speech_to_translation"),
            "inter_cycle_gaps": analyze_trend(gap_values, "inter_cycle_gaps"),
            "total_cycles": len(self.cycles)
        }
        
    def print_analysis(self):
        """Print comprehensive speech detection timing analysis."""
        print("Speech Detection Timing Analysis")
        print("=" * 60)
        print(f"Total speech cycles analyzed: {len(self.cycles)}")
        print()
        
        if len(self.cycles) < 2:
            print("⚠️  Need at least 2 cycles to analyze timing patterns")
            return
            
        # Speech cycle details
        print("Speech Cycle Timing Details:")
        print("-" * 100)
        print(f"{'#':<3} {'Side':<6} {'Speech Dur':<10} {'Speech->Trans':<12} {'Trans Time':<10} {'Total':<10}")
        print("-" * 100)
        
        for cycle in self.cycles:
            speech_dur = f"{cycle.speech_duration}ms" if cycle.speech_duration else "N/A"
            speech_to_trans = f"{cycle.speech_to_translation_delay}ms" if cycle.speech_to_translation_delay else "N/A"
            trans_time = f"{cycle.translation_processing_time}ms" if cycle.translation_processing_time else "N/A"
            total_time = f"{cycle.total_cycle_time}ms" if cycle.total_cycle_time else "N/A"
            
            print(f"{cycle.cycle_number:<3} {cycle.side:<6} {speech_dur:<10} {speech_to_trans:<12} {trans_time:<10} {total_time:<10}")
        print()
        
        # Inter-cycle gap analysis
        gaps = self.calculate_inter_cycle_gaps()
        if gaps:
            print("Inter-Cycle Gap Analysis:")
            print("-" * 60)
            print(f"{'Gap #':<6} {'Gap (ms)':<10} {'Type':<25} {'Trend':<10}")
            print("-" * 60)
            
            prev_gap = None
            for gap_num, gap_ms, gap_type in gaps:
                trend = ""
                if prev_gap is not None:
                    if gap_ms > prev_gap:
                        trend = f"+{gap_ms - prev_gap}ms"
                    elif gap_ms < prev_gap:
                        trend = f"{gap_ms - prev_gap}ms"
                    else:
                        trend = "same"
                
                print(f"{gap_num:<6} {gap_ms:<10} {gap_type:<25} {trend:<10}")
                prev_gap = gap_ms
            print()
            
        # Timing drift analysis
        drift_analysis = self.detect_timing_drift()
        
        print("Timing Drift Analysis:")
        print("-" * 40)
        
        speech_to_trans = drift_analysis.get("speech_to_translation_delays", {})
        if speech_to_trans.get("increase_ms"):
            increase = speech_to_trans["increase_ms"]
            increase_pct = speech_to_trans["increase_pct"]
            
            if abs(increase) > 100:  # More than 100ms change
                if increase > 0:
                    print(f"🚨 SPEECH-TO-TRANSLATION DELAY INCREASING!")
                    print(f"   Average increase: {increase:.0f}ms ({increase_pct:.1f}%)")
                else:
                    print(f"✅ Speech-to-translation delay decreasing")
                    print(f"   Average decrease: {abs(increase):.0f}ms ({abs(increase_pct):.1f}%)")
            else:
                print(f"✅ Speech-to-translation delay stable")
                
            print(f"   Range: {speech_to_trans['min']:.0f}ms - {speech_to_trans['max']:.0f}ms")
            print(f"   First half avg: {speech_to_trans['avg_first_half']:.0f}ms")
            print(f"   Last half avg: {speech_to_trans['avg_last_half']:.0f}ms")
            print()
            
        inter_cycle = drift_analysis.get("inter_cycle_gaps", {})
        if inter_cycle.get("increase_ms"):
            increase = inter_cycle["increase_ms"]
            increase_pct = inter_cycle["increase_pct"]
            
            if abs(increase) > 1000:  # More than 1 second change
                if increase > 0:
                    print(f"🚨 INTER-CYCLE GAPS INCREASING!")
                    print(f"   Average increase: {increase:.0f}ms ({increase_pct:.1f}%)")
                    print(f"   This indicates delay accumulation between cycles")
                else:
                    print(f"✅ Inter-cycle gaps decreasing")
                    print(f"   Average decrease: {abs(increase):.0f}ms ({abs(increase_pct):.1f}%)")
            else:
                print(f"✅ Inter-cycle gaps stable")
                
            print(f"   Range: {inter_cycle['min']:.0f}ms - {inter_cycle['max']:.0f}ms")
            print(f"   First half avg: {inter_cycle['avg_first_half']:.0f}ms")
            print(f"   Last half avg: {inter_cycle['avg_last_half']:.0f}ms")
            print()
            
        # Recommendations
        print("Recommendations:")
        print("-" * 20)
        
        if speech_to_trans.get("increase_ms", 0) > 100:
            print("1. 🚨 Speech-to-translation delay is increasing")
            print("   - Check if input_audio_buffer.clear is working properly")
            print("   - Verify speech detection timing accuracy")
            print("   - Monitor for buffer accumulation issues")
            
        if inter_cycle.get("increase_ms", 0) > 1000:
            print("2. 🚨 Inter-cycle gaps are increasing significantly")
            print("   - This confirms delay accumulation between cycles")
            print("   - The issue persists through pauses, indicating systemic timing drift")
            print("   - Check for timing state that's not being reset between cycles")
            
        if inter_cycle.get("range", 0) > 10000:
            print("3. ⚠️  Large variation in inter-cycle gaps")
            print("   - Indicates inconsistent timing behavior")
            print("   - May suggest race conditions or timing synchronization issues")

def main():
    """Main function to process log input."""
    analyzer = SpeechDetectionAnalyzer()
    
    print("Reading log data for speech detection timing analysis...")
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