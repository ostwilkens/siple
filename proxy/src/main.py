import asyncio
import threading
import websockets
import pjsua2 as pj
import json
import base64
import os
import logging
import queue
import time

logging.basicConfig(level=logging.INFO)

# VALID_USERS = {
#     "4600123456": "123ABC123ABC123ABC123ABC123ABC12" # example
# }

SIP_SERVER = os.getenv("SIP_SERVER")
WS_HOST_PORT = os.getenv("WS_HOST_PORT")

connected_clients = set()
ws_loop = None

accounts = {}
account_queue = queue.Queue()
unregister_account_queue = queue.Queue()
old_accounts = set()
received_audio_data = {}
audio_clear_signal = {}


class MyAudioMediaPort(pj.AudioMediaPort):
    username = None
    ci = None

    def __init__(self):
        pj.AudioMediaPort.__init__(self)

    def onFrameRequested(self, frame):
        frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO

        if audio_clear_signal[self.username]:
            logging.info("Clearing audio data")
            # clear received audio data
            while not received_audio_data[self.username].empty():
                received_audio_data[self.username].get()
            audio_clear_signal[self.username] = False

        # check if there is any audio data received from the client
        if received_audio_data[self.username].empty():
            frame.buf.resize(640)
            return

        audio_data = received_audio_data[self.username].get()
        audio_data_len = len(audio_data)

        frame.buf.resize(audio_data_len)
        for i in range(audio_data_len):
            frame.buf[i] = audio_data[i]

    def onFrameReceived(self, frame):
        byte_data = [frame.buf[i] for i in range(frame.buf.size())]
        
        if len(byte_data) > 0:
            base64_string = base64.b64encode(bytes(byte_data)).decode('utf-8')
            phone_number = self.ci.remoteUri.split(":")[1].split("@")[0]
            message = {
                "from": phone_number,
                "data": base64_string
            }
            message_json = json.dumps(message)

            asyncio.run_coroutine_threadsafe(
                send_message_to_clients(message_json), ws_loop
            )


class Call(pj.Call):

    username = None

    def __init__(self, acc, peer_uri="", chat=None, call_id=pj.PJSUA_INVALID_ID):
        pj.Call.__init__(self, acc, call_id)
        self.acc = acc
        self.aud_med = pj.AudioMedia

    def onCallState(self, prm):
        logging.info("onCallState")
        ci = self.getInfo()

        if ci.state == pj.PJSIP_INV_STATE_CONFIRMED:

            # player = pj.AudioMediaPlayer()
            # player.createPlayer("welcome.wav")

            i = 0
            for media in ci.media:
                if media.type == pj.PJMEDIA_TYPE_AUDIO:
                    self.aud_med = self.getAudioMedia(i)
                    break
                i = i + 1

            if self.aud_med != None:
                remote_media = pj.AudioMedia.typecastFromMedia(self.aud_med)

                fmt = pj.MediaFormatAudio()
                fmt.type = pj.PJMEDIA_TYPE_AUDIO
                fmt.clockRate = 16000
                fmt.channelCount = 1
                fmt.bitsPerSample = 16
                fmt.frameTimeUsec = 20000
                self.med_port = MyAudioMediaPort()
                self.med_port.ci = ci
                self.med_port.username = self.username
                self.med_port.createPort("med_port", fmt)
                
                remote_media.startTransmit(self.med_port) 

                self.med_port.startTransmit(remote_media)

                # record
                recorder = pj.AudioMediaRecorder()
                recorder.createRecorder(f"recording_{time.time()}.wav")
                remote_media.startTransmit(recorder)
                self.med_port.startTransmit(recorder)


        raise Exception("onCallState done!")

    def onCallMediaState(self, prm):
        logging.info("onCallMediaState")


class Account(pj.Account):

    username = None

    def onIncomingCall(self, prm):
        c = Call(self, call_id = prm.callId)
        c.username = self.username
        call_prm = pj.CallOpParam()
        call_prm.statusCode = 180
        c.answer(call_prm)
        ci = c.getInfo()
        logging.info(f"Incoming call from {ci.remoteUri}")
        call_prm.statusCode = 200
        c.answer(call_prm)
        raise Exception("onIncomingCall done")
    
    def __del__(self):
        self.shutdown()


async def send_message_to_clients(message):
    if connected_clients:
        await asyncio.gather(*(client.send(message) for client in connected_clients))


def username_password_from_auth_header(auth_header):
    """Extracts the username and password from an incoming basic auth header."""
    try:
        auth_type, credentials = auth_header.strip().split(" ")
        if auth_type != 'Basic':
            return None, None
        
        decoded = base64.b64decode(credentials).decode('utf-8')
        return decoded.split(':')
    except Exception:
        return None, None


async def websocket_handler(websocket, path):

    auth_header = websocket.request_headers.get("Authorization")
    username, password = username_password_from_auth_header(auth_header)

    # if not username or not password or not VALID_USERS.get(username) == password:
    #     await websocket.close(reason="Unauthorized")
    #     logging.info(f"Connection rejected due to invalid credentials: {websocket.remote_address}")
    #     return

    # TODO: confirm successful SIP registration

    # register SIP account
    account_queue.put((username, password))

    # register ws client
    connected_clients.add(websocket)
    logging.info(f"Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            logging.info(f"Received message from {websocket.remote_address}")

            json_message = json.loads(message)

            if json_message.get("signal", None) == "clear":
                logging.info("Received clear signal")
                audio_clear_signal[username] = True

            audio_data_base64 = json_message.get("data")
            if audio_data_base64:

                # decode
                audio_data = base64.b64decode(audio_data_base64)
                audio_data_len = len(audio_data)

                logging.info(f"Received audio data: {audio_data_len} bytes")

                # split into chunks 
                chunk_size = 640
                for i in range(0, audio_data_len, chunk_size):
                    chunk = audio_data[i:i + chunk_size]

                    # send to SIP account
                    if accounts.get(username):
                        received_audio_data[username].put(chunk)


    finally:
        # unregister ws client
        connected_clients.remove(websocket)
        logging.info(f"Client disconnected: {websocket.remote_address}")

        # unregister SIP account
        unregister_account_queue.put(username)


async def ws_main():
    global ws_loop
    ws_loop = asyncio.get_event_loop()

    server = await websockets.serve(websocket_handler, "0.0.0.0", WS_HOST_PORT)
    logging.info("WebSocket server started")

    await asyncio.Future() # wait forever



logging.info("Starting SIP client")

ep_cfg = pj.EpConfig()
ep_cfg.uaConfig.threadCnt = 0
ep_cfg.uaConfig.mainThreadOnly = False

ep = pj.Endpoint()
ep.libCreate()
ep.libInit(ep_cfg)
ep.audDevManager().setNullDev() # this machine has no audio device

sipTpConfig = pj.TransportConfig()
sipTpConfig.port = 12345
ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, sipTpConfig)
ep.libStart()


def create_account(sip_user, sip_password):
    account_cfg = pj.AccountConfig()
    account_cfg.idUri = f"sip:{sip_user}@{SIP_SERVER}"
    account_cfg.regConfig.registrarUri = f"sip:{SIP_SERVER}"
    account_cfg.sipConfig.authCreds.push_back(
        pj.AuthCredInfo("digest", "*", sip_user, 0, sip_password)
    )

    acc = Account()
    acc.create(account_cfg)
    acc.username = sip_user

    logging.info(f"Registered SIP account: {sip_user}")

    return acc


# run ws_main in new thread
ws_thread = threading.Thread(target=asyncio.run, args=(ws_main(),))
ws_thread.start()


while True:
    ep.libHandleEvents(10)

    for i in range(account_queue.qsize()):
        username, password = account_queue.get()
        acc = create_account(username, password)
        account_queue.task_done()
        received_audio_data[username] = queue.Queue()
        audio_clear_signal[username] = False
        accounts[username] = acc
    
    for i in range(unregister_account_queue.qsize()):
        username = unregister_account_queue.get()
        accounts[username].shutdown()
        old_accounts.add(accounts[username]) # retain it, so shutdown() finishes before garbage collection
        accounts[username] = None
        unregister_account_queue.task_done()
