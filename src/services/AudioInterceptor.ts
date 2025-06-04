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
    // Audio Mixing/Replacement: Only forward untranslated audio if no translation is active
    if (
      this.config.FORWARD_AUDIO_BEFORE_TRANSLATION === 'true' &&
      !this.#agentTranslationActive
    ) {
      this.sendAlignedAudio(
        this.#callerSocket,
        message.media.payload,
        parseInt(message.media.timestamp),
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
    // Audio Mixing/Replacement: Only forward untranslated audio if no translation is active
    if (
      this.config.FORWARD_AUDIO_BEFORE_TRANSLATION === 'true' &&
      !this.#callerTranslationActive
    ) {
      this.sendAlignedAudio(
        this.#agentSocket,
        message.media.payload,
        parseInt(message.media.timestamp),
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
      'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17';
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
        turn_detection: { 
          type: 'server_vad', 
          threshold: 0.6,
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
        turn_detection: { 
          type: 'server_vad', 
          threshold: 0.6,
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
          this.logger.info(
            'Caller translation started - blocking untranslated audio forwarding',
          );
        }

        if (
          message.type === 'response.done' ||
          message.type === 'response.audio.done'
        ) {
          this.#callerTranslationActive = false;
          this.#callerActiveResponseId = undefined;
          this.logger.info(
            'Caller translation completed - resuming untranslated audio forwarding',
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
          this.sendAlignedAudio(this.#agentSocket, message.delta!, currentTime);
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
          this.logger.info(
            'Agent translation started - blocking untranslated audio forwarding',
          );
        }

        if (
          message.type === 'response.done' ||
          message.type === 'response.audio.done'
        ) {
          this.#agentTranslationActive = false;
          this.#agentActiveResponseId = undefined;
          this.logger.info(
            'Agent translation completed - resuming untranslated audio forwarding',
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
          this.sendAlignedAudio(
            this.#callerSocket,
            message.delta!,
            currentTime,
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

  private reportOnSocketTimeToFirstAudioBufferAdd(messages: BufferedMessage[]) {
    const filtered = messages.filter(
      (message) => message.first_audio_buffer_add_time,
    );
    const totalTime = filtered.reduce(
      (acc, { first_audio_buffer_add_time, vad_speech_stopped_time }) =>
        acc + (first_audio_buffer_add_time - vad_speech_stopped_time),
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
   * Sends audio with proper alignment and timing to prevent stuttering
   * Addresses Problem 2: Audio stutter and alignment issues
   */
  private sendAlignedAudio(
    socket: StreamSocket | null,
    audioPayload: string,
    timestamp: number,
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
        `Sent aligned audio chunk: ${alignedAudio.length} bytes at timestamp ${timestamp}`,
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
