import { FastifyPluginAsyncTypebox } from '@fastify/type-provider-typebox';
import { FastifyBaseLogger } from 'fastify';
import Twilio from 'twilio';

import AudioInterceptor from '@/services/AudioInterceptor';
import StreamSocket, { StartBaseAudioMessage } from '@/services/StreamSocket';

const interceptWS: FastifyPluginAsyncTypebox = async (server) => {
  const twilio = Twilio(
    server.config.TWILIO_ACCOUNT_SID,
    server.config.TWILIO_AUTH_TOKEN,
  );

  server.get(
    '/intercept',
    { websocket: true },
    async (socket, req) => {
      const logger = req.diScope.resolve<FastifyBaseLogger>('logger');
      const ss = new StreamSocket({ logger, socket });
      const map = req.diScope.resolve<Map<string, AudioInterceptor>>(
        'audioInterceptors',
      );

      ss.onStart(async (message: StartBaseAudioMessage) => {
        const { customParameters } = message.start;
        const from = customParameters?.from;
        if (!from || typeof from !== 'string') return;

        // inbound leg
        if (customParameters.direction === 'inbound') {
          ss.from = from;
          const interceptor = new AudioInterceptor({
            logger,
            config: server.config,
            callerLanguage: String(customParameters.lang),
          });
          interceptor.callerSocket = ss;
          map.set(from, interceptor);
          logger.info(
            'Inbound stream for %s (streamSid=%s)',
            from,
            message.start.streamSid,
          );

          // decide where to forward
          const external = server.config.CALL_THIS_NUMBER_INSTEAD as string;
          const forwardTo = external || server.config.TWILIO_FLEX_NUMBER;
          const announcement = external
            ? 'Please hold while we connect you to an external number.'
            : 'A customer is on the line.';

          logger.info('Dialing %s for %s', forwardTo, from);
          await twilio.calls.create({
            from: server.config.TWILIO_CALLER_NUMBER,
            to: forwardTo,
            // If callerId is passed as an argument then the phone number of the caller
            // will be shown as the caller in the called phone. However, this is only
            // allowed if the caller is a verified number or purchased from Twilio.
            // callerId: from,
            twiml: `
              <Response>
                <Say>${announcement}</Say>
                <Connect>
                  <Stream
                    name="Outbound Audio Stream"
                    url="wss://${server.config.NGROK_DOMAIN}/intercept"
                  >
                    <Parameter name="direction" value="outbound"/>
                    <Parameter name="from"      value="${from}"/>
                  </Stream>
                </Connect>
              </Response>
            `,
          });
        }

        // outbound leg (agent or PSTN stream)
        if (customParameters.direction === 'outbound') {
          const interceptor = map.get(from);
          ss.from = from;
          if (!interceptor) {
            logger.error('No interceptor for %s', from);
            return;
          }

          interceptor.agentSocket = ss;
          logger.info(
            'Outbound stream for %s (streamSid=%s)',
            from,
            message.start.streamSid,
          );

          interceptor.start();
          logger.info('AudioInterceptor started for %s', from);
        }
      });

      ss.onStop((message) => {
        const from = message?.from;
        if (!from) {
          logger.info('Unknown stream closed');
          return;
        }
        const interceptor = map.get(from);
        if (!interceptor) {
          logger.error('No interceptor to close for %s', from);
          return;
        }
        logger.info('Closing interceptor for %s', from);
        interceptor.close();
        map.delete(from);
      });
    },
  );
};

export default interceptWS;
