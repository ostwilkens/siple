# SIPLE

(SIP + Simple)

Disclaimer: This was written in the passionate frenzy of a sleepless night, and lacks proper structure or comments. Consider it a work in progress. 

This is an example of a **bridge between SIP and Websockets**. Make SIP simple by not having to use SIP! The proxy takes care of all the dirty SIP work, and allows your applications to send and receive raw audio data. 

This project was born out of the wish for a way to easily handle real-time two-way audio on a headless server. Existing solutions are either low quality (pyVoIP), require a full browser (JsSIP), or are a pain to install (PJSUA2). This solution wraps the mess that is PJSUA2 in a docker container (or/and remote server). *Note: The dockerhub image used needs to be vetted before any serious use.*

In theory, this example is (probably) pretty close to supporting multiple callers and simultaneous calls. 

To test it: 
- set up the proxy locally or on a remote server
- update credentials in example.py
- connect the example to the proxy
- call your 46elks number, and talk to ChatGPT in real time!
