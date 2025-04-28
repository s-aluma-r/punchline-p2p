#!/usr/bin/env python3
import logging
import traceback
import time
from punchline_p2p import PunchlineServer
if __name__ == "__main__":
    while True:
        try:
            s = PunchlineServer()
            print("Listening")
            s.run_server()
            print("Server ended")
        except KeyboardInterrupt:
            print("KeyboardInterrupt detected. Exiting the loop.")
            break
        except Exception as e:  # this is here to keep the server running in an unexpected event
            timestamp = time.time()
            formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp)) + f",{int(timestamp % 1 * 1000):03d}"

            print(f"[{formatted_time}] An exception occurred: {e}")
            print("Stack trace:")
            traceback.print_exc()
