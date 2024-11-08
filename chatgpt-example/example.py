import asyncio
import websockets
import json
import base64
import sounddevice as sd
import io
from pydub import AudioSegment
import queue


SIPLE_URI = "ws://siple.ostwilkens.se:3000" # replace!
OPENAI_API_KEY = "sk-123abc123abc123abc123abc123abc12" # replace!
SIP_USERNAME = "4600123456" # replace!
SIP_PASSWORD = "123ABC123ABC123ABC123ABC123ABC12" # replace!

event_queue = queue.Queue()
oai_message_queue = queue.Queue()


def audio_to_item_create_event(audio_segment: AudioSegment) -> str:
    # resample to 24kHz mono pcm16
    pcm_audio = audio_segment.set_frame_rate(24000).set_channels(1).set_sample_width(2).raw_data
    
    # encode to base64 string
    pcm_base64 = base64.b64encode(pcm_audio).decode()
    
    event = {
        "type": "input_audio_buffer.append",
        "audio": pcm_base64
    }
    return json.dumps(event)


async def siple_send_events(websocket):
    while True:

        if not oai_message_queue.empty():
            oai_message = oai_message_queue.get()
            if oai_message["type"] == "response.audio.delta":
                base64_audio = oai_message["delta"]
                decoded_audio = base64.b64decode(base64_audio)
                pcm_data = io.BytesIO(decoded_audio)
                audio = AudioSegment.from_raw(pcm_data, sample_width=2, frame_rate=24000, channels=1)
                # resample to 16khz
                audio = audio.set_frame_rate(16000)
                pcm_data = audio.raw_data
                pcm_base64 = base64.b64encode(pcm_data).decode()
                event = {
                    "to": "todo",
                    "data": pcm_base64
                }
                await websocket.send(json.dumps(event))
            elif oai_message["type"] == "input_audio_buffer.speech_started":
                # print(oai_message)
                # ai was interrupted. send a message to clear the queue
                print("!")
                event = {
                    "to": "todo",
                    "signal": "clear",
                }
                await websocket.send(json.dumps(event))
            else:
                pass
                # print(oai_message["type"])
        else:
            await asyncio.sleep(0.01)
        


async def siple_receive_messages(websocket):

    batch_segments = []

    wav_segments = []
    file_i = 0

    while True:
        message = await websocket.recv()
        message_obj = json.loads(message)
        from_number = message_obj["from"]
        base64_audio = message_obj["data"]
        decoded_audio = base64.b64decode(base64_audio)
        # audio_data = np.frombuffer(decoded_audio, dtype="int16")
        # stream.write(audio_data)
        pcm_data = io.BytesIO(decoded_audio)

        # load 16khz bytes mono as AudioSegment
        audio = AudioSegment.from_raw(pcm_data, sample_width=2, frame_rate=16000, channels=1)

        # resample to 24khz
        audio = audio.set_frame_rate(24000)
        
        # # debug wav save
        # wav_segments.append(audio)
        # if len(wav_segments) > 500:
        #     combined = wav_segments.pop(0)
        #     for segment in wav_segments:
        #         combined += segment
        #     combined.export(f"combined_{file_i}.wav", format="wav")
        #     wav_segments = []
        #     file_i += 1

        batch_segments.append(audio)
        if len(batch_segments) > 20:
            combined = batch_segments.pop(0)
            for segment in batch_segments:
                combined += segment
            event = audio_to_item_create_event(combined)
            event_queue.put(event)
            batch_segments = []

        # # send resampled audio to stream
        # stream.write(audio_data)

        # event = audio_to_item_create_event(audio)

        # event_queue.put(event)


async def oai_send_events(websocket):
    while True:
        if not event_queue.empty():
            event = event_queue.get()
            await websocket.send(event)
        else:
            await asyncio.sleep(0.01)


async def oai_receive_messages(websocket):
    while True:
        message = await websocket.recv()

        # print response message
        message_obj = json.loads(message)
        
        oai_message_queue.put(message_obj)


async def listen_openai():
    uri = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    async with websockets.connect(uri, extra_headers=headers) as websocket:
        print("Connected to the OpenAI server")

        try:
            await asyncio.gather(
                oai_send_events(websocket),
                oai_receive_messages(websocket)
            )

        except Exception as e:
            print(f"Exception: {e}")


async def listen_siple():
    credentials = f"{SIP_USERNAME}:{SIP_PASSWORD}".encode('utf-8')
    encoded_credentials = base64.b64encode(credentials).decode('utf-8')
    headers = {
        "Authorization": f"Basic {encoded_credentials}"
    }

    async with websockets.connect(SIPLE_URI, extra_headers=headers) as websocket:
        print("Connected to the siple server")
        
        stream = sd.OutputStream(samplerate=24000, channels=1, dtype="int16")
        stream.start()
        
        await asyncio.gather(
            siple_send_events(websocket),
            siple_receive_messages(websocket)
        )


if __name__ == "__main__":
    async def main():
        # run both coroutines concurrently
        await asyncio.gather(listen_siple(), listen_openai())

    # run the main coroutine
    asyncio.run(main())

