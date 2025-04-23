#!/usr/bin/env python3

import os
import sys
import time
import logging
from punchline_p2p import PunchlineClient
TARGET_FOLDER = "/tmp/receive/"
CONNECTION_PUNCHLINE = None
pc = None
def send_rec_main():
    global pc
    if len(sys.argv) == 2:
        print("Sending mode.")
    else:
        print("Receiving mode. (for sending mode call ./send_receive.py [path to file])") 
        
    pc = PunchlineClient(logging_level=logging.INFO)
    # pc = PunchlineClient(dedicated_server=('localhost', 12345))  # to test locally with own server
    if CONNECTION_PUNCHLINE:
        code = CONNECTION_PUNCHLINE
    else:
        code = input("Enter punchline: ")
    print("connecting...")
    
    pc.connect_async(code.encode(encoding='utf-8'))
    
    while not pc.is_connected():
        time.sleep(0.01)
        
    print(f"Connected to: {pc._destination_address_port}")
    
    if len(sys.argv) == 2:
        # send
        path = sys.argv[1]
        if os.path.isfile(path):
            with open(path, "rb") as file:
                data = file.read()
                filename = os.path.basename(path)
                pc.send("#send:"+filename)
                pc.send(data)
            print("waiting for transmit to be over...")
            while pc.get_send_progress() > 0:
                time.sleep(0.1)
                print(f"{pc.get_send_progress()}          ", end="\r", flush=True)
            print("Done")
        else:
            print(f"{path} is not a file")
    else:
        if not os.path.isdir(TARGET_FOLDER):
            os.mkdir(TARGET_FOLDER)
        while pc.is_connected():
            r = pc.receive()
            if r:
                if "#send:" in r:
                    # print(time.time())
                    parts = r.split(":")
                    print(f"Would you like to save file: {parts[1]}?")
                    save_file = (input("(y/n): ") == "y")
                    if save_file:
                        print("Receiving file:")
                    else:
                        print("Ignoring file being sent:")
                    filename = parts[1]
                    while True:
                        data = pc.receive()
                        print(f"{pc.get_rec_progress()}          ", end="\r", flush=True)
                        if data:
                            break
                        time.sleep(0.1)
                    if save_file:
                        path = TARGET_FOLDER + filename
                        with open(path, "wb") as file:
                            file.write(data)
                        print(f"Received file: {parts[1]}")
                    else:
                        print(f"Ignored file: {parts[1]}")
                    break
                else:
                    print("RECEIVED MESSAGE: ", r)
                    break
            else:
                time.sleep(0.1)
    pc.disconnect()

if __name__ == "__main__":
    try:
        send_rec_main()
    except (Exception, KeyboardInterrupt) as e:
        print(f"{pc.disconnect()=}")
        raise e
    finally:
        print(f"\n {pc.stat_ping=}, {pc.stat_resends=}, {pc._in_data_queue.qsize()=}, {pc._out_pkg_queue.qsize()=}", end="")
        if pc._current_data_collection:
            print(f"{len(pc._current_data_collection)=}")
        else:
            print()
