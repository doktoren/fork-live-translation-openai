#!/usr/bin/env python3
"""
OpenAI API Performance Analysis Script

This script analyzes OpenAI API response times and identifies performance
degradation patterns that could cause delay accumulation.

Usage:
    python analyze_api_performance.py < logfile.txt
"""

import sys
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class APIPerformanceMetric:
    cycle_number: int
    side: str
    response_id: str
    
    # Key timing metrics
    response_created_time: datetime
    output_item_added_time: Optional[datetime] = None
    first_audio_time: Optional[datetime] = None
    response_done_time: Optional[datetime] = None
    
    # Performance metrics (in ms)
    time_to_output_item: Optional[int] = None
    time_to_first_audio: Optional[int] = None
    total_response_time: Optional[int] = None
    
    # Context metrics
    conversation_length: int = 0
    rate_limit_info: Optional[Dict] = None

class APIPerformanceAnalyzer:
    def __init__(self):
        self.metrics: List[APIPerformanceMetric] = []
        self.current_metric: Optional[APIPerformanceMetric] = None
        self.cycle_counter = 0
        self.conversation_items = 0
        
    def parse_log_line(self, line: str):
        """Parse a single log line and extract API performance data."""
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
                
                if event_type == "response.created":
                    # Start new performance metric
                    self.cycle_counter += 1
                    if self.current_metric:
                        # Finish previous metric
                        self.metrics.append(self.current_metric)
                    
                    self.current_metric = APIPerformanceMetric(
                        cycle_number=self.cycle_counter,
                        side=side,
                        response_id=message_data.get("response", {}).get("id", ""),
                        response_created_time=timestamp,
                        conversation_length=self.conversation_items
                    )
                    
                elif event_type == "response.output_item.added":
                    if self.current_metric and response_id == self.current_metric.response_id:
                        self.current_metric.output_item_added_time = timestamp
                        self.current_metric.time_to_output_item = int(
                            (timestamp - self.current_metric.response_created_time).total_seconds() * 1000
                        )
                        
                elif event_type == "response.audio.delta":
                    if self.current_metric and response_id == self.current_metric.response_id:
                        if not self.current_metric.first_audio_time:
                            self.current_metric.first_audio_time = timestamp
                            self.current_metric.time_to_first_audio = int(
                                (timestamp - self.current_metric.response_created_time).total_seconds() * 1000
                            )
                            
                elif event_type == "response.done":
                    if self.current_metric and response_id == self.current_metric.response_id:
                        self.current_metric.response_done_time = timestamp
                        self.current_metric.total_response_time = int(
                            (timestamp - self.current_metric.response_created_time).total_seconds() * 1000
                        )
                        
                elif event_type == "conversation.item.created":
                    self.conversation_items += 1
                    
                elif event_type == "rate_limits.updated":
                    if self.current_metric:
                        self.current_metric.rate_limit_info = message_data
                        
            except json.JSONDecodeError:
                pass
                
    def finalize_analysis(self):
        """Finalize the analysis by adding any remaining metric."""
        if self.current_metric:
            self.metrics.append(self.current_metric)
            
    def detect_performance_degradation(self) -> Dict:
        """Detect patterns of API performance degradation."""
        if len(self.metrics) < 3:
            return {"pattern": "insufficient_data"}
            
        # Analyze time to output item trend
        output_times = [m.time_to_output_item for m in self.metrics if m.time_to_output_item]
        audio_times = [m.time_to_first_audio for m in self.metrics if m.time_to_first_audio]
        total_times = [m.total_response_time for m in self.metrics if m.total_response_time]
        
        def analyze_trend(values, name):
            if len(values) < 3:
                return {"trend": "insufficient_data"}
                
            # Calculate trend
            first_third = values[:len(values)//3]
            last_third = values[-len(values)//3:]
            
            avg_first = sum(first_third) / len(first_third)
            avg_last = sum(last_third) / len(last_third)
            
            increase = avg_last - avg_first
            increase_pct = (increase / avg_first) * 100 if avg_first > 0 else 0
            
            trend_type = "stable"
            if increase_pct > 50:
                trend_type = "degrading"
            elif increase_pct < -20:
                trend_type = "improving"
            elif abs(increase_pct) > 20:
                trend_type = "unstable"
                
            return {
                "trend": trend_type,
                "avg_first_third": avg_first,
                "avg_last_third": avg_last,
                "increase_ms": increase,
                "increase_pct": increase_pct,
                "min": min(values),
                "max": max(values),
                "range": max(values) - min(values)
            }
            
        return {
            "output_item_timing": analyze_trend(output_times, "time_to_output_item"),
            "first_audio_timing": analyze_trend(audio_times, "time_to_first_audio"),
            "total_response_timing": analyze_trend(total_times, "total_response_time"),
            "conversation_growth": self.conversation_items,
            "total_cycles": len(self.metrics)
        }
        
    def print_analysis(self):
        """Print comprehensive API performance analysis."""
        print("OpenAI API Performance Analysis")
        print("=" * 60)
        print(f"Total translation cycles analyzed: {len(self.metrics)}")
        print(f"Total conversation items: {self.conversation_items}")
        print()
        
        if len(self.metrics) < 2:
            print("⚠️  Need at least 2 cycles to analyze performance trends")
            return
            
        # Performance metrics table
        print("API Performance Metrics by Cycle:")
        print("-" * 90)
        print(f"{'#':<3} {'Side':<6} {'To Output':<10} {'To Audio':<10} {'Total':<10} {'Conv Items':<10}")
        print("-" * 90)
        
        for metric in self.metrics:
            output_time = f"{metric.time_to_output_item}ms" if metric.time_to_output_item else "N/A"
            audio_time = f"{metric.time_to_first_audio}ms" if metric.time_to_first_audio else "N/A"
            total_time = f"{metric.total_response_time}ms" if metric.total_response_time else "N/A"
            
            print(f"{metric.cycle_number:<3} {metric.side:<6} {output_time:<10} {audio_time:<10} {total_time:<10} {metric.conversation_length:<10}")
        print()
        
        # Performance degradation analysis
        degradation = self.detect_performance_degradation()
        
        print("Performance Degradation Analysis:")
        print("-" * 50)
        
        for timing_type, analysis in degradation.items():
            if timing_type.endswith("_timing") and isinstance(analysis, dict):
                metric_name = timing_type.replace("_timing", "").replace("_", " ").title()
                print(f"\n{metric_name}:")
                
                if analysis.get("trend") == "insufficient_data":
                    print("  Insufficient data for analysis")
                    continue
                    
                trend = analysis["trend"]
                increase_pct = analysis["increase_pct"]
                increase_ms = analysis["increase_ms"]
                
                if trend == "degrading":
                    print(f"  🚨 PERFORMANCE DEGRADATION DETECTED!")
                    print(f"     Average increase: {increase_ms:.0f}ms ({increase_pct:.1f}%)")
                elif trend == "unstable":
                    print(f"  ⚠️  UNSTABLE PERFORMANCE")
                    print(f"     Variation: {increase_ms:.0f}ms ({increase_pct:.1f}%)")
                elif trend == "improving":
                    print(f"  ✅ Performance improving")
                    print(f"     Average decrease: {abs(increase_ms):.0f}ms ({abs(increase_pct):.1f}%)")
                else:
                    print(f"  ✅ Performance stable")
                    
                print(f"     Range: {analysis['min']:.0f}ms - {analysis['max']:.0f}ms")
                print(f"     First third avg: {analysis['avg_first_third']:.0f}ms")
                print(f"     Last third avg: {analysis['avg_last_third']:.0f}ms")
                
        print(f"\nConversation Growth: {degradation['conversation_growth']} items")
        
        # Rate limiting analysis
        rate_limited_cycles = [m for m in self.metrics if m.rate_limit_info]
        if rate_limited_cycles:
            print(f"\nRate Limiting Information:")
            print("-" * 30)
            print(f"Cycles with rate limit data: {len(rate_limited_cycles)}")
            # Could add more detailed rate limit analysis here
            
        # Recommendations
        print("\nRecommendations:")
        print("-" * 20)
        
        output_analysis = degradation.get("output_item_timing", {})
        if output_analysis.get("trend") == "degrading":
            print("1. 🚨 API response times are degrading significantly")
            print("   - Consider implementing request throttling")
            print("   - Monitor OpenAI API status and rate limits")
            print("   - Consider conversation context pruning")
            
        audio_analysis = degradation.get("first_audio_timing", {})
        if audio_analysis.get("trend") == "degrading":
            print("2. 🚨 Time to first audio is increasing")
            print("   - This directly impacts user experience")
            print("   - Consider audio streaming optimizations")
            
        total_analysis = degradation.get("total_response_timing", {})
        if total_analysis.get("trend") == "degrading":
            print("3. 🚨 Total response times are increasing")
            print("   - This causes the delay accumulation you're experiencing")
            print("   - Consider session management strategies")
            
        if degradation["conversation_growth"] > 20:
            print("4. 📈 Large conversation context detected")
            print("   - Consider implementing conversation pruning")
            print("   - Long contexts can slow down API responses")

def main():
    """Main function to process log input."""
    analyzer = APIPerformanceAnalyzer()
    
    print("Reading log data for API performance analysis...")
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