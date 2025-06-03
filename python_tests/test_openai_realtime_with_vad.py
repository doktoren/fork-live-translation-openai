#!/usr/bin/env python3
"""
Alternative OpenAI Realtime API test script with server VAD enabled.
This version uses the same turn detection settings as the TypeScript implementation.
"""

import asyncio
import json
import os
import base64
import tempfile
import websockets
import ffmpeg

# Import the AI_PROMPT_AGENT from the TypeScript file
def get_ai_prompt_agent():
    """Extract AI_PROMPT_AGENT from the TypeScript prompts file."""
    try:
        with open('../src/prompts.ts', 'r') as f:
            content = f.read()
            
        # Find the AI_PROMPT_AGENT export
        start_marker = "export const AI_PROMPT_AGENT = `"
        end_marker = "`;"
        
        start_idx = content.find(start_marker)
        if start_idx == -1:
            raise ValueError("AI_PROMPT_AGENT not found in prompts.ts")
        
        start_idx += len(start_marker)
        end_idx = content.find(end_marker, start_idx)
        if end_idx == -1:
            raise ValueError("End of AI_PROMPT_AGENT not found in prompts.ts")
        
        return content[start_idx:end_idx]
    except Exception as e:
        print(f"Error reading AI_PROMPT_AGENT: {e}")
        return "You are a helpful translation assistant. Translate the audio to Danish."

AI_PROMPT_AGENT = get_ai_prompt_agent()

class OpenAIRealtimeTranslator:
    def __init__(self, api_key: str, caller_language: str = "English"):
        self.api_key = api_key
        self.caller_language = caller_language
        self.websocket = None
        self.audio_chunks = []
        self.session_ready = False
        
    async def connect(self):
        """Connect to OpenAI Realtime API."""
        url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
        headers = [
            ('Authorization', f'Bearer {self.api_key}'),
            ('OpenAI-Beta', 'realtime=v1')
        ]
        
        print("Connecting to OpenAI Realtime API...")
        self.websocket = await websockets.connect(url, additional_headers=headers)
        print("Connected successfully!")
        
        # Configure the session with server VAD (same as TypeScript)
        agent_prompt = AI_PROMPT_AGENT.replace('[CALLER_LANGUAGE]', self.caller_language)
        
        session_config = {
            'type': 'session.update',
            'session': {
                'modalities': ['text', 'audio'],
                'instructions': agent_prompt,
                'input_audio_format': 'g711_ulaw',
                'output_audio_format': 'g711_ulaw',
                'turn_detection': {'type': 'server_vad', 'threshold': 0.6},
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
        
        # Commit the audio buffer
        commit_message = {'type': 'input_audio_buffer.commit'}
        await self.websocket.send(json.dumps(commit_message))
        print("Audio buffer committed")

        # Wait a moment for processing
        await asyncio.sleep(0.5)

        # Manually create response (even with server VAD, this helps ensure response generation)
        response_message = {
            'type': 'response.create',
            'response': {
                'modalities': ['text', 'audio'],
                'instructions': 'Translate the provided audio to Danish. Respond only with the translation.'
            }
        }
        await self.websocket.send(json.dumps(response_message))
        print("Response creation requested")
        
    async def listen_for_responses(self):
        """Listen for responses from OpenAI and collect audio chunks."""
        print("Listening for responses...")
        
        response_started = False
        
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
                
                elif data.get('type') == 'input_audio_buffer.committed':
                    print("Audio buffer committed by server")
                
                elif data.get('type') == 'conversation.item.created':
                    print("Conversation item created")
                
                elif data.get('type') == 'response.created':
                    print("Response creation started")
                    response_started = True
                
                elif data.get('type') == 'response.audio.delta':
                    # Collect audio chunks
                    if 'delta' in data:
                        self.audio_chunks.append(data['delta'])
                        print(f"Received audio chunk ({len(data['delta'])} chars)")
                
                elif data.get('type') == 'response.audio.done':
                    print("Audio response completed")
                    if response_started:
                        break
                
                elif data.get('type') == 'response.done':
                    print("Response completed")
                    if response_started:
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
    
    async def close(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()

def convert_mp3_to_g711_ulaw(input_file: str) -> str:
    """Convert MP3 file to G.711 μ-law format and return as base64."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
        temp_path = temp_file.name
    
    try:
        # Convert MP3 to G.711 μ-law WAV
        (
            ffmpeg
            .input(input_file)
            .output(temp_path, acodec='pcm_mulaw', ar=8000, ac=1, f='wav')
            .overwrite_output()
            .run(quiet=True)
        )
        
        print(f"Converted {input_file} to G.711 μ-law format: {temp_path}")
        
        # Read the converted file and encode as base64
        with open(temp_path, 'rb') as f:
            audio_data = f.read()
        
        return base64.b64encode(audio_data).decode('utf-8')
    
    finally:
        # Clean up temporary file
        if os.path.exists(temp_path):
            os.unlink(temp_path)

def save_audio_chunks_as_mp3(audio_chunks: list, output_file: str):
    """Convert base64 audio chunks back to MP3 format."""
    if not audio_chunks:
        print("No audio chunks to save")
        return False
    
    print(f"Combining {len(audio_chunks)} audio chunks...")
    
    # Decode each chunk individually and combine the raw audio bytes
    audio_data = b''
    for i, chunk in enumerate(audio_chunks):
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
    
    # Save as temporary raw audio file first
    with tempfile.NamedTemporaryFile(suffix='.raw', delete=False) as temp_file:
        temp_path = temp_file.name
        temp_file.write(audio_data)
    
    print(f"Saved raw audio data to: {temp_path}")
    
    try:
        # First, let's check what format the raw audio is in
        try:
            probe = ffmpeg.probe(temp_path)
            print(f"Raw audio file info: {probe}")
        except:
            print("Could not probe raw audio file - treating as G.711 μ-law")
        
        # Convert raw G.711 μ-law to MP3 with enhanced quality
        # Apply audio filters to improve quality and upsample for better output
        (
            ffmpeg
            .input(temp_path, f='mulaw', ar=8000, ac=1)
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
        
        print(f"Saved translated audio as: {output_file}")
        return True
    
    except ffmpeg.Error as e:
        print(f"Error converting audio to MP3: {e}")
        print(f"FFmpeg stdout: {e.stdout.decode() if e.stdout else 'None'}")
        print(f"FFmpeg stderr: {e.stderr.decode() if e.stderr else 'None'}")
        return False
    except Exception as e:
        print(f"Unexpected error converting audio: {e}")
        return False
    
    finally:
        # Clean up temporary file
        if os.path.exists(temp_path):
            os.unlink(temp_path)

async def main():
    """Main function to test OpenAI Realtime API translation."""
    # Check for API key
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set")
        print("Please set your OpenAI API key: export OPENAI_API_KEY='your-api-key'")
        return
    
    # File paths
    input_file = "test/agent.mp3"
    output_file = "test/agent_translated.mp3"
    
    # Check if input file exists
    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found")
        return
    
    try:
        # Convert audio to G.711 μ-law format
        audio_base64 = convert_mp3_to_g711_ulaw(input_file)
        print(f"Audio file loaded ({len(audio_base64)} chars)")
        
        # Create translator instance
        translator = OpenAIRealtimeTranslator(api_key, caller_language="English")
        
        # Connect to OpenAI
        await translator.connect()
        
        # Wait for session to be ready
        await asyncio.sleep(1)
        
        # Send audio data
        await translator.send_audio_data(audio_base64)
        
        # Listen for responses
        await translator.listen_for_responses()
        
        # Close connection
        await translator.close()
        print("Connection closed")
        
        # Save translated audio
        if translator.audio_chunks:
            success = save_audio_chunks_as_mp3(translator.audio_chunks, output_file)
            if success:
                print(f"✅ Translation completed successfully!")
                print(f"Original: {input_file}")
                print(f"Translated: {output_file}")
            else:
                print("❌ Failed to save translated audio")
        else:
            print("❌ Translation failed - no audio received")
    
    except Exception as e:
        print(f"❌ Error during translation: {e}")

if __name__ == "__main__":
    asyncio.run(main())