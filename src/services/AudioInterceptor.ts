import { FastifyBaseLogger } from 'fastify';
import WebSocket from 'ws';

import StreamSocket, { MediaBaseAudioMessage } from '@/services/StreamSocket';
import { Config } from '@/config';
import { AI_PROMPT_AGENT, AI_PROMPT_CALLER } from '@/prompts';

type AudioInterceptorOptions = {
  logger: FastifyBaseLogger;
  config: Config;
  callerLanguage: string;
};

type BufferedMessage = {
  message_id: string;
  first_audio_buffer_add_time?: number;
  vad_speech_stopped_time: number;
};

type OpenAIMessage = {
  event_id: string;
  first_audio_buffer_add_time?: number;
  vad_speech_stopped_time: number;
  type: string;
  delta?: string;
  response?: {
    id: string;
  };
};

export default class AudioInterceptor {
  private static instance: AudioInterceptor;

  private readonly logger: FastifyBaseLogger;

  private config: Config;

  private readonly callerLanguage?: string;

  #callerPingInterval?: NodeJS.Timeout;

  #callerSocket?: StreamSocket;

  #agentPingInterval?: NodeJS.Timeout;

  #agentSocket?: StreamSocket;

  #callerOpenAISocket?: WebSocket;

  #agentOpenAISocket?: WebSocket;

  #agentFirstAudioTime?: number;

  #callerMessages?: BufferedMessage[];

  #agentMessages?: BufferedMessage[];

  // Audio mixing state tracking
  #callerTranslationActive: boolean = false;

  #agentTranslationActive: boolean = false;

  #callerActiveResponseId?: string;

  #agentActiveResponseId?: string;

  // Audio timing and buffering for problem 2
  #audioBuffer = new Map<
    string,
    { payload: string; timestamp: number; sequenceNumber: number }[]
  >();

  // Timing compensation for maintaining proper audio timing
  #callerLastSpeechTimestamp?: number;
  #agentLastSpeechTimestamp?: number;
  #callerTranslationStartTime?: number;
  #agentTranslationStartTime?: number;
  #callerSpeechStartTimestamp?: number;
  #agentSpeechStartTimestamp?: number;

  // Tracking timing of translated audio playback on Twilio
  #callerOutputState = {
    currentStart: undefined as number | undefined,
    currentDurationMs: 0,
    lastStart: undefined as number | undefined,
    lastDurationMs: undefined as number | undefined,
  };

  #agentOutputState = {
    currentStart: undefined as number | undefined,
    currentDurationMs: 0,
    lastStart: undefined as number | undefined,
    lastDurationMs: undefined as number | undefined,
  };

  public constructor(options: AudioInterceptorOptions) {
    this.logger = options.logger;
    this.config = options.config;
    this.callerLanguage = options.callerLanguage;
    this.setupOpenAISockets();
  }

  /**
   * Closes the audio interceptor
   */
  public close() {
    if (this.#callerSocket) {
      this.#callerSocket.close();
      this.#callerSocket = null;
    }
    if (this.#agentSocket) {
      this.#agentSocket.close();
      this.#agentSocket = null;
    }
    if (this.#callerOpenAISocket) {
      clearInterval(this.#callerPingInterval!);
      this.#callerOpenAISocket.close();
    }
    if (this.#agentOpenAISocket) {
      clearInterval(this.#agentPingInterval!);
      this.#agentOpenAISocket.close();
    }

    // Reset audio mixing state
    this.#callerTranslationActive = false;
    this.#agentTranslationActive = false;
    this.#callerActiveResponseId = undefined;
    this.#agentActiveResponseId = undefined;
    this.#audioBuffer.clear();

    this.#callerOutputState = {
      currentStart: undefined,
      currentDurationMs: 0,
      lastStart: undefined,
      lastDurationMs: undefined,
    };
    this.#agentOutputState = {
      currentStart: undefined,
      currentDurationMs: 0,
      lastStart: undefined,
      lastDurationMs: undefined,
    };
    
    // Reset timing compensation state
    this.#callerLastSpeechTimestamp = undefined;
    this.#agentLastSpeechTimestamp = undefined;
    this.#callerTranslationStartTime = undefined;
    this.#agentTranslationStartTime = undefined;
    this.#callerSpeechStartTimestamp = undefined;
    this.#agentSpeechStartTimestamp = undefined;

    const callerTime = this.reportOnSocketTimeToFirstAudioBufferAdd(
      this.#callerMessages,
    );
    this.logger.info(`callerAverageTimeToFirstAudioBufferAdd = ${callerTime}`);
    const agentTime = this.reportOnSocketTimeToFirstAudioBufferAdd(
      this.#agentMessages,
    );
    this.logger.info(`agentAverageTimeToFirstAudioBufferAdd = ${agentTime}`);
  }

  /**
   * Starts the audio interception
   */
  public start() {
    if (!this.#agentSocket || !this.#callerSocket) {
      this.logger.error('Both sockets are not set. Cannot start interception');
      return;
    }

    this.logger.info('Initiating the websocket to OpenAI Realtime S2S API');
    // Start Audio Interception
    this.logger.info('Both sockets are set. Starting interception');
    this.#callerSocket.onMedia(this.translateAndForwardCallerAudio.bind(this));
    this.#agentSocket.onMedia(this.translateAndForwardAgentAudio.bind(this));
  }

  private translateAndForwardAgentAudio(message: MediaBaseAudioMessage) {
    const currentTimestamp = parseInt(message.media.timestamp);
    const currentSystemTime = new Date().getTime();
    
    // Track speech timing for compensation using system time for consistency
    if (!this.#agentSpeechStartTimestamp) {
      this.#agentSpeechStartTimestamp = currentSystemTime;
      this.logger.debug(`Agent speech started at system time: ${currentSystemTime} (media timestamp: ${currentTimestamp})`);
    }
    this.#agentLastSpeechTimestamp = currentSystemTime;
    
    // Audio Mixing/Replacement: Only forward untranslated audio if no translation is active
    if (
      this.config.FORWARD_AUDIO_BEFORE_TRANSLATION === 'true' &&
      !this.#agentTranslationActive
    ) {
      this.sendAlignedAudio(
        this.#callerSocket,
        message.media.payload,
      );
    }

    // Wait for 1 second after the first time we hear audio from the agent
    // This ensures that we don't send beeps from Flex to OpenAI when the call
    // first connects
    const now = new Date().getTime();
    if (!this.#agentFirstAudioTime) {
      this.#agentFirstAudioTime = now;
    } else if (now - this.#agentFirstAudioTime >= 1000) {
      if (!this.#agentOpenAISocket) {
        this.logger.error('Agent OpenAI WebSocket is not available.');
      } else {
        this.forwardAudioToOpenAIForTranslation(
          this.#agentOpenAISocket,
          message.media.payload,
        );
      }
    }
  }

  private translateAndForwardCallerAudio(message: MediaBaseAudioMessage) {
    const currentTimestamp = parseInt(message.media.timestamp);
    const currentSystemTime = new Date().getTime();
    
    // Track speech timing for compensation using system time for consistency
    if (!this.#callerSpeechStartTimestamp) {
      this.#callerSpeechStartTimestamp = currentSystemTime;
      this.logger.debug(`Caller speech started at system time: ${currentSystemTime} (media timestamp: ${currentTimestamp})`);
    }
    this.#callerLastSpeechTimestamp = currentSystemTime;
    
    // Audio Mixing/Replacement: Only forward untranslated audio if no translation is active
    if (
      this.config.FORWARD_AUDIO_BEFORE_TRANSLATION === 'true' &&
      !this.#callerTranslationActive
    ) {
      this.sendAlignedAudio(
        this.#agentSocket,
        message.media.payload,
      );
    }

    if (!this.#callerOpenAISocket) {
      this.logger.error('Caller OpenAI WebSocket is not available.');
      return;
    }
    this.forwardAudioToOpenAIForTranslation(
      this.#callerOpenAISocket,
      message.media.payload,
    );
  }

  /**
   * Setup the WebSocket connection to OpenAI Realtime S2S API
   * @private
   */
  private setupOpenAISockets() {
    this.logger.warn('setupOpenAISockets called');
    const url =
      'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2025-06-03';
    const callerSocket = new WebSocket(url, {
      headers: {
        Authorization: `Bearer ${this.config.OPENAI_API_KEY}`,
        'OpenAI-Beta': 'realtime=v1',
      },
    });
    const agentSocket = new WebSocket(url, {
      headers: {
        Authorization: `Bearer ${this.config.OPENAI_API_KEY}`,
        'OpenAI-Beta': 'realtime=v1',
      },
    });
    const callerPrompt = AI_PROMPT_CALLER.replace(
      /\[CALLER_LANGUAGE\]/g,
      this.callerLanguage,
    );
    const agentPrompt = AI_PROMPT_AGENT.replace(
      /\[CALLER_LANGUAGE\]/g,
      this.callerLanguage,
    );

    // Store the WebSocket instances
    this.#callerOpenAISocket = callerSocket;
    this.#agentOpenAISocket = agentSocket;

    // start keep-alive pings every 30 seconds for OpenAI sockets
    this.#callerPingInterval = setInterval(() => {
      if (this.#callerOpenAISocket?.readyState === WebSocket.OPEN) {
        this.#callerOpenAISocket.ping();
      }
    }, 30000);
    this.#agentPingInterval = setInterval(() => {
      if (this.#agentOpenAISocket?.readyState === WebSocket.OPEN) {
        this.#agentOpenAISocket.ping();
      }
    }, 30000);

    // Configure the Realtime AI Agents with new 'session.update' client event
    const callerConfigMsg = {
      type: 'session.update',
      session: {
        modalities: ['text', 'audio'],
        instructions: callerPrompt,
        input_audio_format: 'g711_ulaw',
        output_audio_format: 'g711_ulaw',
        // input_audio_transcription: {model: 'whisper-1'},
        input_audio_transcription: null,
        // Even with input_audio_transcription commented out I'm these messages in the log:
        // [09:02:38.298] INFO: Agent message from OpenAI: {"type":"response.audio_transcript.delta",
        // "event_id":"event_BeyuUrTCzhrKWQqHjqEVh","response_id":"resp_BeyuRV6EOJaEJ3NAH1Ogb",
        // "item_id":"item_BeyuR5QsLF1dbBLPScCVZ","output_index":0,"content_index":0,"delta":" ignor"}
        turn_detection: { 
          type: 'server_vad',
          threshold: 0.6,
          silence_duration_ms: 1000,
          create_response: true,
          interrupt_response: false  // Prevent interrupting ongoing translations
        },
        // turn_detection: {
        //   'type': 'semantic_vad',
        //   'eagerness': 'high',  // "low" | "medium" | "high" | "auto", // optional
        //   // 'create_response': true, // only in conversation mode
        //   // 'interrupt_response': true, // only in conversation mode
        // },
        // Setting temperature to minimum allowed value to get deterministic translation results
        temperature: 0.6,
      },
    };
    const agentConfigMsg = {
      type: 'session.update',
      session: {
        modalities: ['text', 'audio'],
        instructions: agentPrompt,
        input_audio_format: 'g711_ulaw',
        output_audio_format: 'g711_ulaw',
        // input_audio_transcription: {model: 'whisper-1'},
        input_audio_transcription: null,
        turn_detection: { 
          type: 'server_vad', 
          threshold: 0.6,
          silence_duration_ms: 1000,
          create_response: true,
          interrupt_response: false  // Prevent interrupting ongoing translations
        },
        // Setting temperature to minimum allowed value to get deterministic translation results
        temperature: 0.6,
      },
    };

    // Event listeners for when the connection is opened
    callerSocket.on('open', () => {
      this.logger.info('Caller webSocket connection to OpenAI is open now.');
      // Send the initial prompt/config message to OpenAI for the Translation Agent.
      this.sendMessageToOpenAI(callerSocket, callerConfigMsg);
      this.logger.info(
        callerConfigMsg,
        'Caller session has been configured with the following settings:',
      );
    });
    agentSocket.on('open', () => {
      this.logger.info('Agent webSocket connection to OpenAI is open now.');
      // Send the initial prompt/config message to OpenAI for the Translation Agent.
      this.sendMessageToOpenAI(agentSocket, agentConfigMsg);
      this.logger.info(
        agentConfigMsg,
        'Agent session has been configured with the following settings:',
      );
    });

    // Event listeners for when a message is received from the server
    callerSocket.on('message', (msg) => {
      try {
        this.logger.info(`Caller message from OpenAI: ${msg}`);
        const currentTime = new Date().getTime();
        const message = JSON.parse(msg.toString()) as OpenAIMessage;

        // Track translation state for audio mixing
        if (message.type === 'response.created') {
          this.#callerTranslationActive = true;
          this.#callerActiveResponseId = message.response?.id;
          this.#callerTranslationStartTime = currentTime;
          this.logger.info(
            `Caller translation started at ${currentTime} - blocking untranslated audio forwarding`,
          );
          this.logger.info(
            `TRANSLATION_LIFECYCLE: caller response.created response_id=${message.response?.id} timestamp=${currentTime}`,
          );
        }

        if (
          message.type === 'response.done' ||
          message.type === 'response.audio.done'
        ) {
          this.#callerTranslationActive = false;
          const completedResponseId = this.#callerActiveResponseId;
          this.#callerActiveResponseId = undefined;
          this.#callerTranslationStartTime = undefined;

          this.finalizeTranslationOutput(this.#agentOutputState);
          
          // Reset timing state after translation to prevent long-term drift
          this.logger.debug(
            `Caller translation completed - resetting timing state. ` +
            `Speech start was: ${this.#callerSpeechStartTimestamp}, ` +
            `Last speech was: ${this.#callerLastSpeechTimestamp}`
          );
          this.#callerSpeechStartTimestamp = undefined;
          this.#callerLastSpeechTimestamp = undefined;
          
          this.logger.info(
            'Caller translation completed - resuming untranslated audio forwarding',
          );
          this.logger.info(
            `TRANSLATION_LIFECYCLE: caller ${message.type} response_id=${completedResponseId} timestamp=${currentTime}`,
          );
        }

        if (message.type === 'input_audio_buffer.speech_started') {
          // Check for interruption: speech starting while translation is active
          if (this.#agentTranslationActive && this.#agentActiveResponseId) {
            this.logger.warn(
              `INTERRUPTION_DETECTED: caller speech_started while agent translation active! ` +
              `Active response_id=${this.#agentActiveResponseId}, ` +
              `translation_duration=${currentTime - (this.#agentTranslationStartTime || currentTime)}ms, ` +
              `timestamp=${currentTime}`
            );
          }
          this.logger.info(
            `SPEECH_LIFECYCLE: caller speech_started timestamp=${currentTime}`,
          );
        }

        if (message.type === 'input_audio_buffer.speech_stopped') {
          if (!this.#callerMessages) {
            this.#callerMessages = [];
          }
          this.#callerMessages.push({
            message_id: message.event_id,
            vad_speech_stopped_time: currentTime,
          });
          
          // DON'T reset speech start timestamp here - preserve it for translation timing
          // It will be reset when translation completes (response.done/response.audio.done)
          
          // Clear the input audio buffer after speech stops to prevent audio accumulation
          // This is critical for preventing delay buildup between translation cycles
          this.sendMessageToOpenAI(this.#callerOpenAISocket!, {
            type: 'input_audio_buffer.clear'
          });
          this.logger.info('Cleared caller input audio buffer after speech stopped');
          this.logger.info(
            `SPEECH_LIFECYCLE: caller speech_stopped timestamp=${currentTime}`,
          );
        }

        if (message.type === 'response.audio.delta') {
          // Handle an audio message from OpenAI, post translation
          this.logger.info('Received caller translation from OpenAI');
          if (
            this.#callerMessages &&
            this.#callerMessages.length > 0 &&
            !this.#callerMessages[this.#callerMessages.length - 1]
              .first_audio_buffer_add_time
          ) {
            this.#callerMessages[
              this.#callerMessages.length - 1
            ].first_audio_buffer_add_time = currentTime;
          }
          // Track translation timing to detect delay accumulation
          this.trackTranslationTiming('caller', currentTime);
          this.sendTranslationOutput(
            this.#agentSocket,
            message.delta!,
            this.#agentOutputState,
          );
        }
      } catch (error) {
        this.logger.error('Error processing caller OpenAI message', {
          error: error instanceof Error ? error.message : String(error),
          messageType: typeof msg,
          messageLength: msg ? msg.toString().length : 0,
        });
      }
    });
    agentSocket.on('message', (msg) => {
      try {
        this.logger.info(`Agent message from OpenAI: ${msg.toString()}`);
        const currentTime = new Date().getTime();
        const message = JSON.parse(msg.toString()) as OpenAIMessage;

        // Track translation state for audio mixing
        if (message.type === 'response.created') {
          this.#agentTranslationActive = true;
          this.#agentActiveResponseId = message.response?.id;
          this.#agentTranslationStartTime = currentTime;
          this.logger.info(
            `Agent translation started at ${currentTime} - blocking untranslated audio forwarding`,
          );
          this.logger.info(
            `TRANSLATION_LIFECYCLE: agent response.created response_id=${message.response?.id} timestamp=${currentTime}`,
          );
        }

        if (
          message.type === 'response.done' ||
          message.type === 'response.audio.done'
        ) {
          this.#agentTranslationActive = false;
          const completedResponseId = this.#agentActiveResponseId;
          this.#agentActiveResponseId = undefined;
          this.#agentTranslationStartTime = undefined;

          this.finalizeTranslationOutput(this.#callerOutputState);
          
          // Reset timing state after translation to prevent long-term drift
          this.logger.debug(
            `Agent translation completed - resetting timing state. ` +
            `Speech start was: ${this.#agentSpeechStartTimestamp}, ` +
            `Last speech was: ${this.#agentLastSpeechTimestamp}`
          );
          this.#agentSpeechStartTimestamp = undefined;
          this.#agentLastSpeechTimestamp = undefined;
          
          this.logger.info(
            'Agent translation completed - resuming untranslated audio forwarding',
          );
          this.logger.info(
            `TRANSLATION_LIFECYCLE: agent ${message.type} response_id=${completedResponseId} timestamp=${currentTime}`,
          );
        }

        if (message.type === 'input_audio_buffer.speech_started') {
          // Check for interruption: speech starting while translation is active
          if (this.#callerTranslationActive && this.#callerActiveResponseId) {
            this.logger.warn(
              `INTERRUPTION_DETECTED: agent speech_started while caller translation active! ` +
              `Active response_id=${this.#callerActiveResponseId}, ` +
              `translation_duration=${currentTime - (this.#callerTranslationStartTime || currentTime)}ms, ` +
              `timestamp=${currentTime}`
            );
          }
          this.logger.info(
            `SPEECH_LIFECYCLE: agent speech_started timestamp=${currentTime}`,
          );
        }

        if (message.type === 'input_audio_buffer.speech_stopped') {
          if (!this.#agentMessages) {
            this.#agentMessages = [];
          }
          this.#agentMessages.push({
            message_id: message.event_id,
            vad_speech_stopped_time: currentTime,
          });
          
          // DON'T reset speech start timestamp here - preserve it for translation timing
          // It will be reset when translation completes (response.done/response.audio.done)
          
          // Clear the input audio buffer after speech stops to prevent audio accumulation
          // This is critical for preventing delay buildup between translation cycles
          this.sendMessageToOpenAI(this.#agentOpenAISocket!, {
            type: 'input_audio_buffer.clear'
          });
          this.logger.info('Cleared agent input audio buffer after speech stopped');
          this.logger.info(
            `SPEECH_LIFECYCLE: agent speech_stopped timestamp=${currentTime}`,
          );
        }

        if (message.type === 'response.audio.delta') {
          // Handle an audio message from OpenAI, post translation
          this.logger.info('Received agent translation from OpenAI');
          if (
            this.#agentMessages &&
            this.#agentMessages.length > 0 &&
            !this.#agentMessages[this.#agentMessages.length - 1]
              .first_audio_buffer_add_time
          ) {
            this.#agentMessages[
              this.#agentMessages.length - 1
            ].first_audio_buffer_add_time = currentTime;
          }
          // Track translation timing to detect delay accumulation
          this.trackTranslationTiming('agent', currentTime);
          this.sendTranslationOutput(
            this.#callerSocket,
            message.delta!,
            this.#callerOutputState,
          );
        }
      } catch (error) {
        this.logger.error('Error processing agent OpenAI message', {
          error: error instanceof Error ? error.message : String(error),
          messageType: typeof msg,
          messageLength: msg ? msg.toString().length : 0,
        });
      }
    });

    // Event listeners for when an error occurs
    callerSocket.on('error', (error: Error) => {
      this.logger.error(`Caller webSocket error: ${error}`);
    });
    agentSocket.on('error', (error: Error) => {
      this.logger.error(`Agent webSocket error: ${error}`);
    });

    // Event listeners for when the connection is closed
    callerSocket.on('close', () => {
      clearInterval(this.#callerPingInterval!);
      this.logger.info('Caller webSocket connection to OpenAI is closed now.');
    });

    agentSocket.on('close', () => {
      clearInterval(this.#agentPingInterval!);
      this.logger.info('Agent webSocket connection to OpenAI is closed now.');
    });
  }

  private reportOnSocketTimeToFirstAudioBufferAdd(
    messages: BufferedMessage[] = [],
  ) {
    if (!messages.length) {
      return 0;
    }

    const filtered = messages.filter(
      (message) => message.first_audio_buffer_add_time !== undefined,
    );

    if (filtered.length === 0) {
      return 0;
    }

    const totalTime = filtered.reduce(
      (acc, { first_audio_buffer_add_time, vad_speech_stopped_time }) =>
        acc + (first_audio_buffer_add_time! - vad_speech_stopped_time),
      0,
    );

    return totalTime / filtered.length;
  }

  private forwardAudioToOpenAIForTranslation(socket: WebSocket, audio: String) {
    this.sendMessageToOpenAI(socket, {
      type: 'input_audio_buffer.append',
      audio,
    });
  }

  private sendMessageToOpenAI(socket: WebSocket, message: object) {
    if (socket.readyState === WebSocket.OPEN) {
      const jsonMessage = JSON.stringify(message);
      socket.send(jsonMessage);
    } else {
      this.logger.error('WebSocket is not open. Unable to send message.');
    }
  }

  /**
   * Tracks translation timing to detect and prevent delay accumulation
   */
  private trackTranslationTiming(
    side: 'caller' | 'agent',
    currentTime: number,
  ): void {
    const speechStartTimestamp = side === 'caller' 
      ? this.#callerSpeechStartTimestamp 
      : this.#agentSpeechStartTimestamp;
    const translationStartTime = side === 'caller' 
      ? this.#callerTranslationStartTime 
      : this.#agentTranslationStartTime;

    if (!speechStartTimestamp || !translationStartTime) {
      this.logger.warn(
        `Missing timing data for ${side}: speechStart=${speechStartTimestamp}, ` +
        `translationStart=${translationStartTime}. Cannot track timing.`
      );
      return;
    }

    // Calculate the translation processing delay
    const translationDelay = currentTime - translationStartTime;
    const totalDelay = currentTime - speechStartTimestamp;
    
    // Always log timing information for analysis
    this.logger.info(
      `Translation timing for ${side}: speech_start=${speechStartTimestamp}, ` +
      `translation_start=${translationStartTime}, current=${currentTime}, ` +
      `translation_delay=${translationDelay}ms, total_delay=${totalDelay}ms`,
    );
    
    // Log warning if translation delay is becoming excessive
    if (translationDelay > 5000) {
      this.logger.warn(
        `Excessive translation delay detected for ${side}: ${translationDelay}ms. ` +
        `This may indicate network or API issues.`,
      );
      
      // If delay is excessive, clear the input buffer to prevent further accumulation
      const socket = side === 'caller' ? this.#callerOpenAISocket : this.#agentOpenAISocket;
      if (socket) {
        this.sendMessageToOpenAI(socket, {
          type: 'input_audio_buffer.clear'
        });
        this.logger.warn(`Cleared ${side} input buffer due to excessive delay`);
      }
    }
  }

  /**
   * Sends audio with proper alignment to prevent stuttering
   * Addresses Problem 2: Audio stutter and alignment issues
   */
  private sendAlignedAudio(
    socket: StreamSocket | null,
    audioPayload: string,
  ) {
    if (
      !socket ||
      !socket.socket ||
      socket.socket.readyState !== WebSocket.OPEN
    ) {
      this.logger.debug(
        'Socket is not available or not open, skipping audio send',
      );
      return;
    }

    try {
      // Align audio to G.711 frame boundaries to prevent clicks/pops
      const alignedAudio = this.alignAudioFrames(audioPayload);

      // Use the existing StreamSocket send method which handles proper formatting
      socket.send([alignedAudio]);

      this.logger.debug(
        `Sent aligned audio chunk: ${alignedAudio.length} bytes`,
      );
    } catch (error) {
      this.logger.error('Error sending aligned audio:', error);
      // Fallback to original method if alignment fails and socket is still available
      if (
        socket &&
        socket.socket &&
        socket.socket.readyState === WebSocket.OPEN
      ) {
        try {
          socket.send([audioPayload]);
        } catch (fallbackError) {
          this.logger.error('Error in fallback audio send:', fallbackError);
        }
      }
    }
  }

  /**
   * Handles sending translated audio to Twilio while optionally clearing
   * the audio buffer when starting a new segment.
   */
  private sendTranslationOutput(
    socket: StreamSocket | null,
    audioPayload: string,
    state: {
      currentStart?: number;
      currentDurationMs: number;
      lastStart?: number;
      lastDurationMs?: number;
    },
  ) {
    const now = Date.now();
    if (state.currentStart === undefined) {
      if (
        state.lastStart !== undefined &&
        state.lastDurationMs !== undefined &&
        state.lastStart + state.lastDurationMs <= now
      ) {
        socket?.clear();
      }
      state.currentStart = now;
      state.currentDurationMs = 0;
    }

    const durationMs =
      (Buffer.from(audioPayload, 'base64').length / 8000) * 1000;
    state.currentDurationMs += durationMs;

    this.sendAlignedAudio(socket, audioPayload);
  }

  private finalizeTranslationOutput(state: {
    currentStart?: number;
    currentDurationMs: number;
    lastStart?: number;
    lastDurationMs?: number;
  }) {
    if (state.currentStart !== undefined) {
      state.lastStart = state.currentStart;
      state.lastDurationMs = state.currentDurationMs;
    }
    state.currentStart = undefined;
    state.currentDurationMs = 0;
  }

  /**
   * Aligns audio data to G.711 μ-law frame boundaries
   * G.711 uses 8kHz sample rate with 8-bit samples
   */
  private alignAudioFrames(audioData: string): string {
    try {
      const buffer = Buffer.from(audioData, 'base64');

      // G.711 μ-law: each sample is 1 byte, 8000 samples per second
      // Align to 8-byte boundaries for better performance and to prevent artifacts
      const frameSize = 8;
      const alignedSize = Math.floor(buffer.length / frameSize) * frameSize;

      if (alignedSize === 0) {
        // If the chunk is too small, return as-is
        return audioData;
      }

      const alignedBuffer = buffer.slice(0, alignedSize);
      return alignedBuffer.toString('base64');
    } catch (error) {
      this.logger.error('Error aligning audio frames:', error);
      return audioData; // Return original data if alignment fails
    }
  }

  get callerSocket(): StreamSocket {
    if (!this.#callerSocket) {
      throw new Error('Caller socket not set');
    }
    return this.#callerSocket;
  }

  set callerSocket(value: StreamSocket) {
    this.#callerSocket = value;
  }

  get agentSocket(): StreamSocket {
    if (!this.#agentSocket) {
      throw new Error('Agent socket not set');
    }
    return this.#agentSocket;
  }

  set agentSocket(value: StreamSocket) {
    this.#agentSocket = value;
  }
}
