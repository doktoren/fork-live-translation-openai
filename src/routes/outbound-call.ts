import { FastifyBaseLogger, FastifyPluginAsync } from 'fastify';
import VoiceResponse from 'twilio/lib/twiml/VoiceResponse';

interface OutboundCallBody {
  Caller: string;
  // …any other fields you need, e.g. Called, ForwardTo, etc.
}

const outboundCall: FastifyPluginAsync = async (server) => {
  server.post<{ Body: OutboundCallBody }>(
    '/outbound-call',
    { logLevel: 'info' },
    async (req, reply) => {
      const logger = req.diScope.resolve<FastifyBaseLogger>('logger');
      const response = new VoiceResponse();

      try {
        const forwardTo = server.config.CALL_THIS_NUMBER_INSTEAD as string; 
        const { Caller: inboundCaller } = req.body;  // now typed
    
        if (forwardTo) {
          logger.info('Forwarding to external number %s', forwardTo);
          response.say('Please hold while we connect you.');

          // Dial out
          response.dial(
            { callerId: inboundCaller },
            forwardTo
          );
        } else {
          logger.info('Enqueuing to Flex workflow %s', server.config.TWILIO_FLEX_WORKFLOW_SID);
          response.say('A customer is on the line.');
          response
            .enqueue({ workflowSid: server.config.TWILIO_FLEX_WORKFLOW_SID })
            .task(
              JSON.stringify({
                name: inboundCaller,
                type: 'inbound',
                from: inboundCaller,        // in case you want it later
              })
            );
        }

        reply.type('text/xml').send(response.toString());
      } catch (error) {
        logger.error('Error building TwiML:', { error });
        reply.status(500).send('Internal Server Error');
      }
    },
  );
};

export default outboundCall;
