import { FastifyBaseLogger } from 'fastify';
import WebSocket from 'ws';

type BaseAudioMessage = {
  sequenceNumber: number;
};

export type ConnectedBaseAudioMessage = BaseAudioMessage & {
  event: 'connected';
  protocol: string;
};

// All types here https://www.twilio.com/docs/voice/media-streams/websocket-messages#send-websocket-messages-to-twilio
export type StartBaseAudioMessage = BaseAudioMessage & {
  event: 'start';
  start: {
    streamSid: string;
    accountSid: string;
    callSid: string;
    track: 'inbound' | 'outbound';
    customParameters: Record<string, unknown>;
  };
};

export type MediaBaseAudioMessage = BaseAudioMessage & {
  event: 'media';
  media: {
    chunk: number;
    timestamp: string;
    payload: string;
    streamSid: string;
    track: 'inbound' | 'outbound';
  };
};

export type StopBaseAudioMessage = BaseAudioMessage & {
  event: 'stop';
  stop: {
    accountSid: string;
    callSid: string;
  };
  from?: string;
};

export type MarkBaseAudioMessage = BaseAudioMessage & {
  event: 'mark';
  stop: {
    accountSid: string;
    callSid: string;
  };
};

type AudioMessage =
  | StartBaseAudioMessage
  | MediaBaseAudioMessage
  | StopBaseAudioMessage
  | ConnectedBaseAudioMessage
  | MarkBaseAudioMessage;

type OnCallback<T> = (message: T) => void;

type StreamSocketOptions = {
  logger: FastifyBaseLogger;
  socket: WebSocket;
};
export default class StreamSocket {
  private readonly logger: FastifyBaseLogger;

  public readonly socket: WebSocket;

  private pingInterval: NodeJS.Timeout;

  public streamSid: string;

  public from?: string;

  private onStartCallback: OnCallback<StartBaseAudioMessage>[] = [];

  private onConnectedCallback: OnCallback<ConnectedBaseAudioMessage>[] = [];

  private onMediaCallback: OnCallback<MediaBaseAudioMessage>[] = [];

  private onStopCallback: OnCallback<StopBaseAudioMessage>[] = [];

  constructor(options: StreamSocketOptions) {
    this.logger = options.logger;
    this.socket = options.socket;
    // Start keep-alive pings every 30 seconds
    this.pingInterval = setInterval(() => {
      if (this.socket.readyState === WebSocket.OPEN) {
        this.socket.ping();
      }
    }, 30000);

    this.socket.on('message', this.onMessage);
    this.socket.on('close', () => {
      clearInterval(this.pingInterval);
      this.logger.info('WebSocket connection closed');
    });
    this.socket.on('error', (err) => {
      this.logger.error(`WebSocket error: ${err}`);
    });
  }

  public close() {
    clearInterval(this.pingInterval);
    this.socket.close();
  }

  /**
   * Adds a callback to the connected event
   * @param callback
   */
  public onConnected = (callback: OnCallback<ConnectedBaseAudioMessage>) => {
    this.onConnectedCallback.push(callback);
  };

  /**
   * Adds a callback to the start event
   * @param callback
   */
  public onStart = (callback: OnCallback<StartBaseAudioMessage>) => {
    this.onStartCallback.push(callback);
  };

  /**
   * Adds a callback to the media event
   * @param callback
   */
  public onMedia = (callback: OnCallback<MediaBaseAudioMessage>) => {
    this.onMediaCallback.push(callback);
  };

  /**
   * Adds a callback to the stop event
   * @param callback
   */
  public onStop = (callback: OnCallback<StopBaseAudioMessage>) => {
    this.onStopCallback.push(callback);
  };

  /**
   * Sends a message to the socket
   * @param messages
   * @param isLast
   */
  public send = (messages: string[], isLast = false) => {
    const buffers = messages.map((msg) => Buffer.from(msg, 'base64'));
    const payload = Buffer.concat(buffers).toString('base64');

    const mediaMessage = {
      event: 'media',
      streamSid: this.streamSid,
      media: {
        payload,
      },
    };

    this.socket.send(JSON.stringify(mediaMessage));
    if (isLast) {
      const markMessage = {
        event: 'mark',
        streamSid: this.streamSid,
        mark: {
          name: Date.now(),
        },
      };
      this.socket.send(JSON.stringify(markMessage));
    }
  };

  /**
   * Routes the message to the correct callback
   * @param message
   */
  private onMessage = (message: unknown) => {
    const parse = () => {
      let messageStr: string;
      
      if (typeof message === 'string') {
        messageStr = message;
      } else if (Buffer.isBuffer(message)) {
        messageStr = message.toString('utf8');
      } else if (message && typeof message === 'object' && 'toString' in message) {
        messageStr = (message as any).toString();
      } else {
        throw new Error(`Unsupported message type: ${typeof message}`);
      }
      
      return JSON.parse(messageStr) as AudioMessage;
    };

    try {
      const parsed = parse();

      if (parsed.event === 'start') {
        this.onStartCallback.map((cb) => cb(parsed));
        this.streamSid = parsed.start.streamSid;
      } else if (parsed.event === 'media') {
        this.onMediaCallback.map((cb) => cb(parsed));
      } else if (parsed.event === 'stop') {
        this.onMediaCallback = [];
        this.onStartCallback = [];
        this.onConnectedCallback = [];

        this.onStopCallback.map((cb) => cb({ ...parsed, from: this.from }));
      } else if (parsed.event === 'connected') {
        this.onConnectedCallback.map((cb) => cb(parsed));
      } else if (parsed.event === 'mark') {
        // do something
      } else {
        this.logger.error('Unknown event: %s', JSON.stringify(parsed));
      }
    } catch (error) {
      this.logger.error('Error parsing WebSocket message', { 
        error: error instanceof Error ? error.message : String(error),
        messageType: typeof message,
        messageLength: Buffer.isBuffer(message) ? message.length : (typeof message === 'string' ? message.length : 'unknown')
      });
      
      // Log the raw message for debugging, but limit size to prevent log spam
      if (Buffer.isBuffer(message)) {
        this.logger.error('Raw message (Buffer): %s', message.toString('utf8', 0, Math.min(500, message.length)));
      } else if (typeof message === 'string') {
        this.logger.error('Raw message (String): %s', message.substring(0, 500));
      } else {
        this.logger.error('Raw message (Other): %s', JSON.stringify(message).substring(0, 500));
      }
    }
  };
}
