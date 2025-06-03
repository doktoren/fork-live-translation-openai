#!/usr/bin/env python3
"""
Test script for OpenAI Realtime API translation.
Converts python_tests/test/agent.mp3 to g711_ulaw format, sends it to OpenAI for translation to Danish,
and saves the result as python_tests/test/agent_translated.mp3.
"""

import asyncio
import websockets
import json
import base64
import os
import tempfile
import ffmpeg
from pathlib import Path

# AI_PROMPT_AGENT from src/prompts.ts
AI_PROMPT_AGENT = """
You are a translation machine. Your sole function is to translate the input text from English to [CALLER_LANGUAGE].
Do not add, omit, or alter any information.
Do not provide explanations, opinions, or any additional text beyond the direct translation.
You are not aware of any other facts, knowledge, or context beyond translation between English and [CALLER_LANGUAGE].
Wait until the speaker is done speaking before translating, and translate the entire input text from their turn.
Example interaction:
User: How many days of the week are there?
Assistant: ¿Cuantos días hay en la semana?
User: I have two brothers and one sister in my family.
Assistant: Tengo dos hermanos y una hermana en mi familia.
"""

class OpenAIRealtimeTest:
    def __init__(self, api_key: str, caller_language: str = "Danish"):
        self.api_key = api_key
        self.caller_language = caller_language
        self.websocket = None
        self.audio_chunks = []
        self.session_ready = False
        
    async def convert_mp3_to_g711_ulaw(self, input_file: str, output_file: str):
        """Convert MP3 file to G.711 μ-law format."""
        try:
            (
                ffmpeg
                .input(input_file)
                .output(output_file, acodec='pcm_mulaw', ar=8000, ac=1, f='wav')
                .overwrite_output()
                .run(quiet=True)
            )
            print(f"Converted {input_file} to G.711 μ-law format: {output_file}")
        except ffmpeg.Error as e:
            print(f"Error converting audio: {e}")
            raise
    
    async def convert_g711_ulaw_to_mp3(self, input_file: str, output_file: str):
        """Convert raw G.711 μ-law format back to MP3."""
        try:
            # First, let's try to probe the file
            try:
                probe = ffmpeg.probe(input_file)
                print(f"Input file info: {probe}")
            except:
                print("Could not probe raw audio file - treating as G.711 μ-law")
            
            # Convert raw G.711 μ-law to MP3 with enhanced quality
            # Apply audio filters to improve quality and upsample for better output
            (
                ffmpeg
                .input(input_file, f='mulaw', ar=8000, ac=1)
                .filter('volume', '1.5')  # Boost volume slightly
                .filter('highpass', f=80)  # Remove low-frequency noise
                .filter('lowpass', f=3400)  # Remove high-frequency noise (G.711 bandwidth limit)
                .output(
                    output_file, 
                    acodec='libmp3lame',  # Use LAME encoder for better quality
                    ar=22050,  # Upsample for better quality (but not too high to avoid artifacts)
                    ac=1,  # Mono
                    audio_bitrate='128k',  # Good bitrate for speech
                    q='2'  # High quality setting for LAME
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
            print(f"Converted G.711 μ-law to MP3: {output_file}")
        except ffmpeg.Error as e:
            print(f"Error converting audio: {e}")
            print(f"FFmpeg stdout: {e.stdout.decode() if e.stdout else 'None'}")
            print(f"FFmpeg stderr: {e.stderr.decode() if e.stderr else 'None'}")
            raise
    
    def read_audio_file_as_base64(self, file_path: str) -> str:
        """Read audio file and encode as base64."""
        with open(file_path, 'rb') as f:
            audio_data = f.read()
        return base64.b64encode(audio_data).decode('utf-8')
    
    async def connect_to_openai(self):
        """Establish WebSocket connection to OpenAI Realtime API."""
        url = 'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17'
        headers = [
            ('Authorization', f'Bearer {self.api_key}'),
            ('OpenAI-Beta', 'realtime=v1')
        ]
        
        print("Connecting to OpenAI Realtime API...")
        self.websocket = await websockets.connect(url, additional_headers=headers)
        print("Connected successfully!")
        
        # Configure the session
        agent_prompt = AI_PROMPT_AGENT.replace('[CALLER_LANGUAGE]', self.caller_language)
        
        session_config = {
            'type': 'session.update',
            'session': {
                'modalities': ['text', 'audio'],
                'instructions': agent_prompt,
                'input_audio_format': 'g711_ulaw',
                'output_audio_format': 'g711_ulaw',
                'turn_detection': None,  # Disable automatic turn detection to prevent feedback
                'temperature': 0.6
            }
        }
        
        await self.websocket.send(json.dumps(session_config))
        print("Session configured with Danish translation prompt")
        
    async def send_audio_data(self, audio_base64: str):
        """Send audio data to OpenAI for translation."""
        message = {
            'type': 'input_audio_buffer.append',
            'audio': audio_base64
        }
        
        await self.websocket.send(json.dumps(message))
        print("Audio data sent to OpenAI")
        
        # Commit the audio buffer to trigger processing
        commit_message = {
            'type': 'input_audio_buffer.commit'
        }
        await self.websocket.send(json.dumps(commit_message))
        print("Audio buffer committed")
        
        # Manually create a response since we disabled turn detection
        response_message = {
            'type': 'response.create',
            'response': {
                'modalities': ['text', 'audio'],
                'instructions': 'Translate the audio to Danish and respond with audio only.'
            }
        }
        await self.websocket.send(json.dumps(response_message))
        print("Response creation requested")
    
    async def listen_for_responses(self):
        """Listen for responses from OpenAI and collect audio chunks."""
        print("Listening for responses...")
        
        while True:
            try:
                message = await asyncio.wait_for(self.websocket.recv(), timeout=30.0)
                data = json.loads(message)
                
                print(f"Received message type: {data.get('type')}")
                
                if data.get('type') == 'session.created':
                    print("Session created successfully")
                    self.session_ready = True
                
                elif data.get('type') == 'session.updated':
                    print("Session updated successfully")
                
                elif data.get('type') == 'input_audio_buffer.speech_started':
                    print("Speech detection started")
                
                elif data.get('type') == 'input_audio_buffer.speech_stopped':
                    print("Speech detection stopped")
                
                elif data.get('type') == 'response.created':
                    print("Response creation started")
                
                elif data.get('type') == 'response.audio.delta':
                    # Collect audio chunks
                    if 'delta' in data:
                        self.audio_chunks.append(data['delta'])
                        print(f"Received audio chunk ({len(data['delta'])} chars)")
                
                elif data.get('type') == 'response.audio.done':
                    print("Audio response completed")
                    break
                
                elif data.get('type') == 'response.done':
                    print("Response completed")
                    break
                
                elif data.get('type') == 'error':
                    print(f"Error received: {data}")
                    break
                    
            except asyncio.TimeoutError:
                print("Timeout waiting for response")
                break
            except Exception as e:
                print(f"Error receiving message: {e}")
                break
    
    async def save_translated_audio(self, output_file: str):
        """Save the collected audio chunks to a file."""
        if not self.audio_chunks:
            print("No audio chunks received")
            return False
        
        print(f"Combining {len(self.audio_chunks)} audio chunks...")
        
        # Decode each chunk individually and combine the raw audio bytes
        audio_data = b''
        for i, chunk in enumerate(self.audio_chunks):
            try:
                chunk_data = base64.b64decode(chunk)
                audio_data += chunk_data
                print(f"Chunk {i+1}: {len(chunk)} chars -> {len(chunk_data)} bytes")
            except Exception as e:
                print(f"Error decoding chunk {i+1}: {e}")
                return False
        
        print(f"Total decoded audio data: {len(audio_data)} bytes")
        print(f"First 20 bytes: {audio_data[:20].hex()}")
        
        if len(audio_data) == 0:
            print("No audio data after decoding chunks")
            return False
        
        # Save to temporary raw audio file
        with tempfile.NamedTemporaryFile(suffix='.raw', delete=False) as temp_file:
            temp_file.write(audio_data)
            temp_g711_file = temp_file.name
            print(f"Saved raw audio data to: {temp_g711_file}")
        
        try:
            # Convert raw G.711 μ-law to MP3
            await self.convert_g711_ulaw_to_mp3(temp_g711_file, output_file)
            print(f"Translated audio saved to: {output_file}")
            return True
        finally:
            # Clean up temporary file
            os.unlink(temp_g711_file)
    
    async def close_connection(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            print("Connection closed")

async def main():
    # Check for OpenAI API key
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set")
        print("Please set your OpenAI API key: export OPENAI_API_KEY='your-api-key'")
        return
    
    # File paths
    input_file = 'test/agent.mp3'
    output_file = 'test/agent_translated.mp3'
    
    # Check if input file exists
    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found")
        return
    
    # Create test instance
    test = OpenAIRealtimeTest(api_key, caller_language="Danish")
    
    try:
        # Convert MP3 to G.711 μ-law format
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_g711_file = temp_file.name
        
        await test.convert_mp3_to_g711_ulaw(input_file, temp_g711_file)
        
        # Read audio file as base64
        audio_base64 = test.read_audio_file_as_base64(temp_g711_file)
        print(f"Audio file loaded ({len(audio_base64)} chars)")
        
        # Connect to OpenAI
        await test.connect_to_openai()
        
        # Wait for session to be ready
        await asyncio.sleep(1)
        
        # Start listening for responses in the background
        listen_task = asyncio.create_task(test.listen_for_responses())
        
        # Send audio data
        await test.send_audio_data(audio_base64)
        
        # Wait for responses
        await listen_task
        
        # Save translated audio
        success = await test.save_translated_audio(output_file)
        
        if success:
            print(f"\n✅ Translation completed successfully!")
            print(f"Input: {input_file}")
            print(f"Output: {output_file}")
        else:
            print("\n❌ Translation failed - no audio received")
        
    except Exception as e:
        print(f"Error during translation: {e}")
    finally:
        # Clean up
        if 'temp_g711_file' in locals():
            try:
                os.unlink(temp_g711_file)
            except:
                pass
        await test.close_connection()

if __name__ == "__main__":
    asyncio.run(main())