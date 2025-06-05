#!/usr/bin/env python3
"""
Delay Accumulation Analysis Script

This script focuses specifically on identifying delay accumulation patterns
in translation timing by analyzing multiple translation cycles.

Usage:
    python analyze_delay_accumulation.py < logfile.txt
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
    response_id: str
    start_time: datetime
    start_ms: int
    end_time: Optional[datetime] = None
    end_ms: Optional[int] = None
    first_audio_time: Optional[datetime] = None
    first_audio_ms: Optional[int] = None
    speech_stopped_time: Optional[datetime] = None
    speech_stopped_ms: Optional[int] = None
    
    @property
    def duration_ms(self) -> Optional[int]:
        if self.end_ms and self.start_ms:
            return self.end_ms - self.start_ms
        return None
    
    @property
    def time_to_first_audio_ms(self) -> Optional[int]:
        if self.first_audio_ms and self.start_ms:
            return self.first_audio_ms - self.start_ms
        return None

class DelayAccumulationAnalyzer:
    def __init__(self):
        self.cycles: List[TranslationCycle] = []
        self.current_cycle: Optional[TranslationCycle] = None
        self.cycle_counter = 0
        
    def parse_log_line(self, line: str):
        """Parse a single log line and update cycle tracking."""
        # Match timestamp pattern
        timestamp_match = re.match(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\]', line)
        if not timestamp_match:
            return
            
        timestamp_str = timestamp_match.group(1)
        try:
            timestamp = datetime.strptime(f"2025-06-05 {timestamp_str}", "%Y-%m-%d %H:%M:%S.%f")
            timestamp_ms = int(timestamp.timestamp() * 1000)
        except ValueError:
            return
            
        # Look for key events
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
                
                if event_type == "response.created":
                    # Start new cycle
                    self.cycle_counter += 1
                    if self.current_cycle:
                        # Finish previous cycle if not already finished
                        self.cycles.append(self.current_cycle)
                    
                    self.current_cycle = TranslationCycle(
                        cycle_number=self.cycle_counter,
                        side=side,
                        response_id=message_data.get("response", {}).get("id", ""),
                        start_time=timestamp,
                        start_ms=timestamp_ms
                    )
                    
                elif event_type in ["response.done", "response.audio.done"]:
                    if self.current_cycle and response_id == self.current_cycle.response_id:
                        self.current_cycle.end_time = timestamp
                        self.current_cycle.end_ms = timestamp_ms
                        
                elif event_type == "response.audio.delta":
                    if self.current_cycle and response_id == self.current_cycle.response_id:
                        if not self.current_cycle.first_audio_time:
                            self.current_cycle.first_audio_time = timestamp
                            self.current_cycle.first_audio_ms = timestamp_ms
                            
                elif event_type == "input_audio_buffer.speech_stopped":
                    if self.current_cycle:
                        self.current_cycle.speech_stopped_time = timestamp
                        self.current_cycle.speech_stopped_ms = timestamp_ms
                        
            except json.JSONDecodeError:
                pass
                
    def finalize_analysis(self):
        """Finalize the analysis by adding any remaining cycle."""
        if self.current_cycle:
            self.cycles.append(self.current_cycle)
            
    def calculate_gaps(self) -> List[Tuple[int, int, str]]:
        """Calculate gaps between translation cycles."""
        gaps = []
        
        for i in range(1, len(self.cycles)):
            prev_cycle = self.cycles[i-1]
            curr_cycle = self.cycles[i]
            
            # Use end time if available, otherwise use first audio time
            prev_end = prev_cycle.end_ms or prev_cycle.first_audio_ms
            if prev_end and curr_cycle.start_ms:
                gap = curr_cycle.start_ms - prev_end
                gap_type = "end_to_start" if prev_cycle.end_ms else "audio_to_start"
                gaps.append((i, gap, gap_type))
                
        return gaps
        
    def detect_accumulation_pattern(self) -> Dict:
        """Detect if there's a delay accumulation pattern."""
        gaps = self.calculate_gaps()
        if len(gaps) < 3:
            return {"pattern": "insufficient_data", "gaps": gaps}
            
        gap_values = [gap for _, gap, _ in gaps]
        
        # Check for increasing trend
        increasing_count = 0
        decreasing_count = 0
        
        for i in range(1, len(gap_values)):
            if gap_values[i] > gap_values[i-1]:
                increasing_count += 1
            elif gap_values[i] < gap_values[i-1]:
                decreasing_count += 1
                
        total_comparisons = len(gap_values) - 1
        increasing_ratio = increasing_count / total_comparisons if total_comparisons > 0 else 0
        
        # Calculate trend statistics
        first_gap = gap_values[0]
        last_gap = gap_values[-1]
        total_increase = last_gap - first_gap
        avg_gap = sum(gap_values) / len(gap_values)
        max_gap = max(gap_values)
        min_gap = min(gap_values)
        
        pattern_type = "stable"
        if increasing_ratio > 0.6:
            pattern_type = "accumulating"
        elif increasing_ratio < 0.4:
            pattern_type = "decreasing"
        elif max_gap - min_gap > 2000:  # More than 2 second variation
            pattern_type = "unstable"
            
        return {
            "pattern": pattern_type,
            "increasing_ratio": increasing_ratio,
            "total_increase": total_increase,
            "avg_gap": avg_gap,
            "max_gap": max_gap,
            "min_gap": min_gap,
            "gap_range": max_gap - min_gap,
            "gaps": gaps
        }
        
    def print_analysis(self):
        """Print comprehensive delay accumulation analysis."""
        print("Delay Accumulation Analysis")
        print("=" * 60)
        print(f"Total translation cycles: {len(self.cycles)}")
        
        if len(self.cycles) < 2:
            print("⚠️  Need at least 2 complete cycles to analyze accumulation")
            return
            
        print()
        
        # Cycle summary
        print("Translation Cycle Summary:")
        print("-" * 80)
        print(f"{'#':<3} {'Side':<6} {'Start Time':<12} {'Duration':<10} {'To Audio':<10} {'Complete':<8}")
        print("-" * 80)
        
        for cycle in self.cycles:
            start_time = cycle.start_time.strftime("%H:%M:%S.%f")[:-3]
            duration = f"{cycle.duration_ms}ms" if cycle.duration_ms else "N/A"
            to_audio = f"{cycle.time_to_first_audio_ms}ms" if cycle.time_to_first_audio_ms else "N/A"
            complete = "Yes" if cycle.end_ms else "No"
            
            print(f"{cycle.cycle_number:<3} {cycle.side:<6} {start_time:<12} {duration:<10} {to_audio:<10} {complete:<8}")
        print()
        
        # Gap analysis
        pattern_analysis = self.detect_accumulation_pattern()
        gaps = pattern_analysis["gaps"]
        
        if gaps:
            print("Gap Analysis Between Cycles:")
            print("-" * 60)
            print(f"{'Gap #':<6} {'Gap (ms)':<10} {'Type':<15} {'Trend':<10}")
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
                
                print(f"{gap_num:<6} {gap_ms:<10} {gap_type:<15} {trend:<10}")
                prev_gap = gap_ms
            print()
            
            # Pattern analysis
            print("Accumulation Pattern Analysis:")
            print("-" * 40)
            pattern = pattern_analysis["pattern"]
            print(f"Pattern type: {pattern}")
            
            if pattern == "accumulating":
                print("🚨 DELAY ACCUMULATION DETECTED!")
                print(f"   Increasing trend in {pattern_analysis['increasing_ratio']:.1%} of gaps")
                print(f"   Total increase: {pattern_analysis['total_increase']}ms")
            elif pattern == "unstable":
                print("⚠️  UNSTABLE TIMING DETECTED!")
                print(f"   Gap range: {pattern_analysis['gap_range']}ms")
            elif pattern == "stable":
                print("✅ Timing appears stable")
            elif pattern == "decreasing":
                print("📉 Gaps are decreasing (timing improving)")
                
            print(f"Average gap: {pattern_analysis['avg_gap']:.1f}ms")
            print(f"Gap range: {pattern_analysis['min_gap']}ms - {pattern_analysis['max_gap']}ms")
            print()
            
        # Recommendations
        print("Recommendations:")
        print("-" * 20)
        if pattern_analysis["pattern"] == "accumulating":
            print("1. Check for audio buffer accumulation between cycles")
            print("2. Verify input_audio_buffer.clear timing")
            print("3. Monitor for memory leaks or resource exhaustion")
            print("4. Check if translation processing time is increasing")
        elif pattern_analysis["pattern"] == "unstable":
            print("1. Check for network latency variations")
            print("2. Monitor OpenAI API response times")
            print("3. Verify timing state management consistency")
        else:
            print("1. Continue monitoring for pattern changes")
            print("2. Collect more data for better analysis")

def main():
    """Main function to process log input."""
    analyzer = DelayAccumulationAnalyzer()
    
    print("Reading log data for delay accumulation analysis...")
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