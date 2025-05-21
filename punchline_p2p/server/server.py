#!/usr/bin/env python3
import logging
import traceback
import time
import sys
try:
    from punchline_p2p.punchline_server import PunchlineServer
except (ImportError, ModuleNotFoundError) as e:
            print(e)
            sys.exit(1)

def get_timestamp():
    timestamp = time.time()
    formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp)) + f",{int(timestamp % 1 * 1000):03d}"
    return formatted_time

# def log_if_logger(logger, message):
#     if logger is not None:
#         logger.info() logger.error()
#     else:
#         formatted_time = get_timestamp()
#         print(f"[{formatted_time}] {message}")

def main():
    # TODO maybe add counter if server crashes over 100* in 1 min or so end programm (probably error in code)
    # or maybe malicious behaviour of a client and add banned ip list to server somehow?
    # just like the timeout list if user constantly blocks a punchline by refreshing it every time connection is closed
    while True:
        try:
            s = PunchlineServer()
            formatted_time = get_timestamp()
            print(f"[{formatted_time}] Listening")
            s.run_server()
            formatted_time = get_timestamp()
            print(f"[{formatted_time}] Server ended unexpectedly without error.")
        except KeyboardInterrupt:
            formatted_time = get_timestamp()
            print(f"[{formatted_time}] KeyboardInterrupt detected exiting the loop.")
            break
        except Exception as e:  # this is here to keep the server running in an unexpected event
            formatted_time = get_timestamp()
            print(f"[{formatted_time}] An exception occurred: {e}")
            print("Stack trace:")
            traceback.print_exc()
    formatted_time = get_timestamp()
    print(f"[{formatted_time}] Server ended")

if __name__ == "__main__":
    main()
