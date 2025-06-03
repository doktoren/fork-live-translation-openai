# Python Tests for OpenAI Realtime API

This folder contains Python test scripts for testing the OpenAI Realtime API translation functionality.

## Setup

### Option 1: Using uv (Recommended)

1. Install dependencies using uv:
```bash
cd python_tests
uv sync --no-install-project
```

### Option 2: Using pip

1. Install required Python packages:
```bash
pip install websockets ffmpeg-python
```

2. Ensure ffmpeg is installed on your system:
```bash
# On Ubuntu/Debian:
sudo apt install ffmpeg

# On macOS:
brew install ffmpeg
```

3. Set your OpenAI API key:
```bash
export OPENAI_API_KEY='your-openai-api-key-here'
```

## Test Script: test_openai_realtime.py

This script tests the OpenAI Realtime API by:

1. Converting `test/agent.mp3` to G.711 μ-law format (as required by the API)
2. Connecting to OpenAI Realtime API with the same parameters as `AudioInterceptor.ts`
3. Using the `AI_PROMPT_AGENT` prompt to translate from English to Danish
4. Sending the audio data for translation
5. Receiving the translated audio response
6. Converting the response back to MP3 format
7. Saving the result as `test/agent_translated.mp3`

### Usage

#### Using uv (Recommended)

```bash
# Make sure you're in the project root directory
cd /path/to/fork-live-translation-openai

# Set your OpenAI API key
export OPENAI_API_KEY='your-api-key-here'

# Run the test using uv
uv run --project python_tests python_tests/test_openai_realtime.py
```

#### Using Python directly

```bash
# Make sure you're in the project root directory
cd /path/to/fork-live-translation-openai

# Set your OpenAI API key
export OPENAI_API_KEY='your-api-key-here'

# Run the test
python python_tests/test_openai_realtime.py
```

### Configuration

The script uses the same parameters as `AudioInterceptor.ts`:

- **Model**: `gpt-4o-realtime-preview-2024-12-17`
- **Audio Format**: `g711_ulaw` (both input and output)
- **Turn Detection**: Server VAD with threshold 0.6
- **Temperature**: 0.6
- **Target Language**: Danish (configurable in the script)

### Expected Output

If successful, you should see:
```
Converted test/agent.mp3 to G.711 μ-law format: /tmp/tmpXXXXXX.wav
Audio file loaded (XXXX chars)
Connecting to OpenAI Realtime API...
Connected successfully!
Session configured with Danish translation prompt
Audio data sent to OpenAI
Audio buffer committed
Listening for responses...
Received message type: session.created
Session created successfully
...
Received audio chunk (XXX chars)
...
Audio response completed
Combining X audio chunks...
Converted G.711 μ-law to MP3: test/agent_translated.mp3
Connection closed

✅ Translation completed successfully!
Input: test/agent.mp3
Output: test/agent_translated.mp3
```

### Troubleshooting

1. **Missing API Key**: Make sure `OPENAI_API_KEY` environment variable is set
2. **Missing Input File**: Ensure `test/agent.mp3` exists
3. **FFmpeg Errors**: Make sure ffmpeg is properly installed
4. **WebSocket Errors**: Check your internet connection and API key validity
5. **No Audio Response**: The input audio might not contain detectable speech

## Verification Script: verify_audio_conversion.py

This script tests the audio conversion pipeline without requiring an OpenAI API key:

#### Using uv (Recommended)

```bash
uv run --project python_tests python_tests/verify_audio_conversion.py
```

#### Using Python directly

```bash
python python_tests/verify_audio_conversion.py
```

This will:
1. Convert `python_tests/test/agent.mp3` to G.711 μ-law format
2. Convert it back to MP3 format
3. Verify that the conversion pipeline is working correctly

## Structure Verification Script: test_script_structure.py

This script verifies the complete test script structure without requiring an OpenAI API key:

#### Using uv (Recommended)

```bash
uv run --project python_tests python_tests/test_script_structure.py
```

#### Using Python directly

```bash
python python_tests/test_script_structure.py
```

This will:
1. Test audio conversion pipeline
2. Verify session configuration structure
3. Validate that the script matches the TypeScript AudioInterceptor.ts implementation

### Files

- `test_openai_realtime.py` - Main test script for OpenAI Realtime API
- `verify_audio_conversion.py` - Audio conversion verification script (no API key required)
- `test_script_structure.py` - Complete script structure verification (no API key required)
- `README.md` - This documentation file

### Dependencies

- `websockets` - For WebSocket communication with OpenAI
- `ffmpeg-python` - For audio format conversion
- `asyncio` - For asynchronous operations
- `base64` - For audio data encoding
- `json` - For API message formatting