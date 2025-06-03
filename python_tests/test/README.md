See https://chatgpt.com/share/683d61e6-25d0-8006-814a-df01b6402b2c

Use Joanna for English

```
aws polly synthesize-speech \
--output-format mp3 \
--voice-id Joanna \
--text-type ssml \
--text file://agent.ssml \
agent.mp3
```

Use Naja for Danish

```
aws polly synthesize-speech \
--output-format mp3 \
--voice-id Naja \
--text-type ssml \
--text file://technician.ssml \
technician.mp3
```