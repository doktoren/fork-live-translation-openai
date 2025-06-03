#!/usr/bin/env python3
"""
Simple script to verify that audio conversion is working correctly.
This can be run without an OpenAI API key to test the audio processing pipeline.
"""

import asyncio
import tempfile
import os
import ffmpeg
from pathlib import Path

async def convert_mp3_to_g711_ulaw(input_file: str, output_file: str):
    """Convert MP3 file to G.711 μ-law format."""
    try:
        (
            ffmpeg
            .input(input_file)
            .output(output_file, acodec='pcm_mulaw', ar=8000, ac=1, f='wav')
            .overwrite_output()
            .run(quiet=True)
        )
        print(f"✅ Converted {input_file} to G.711 μ-law format: {output_file}")
        return True
    except ffmpeg.Error as e:
        print(f"❌ Error converting audio: {e}")
        return False

async def convert_g711_ulaw_to_mp3(input_file: str, output_file: str):
    """Convert G.711 μ-law format back to MP3."""
    try:
        (
            ffmpeg
            .input(input_file)
            .output(output_file, acodec='libmp3lame', ar=8000, ac=1)
            .overwrite_output()
            .run(quiet=True)
        )
        print(f"✅ Converted G.711 μ-law to MP3: {output_file}")
        return True
    except ffmpeg.Error as e:
        print(f"❌ Error converting audio: {e}")
        return False

def get_file_info(file_path: str):
    """Get basic file information."""
    if os.path.exists(file_path):
        size = os.path.getsize(file_path)
        print(f"📁 {file_path}: {size} bytes")
        return size
    else:
        print(f"❌ File not found: {file_path}")
        return 0

async def main():
    print("🔧 Audio Conversion Verification Test")
    print("=" * 50)
    
    # File paths
    input_file = 'test/agent.mp3'
    test_output = 'test/agent_conversion_test.mp3'
    
    # Check if input file exists
    if not os.path.exists(input_file):
        print(f"❌ Input file {input_file} not found")
        print("Please ensure the test audio file exists.")
        return
    
    print(f"📂 Input file: {input_file}")
    original_size = get_file_info(input_file)
    
    try:
        # Create temporary files for conversion
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_g711_file = temp_file.name
        
        print("\n🔄 Step 1: Converting MP3 to G.711 μ-law...")
        success1 = await convert_mp3_to_g711_ulaw(input_file, temp_g711_file)
        
        if success1:
            g711_size = get_file_info(temp_g711_file)
            
            print("\n🔄 Step 2: Converting G.711 μ-law back to MP3...")
            success2 = await convert_g711_ulaw_to_mp3(temp_g711_file, test_output)
            
            if success2:
                final_size = get_file_info(test_output)
                
                print("\n📊 Conversion Summary:")
                print(f"   Original MP3: {original_size} bytes")
                print(f"   G.711 μ-law:  {g711_size} bytes")
                print(f"   Final MP3:    {final_size} bytes")
                
                print("\n✅ Audio conversion pipeline working correctly!")
                print(f"   Test output saved to: {test_output}")
                
                # Clean up test file
                try:
                    os.unlink(test_output)
                    print(f"🧹 Cleaned up test file: {test_output}")
                except:
                    pass
            else:
                print("\n❌ Failed to convert G.711 μ-law back to MP3")
        else:
            print("\n❌ Failed to convert MP3 to G.711 μ-law")
        
    except Exception as e:
        print(f"\n❌ Error during conversion test: {e}")
    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_g711_file)
        except:
            pass
    
    print("\n🏁 Verification test completed!")

if __name__ == "__main__":
    asyncio.run(main())