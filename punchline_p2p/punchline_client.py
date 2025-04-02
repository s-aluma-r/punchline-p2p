#!/usr/bin/env python3
import threading
import socket
import json
import time
import struct
import random as r
import requests
# import base64
# from punchline_p2p import Punchline
from punchline_p2p.punchline import Punchline

"""
PSK:
send PSK package
get ack back
start using psk
"""

"""
for send/rec queue use:

from queue import Queue

shared_queue = Queue()

def append_to_list(item):
    shared_queue.put(item)  # Adds item to the queue

def remove_from_list(item):
    temp_list = list(shared_queue.queue)
    if item in temp_list:
        temp_list.remove(item)
        with shared_queue.mutex:  # Modify queue directly in a thread-safe way
            shared_queue.queue.clear()
            shared_queue.queue.extend(temp_list)

"""


# TODO fix spelling recieve -> receive

class PunchlineClient(Punchline):
    # IDEA maybe turn the json parts inneral serial parts and make it possible to change serialisation methods

    # IDEA: maybe have second out queue for faf packages that alternates with normal send queue to allow sending data while large ammount of data is transmitted?

    # constant
    _DEDICATED_SERVER = None
    
    # data
    _stop_all_threads = False
    _destination_address_port = None
    _out_pkg_queue = []  # only for packages  # TODO make this a queue
    _connected_to_other_client = False
    _connecting = False
    _in_data_queue = []  # for user data  # TODO make this a queue
    _current_data_collection = None
    _current_data_collection_last_id = None  # has to be none because none triggers fresh start
    _recieve_thread = None
    _send_thread = None
    _code = None
    

        # IDEA add optional method to replace functions for python -> binary and back (default json but pickle or custom should work too) using function as parameter at init with dults being json ones
    
    def __init__(self, PSK:str = None, dedicated_server=None, debug=False):
        super().__init__(debug)
        self._DEDICATED_SERVER = dedicated_server

    def connect_async(self, code:bytes):
        # check code size (needs to fit in single data pkg)
        if len(code) > self._MAX_PKG_DATA_SIZE:
            raise ValueError(f"code can max be len: {self._MAX_PKG_DATA_SIZE}")

        if self._connected_to_other_client or self._connecting:
            return False

        self._connecting = True
        self._timed_out = False

        self._destination_address_port = self._get_semi_random_server(code)

        self._code = code

        self._recieve_thread = threading.Thread(target=self._recieve_pkg_thread_func, daemon=True)
        self._recieve_thread.start()

        # start sending thread
        self._send_thread = threading.Thread(target=self._send_pkg_thread_func, daemon=True)
        self._send_thread.start()

        self._out_pkg_queue.append(self._create_pkg(self._PackageType.CON, code))
        return True
        
    def _get_semi_random_server(self, code):
        if self._DEDICATED_SERVER:
            resolved = (socket.gethostbyname(self._DEDICATED_SERVER[0]), self._DEDICATED_SERVER[1])
            return resolved
        response = requests.get('https://raw.githubusercontent.com/s-aluma-r/punchline-p2p/refs/heads/main/active_servers.json', timeout=10)  # TODO catch requests.exceptions.Timeout
        if response.status_code == 200:
            versions = json.loads(response.content.decode())
            servers = versions[f"V{self._VERSION}"]
            rand = r.Random()
            rand.seed(code)  # TODO is this "safe" to do or can it crash
            server_pos = r.randint(0, len(servers)-1)
            server = servers[server_pos]
            ip = socket.gethostbyname(server["ip"])
            port = server["port"]
            return (ip, port)
        else:
            return None  # TODO catch this and make it not crash
        
    def _append_data_packages(self, data: bytes, is_json: bool = False):
        if is_json:
            pkg_type = self._PackageType.JDT
        else:
            pkg_type = self._PackageType.DAT
        
        # Define the chunk size
        chunk_size = self._BUFFER_SIZE-struct.calcsize(self._HEADER)

        # Split the binary data into chunks
        chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
        # print(f"<DBG> {chunks=}")

        for i, chunk in enumerate(chunks):  # iterate backwards for sequence id (0 means last package) [::-1] NO, inormally but re index
            s_id = (len(chunks)-1)-i
            # rint(f"<DBG> appending: {s_id=}, {chunk=}")
            self._out_pkg_queue.append(self._create_pkg(pkg_type, data=chunk, sequence_id=s_id))

    def _send_pkg_thread_func(self):
        last_keepalive_time = 0
        while not self._stop_all_threads:
            if len(self._out_pkg_queue) > 0:
                pkg = self._out_pkg_queue.pop(0)
                self._send_pkg(pkg, self._destination_address_port)
            else:
                # check if its time for a keepalive
                now = time.time()
                keepalive_delay = now - last_keepalive_time
                if keepalive_delay > self._KEEPALIVE_DELAY_S:
                    # send keepalive
                    self._send_pkg(self._create_pkg(self._PackageType.KAL), self._destination_address_port)
                    last_keepalive_time = now
                # wait a little
                time.sleep(self._SEND_QUEUE_EMPTY_CHECK_DEALY_S)

    def send(self, data, fire_and_forget=False):
        if not self._connected_to_other_client or self._connecting:
            return False
        # print(f"<DBG> data: {data}")
        bin_data = None
        if not isinstance(data, bytes):
            try:
                json_data = json.dumps(data)
                # print(f"<DBG> JSON: {json_data}")
                bin_data = json_data.encode(encoding='utf-8')  # Convert to JSON string, then to binary
                # print(f"<DBG> JSON_BIN: {bin_data}")
                is_json = True
            except (TypeError, ValueError) as e:
                raise ValueError("Invalid data: Not JSON Serialisable") from e
        else:
            is_json = False
            bin_data = data

        if fire_and_forget:
            if len(data) > self._MAX_PKG_DATA_SIZE:
                raise ValueError("Invalid data: Data too big for fire_and_forget (single package)")
            else:
                if is_json:
                    faf_type = self._PackageType.JFF
                else:
                    faf_type = self._PackageType.FAF
                self._out_pkg_queue.append(self._create_pkg(faf_type, bin_data))
        else:
            self._append_data_packages(bin_data, is_json=is_json)
        return True

    def _handle_recieve_pkg(self, pkg, sender):
        package_parts = super()._handle_received_pkg(pkg, sender)
        if package_parts:
            pkg_version, pkg_type, pkg_sequence_id, data = package_parts
        else:
            return  # same package was sent twice by mistake

        # print(f"REC:{pkg_version=}\t{pkg_type=}\t{pkg_sequence_id}\t{data=}")

        if pkg_type == self._PackageType.KAL:
            # print("<DBG> KAL")
            pass  # currently don't ack just ignore
        elif pkg_type == self._PackageType.FAF:
            # add to in queue
            self._in_data_queue.append(data)
        elif pkg_type == self._PackageType.JFF:
            # json decode add to in queue
            j = data.decode()
            o = json.loads(j)
            self._in_data_queue.append(o)
        # ________________________ everything below returns ack _________________________
        elif pkg_type == self._PackageType.DAT:   # TODO duplicated code
            # collect, acknowledge (not with out queue), and if id 0 add to in queue

            if not self._current_data_collection_last_id:  # last transmission was done so start new one
                self._current_data_collection = [None for i in range(pkg_sequence_id+1)]
                self._current_data_collection_last_id = pkg_sequence_id
            
            if pkg_sequence_id <= self._current_data_collection_last_id:  # data continues so place in right spot or rewrite spot
                self._current_data_collection[pkg_sequence_id] = data
                self._current_data_collection_last_id = pkg_sequence_id
            else:  # new start of data so reset everything (should only happen in error case?)
                self._current_data_collection = [None for i in range(pkg_sequence_id+1)]
                self._current_data_collection[pkg_sequence_id] = data
                self._current_data_collection_last_id = pkg_sequence_id
                    
            if pkg_sequence_id == 0:
                # assemble collected data packages
                # print("<DBG> ASSEMBLING DATA")
                full_data = b''.join(self._current_data_collection[::-1])
                # full_data = b''
                # for chunk in self._current_data_collection[::-1]:  # opposite way because highest sequence id comes first
                #     full_data += chunk
                # print("<DBG> ASSEMBLED")
                self._current_data_collection = None
                self._in_data_queue.append(full_data)
                pkg_sequence_id = None  # set end of transmission
            
        elif pkg_type == self._PackageType.JDT:  # TODO duplicated code
            # collect, acknowledge (not with out queue), and if id 0 decode and add to in queue

            if not self._current_data_collection_last_id:  # last transmission was done so start new one
                self._current_data_collection = [None for i in range(pkg_sequence_id+1)]
                self._current_data_collection_last_id = pkg_sequence_id
            
            if pkg_sequence_id <= self._current_data_collection_last_id:  # data continues so place in right spot or rewrite spot
                self._current_data_collection[pkg_sequence_id] = data
                self._current_data_collection_last_id = pkg_sequence_id
            else:  # new start of data so reset everything (should only happen in error case?)
                self._current_data_collection = [None for i in range(pkg_sequence_id+1)]
                self._current_data_collection[pkg_sequence_id] = data
                self._current_data_collection_last_id = pkg_sequence_id
                
            if pkg_sequence_id == 0:
                # assemble collected data packages
                full_data = b''
                # print("<DBG> ASSEMBLING")
                for chunk in self._current_data_collection[::-1]:
                    # print(f"<DBG> {chunk}", end="+", flush=True)
                    full_data += chunk
                # print()
                self._current_data_collection = None
                j = full_data.decode()  # INFO only difference
                # print("<DBG> RESULT JSON:", j)
                o = json.loads(j)  # INFO only difference
                # print("<DBG> RESULT:", o)
                self._in_data_queue.append(o)  # INFO only difference
                pkg_sequence_id = None  # set end of transmission
                
        elif pkg_type == self._PackageType.CON:
            # check if not connected to client, if so connect to this new address
            if not self._connected_to_other_client:

                address_port = self._binary_to_address_port(data)

                print("<DBG> updating address to:", address_port)
                self._destination_address_port = address_port
                self._connected_to_other_client = True

                # INFO this is in progress due to restructure of server and client
                # directly send some kind of pkg (maybe empty CON package because its also expected to be acked and therefore repeated) to other client so there is no need to wait for a keepalive?
                # self._send_pkg(self._create_pkg(self._PackageType.CON))
                # FIXME this doesnt work for some reason (investigate)
                # only keepalive works but need to fix server first
                # self._send_pkg(self._create_pkg(self._PackageType.KAL))
                # (feature probably not needed)

        elif pkg_type == self._PackageType.END:
            self.disconnect()

    def _recieve_pkg_thread_func(self):
        # IDEA implement timeout with recvform itself somehow (if no pkg longer than a few times keepalive delay = timeout?)
        while not self._stop_all_threads:
            try:
                pkg, pkg_origin_address_port = self._UDP_socket.recvfrom(self._BUFFER_SIZE)

                if pkg_origin_address_port != self._destination_address_port:
                    raise RuntimeWarning(f"WARN: Unexpected sender address/port: {pkg_origin_address_port}")
                else:
                    if self._connected_to_other_client and self._connecting:
                        self._connecting = False

                    self._handle_recieve_pkg(pkg, pkg_origin_address_port)
            except RuntimeWarning as w:
                print(w)
            except Exception as e:
                raise e
                # TODO END CONNECTION SOMETHING WENT WRONG (probably only recieving connection errors)

    def recieve(self):
        if len(self._in_data_queue) == 0:
            return None
        else:
            return self._in_data_queue.pop(0)

    def is_connected(self):
        return self._connected_to_other_client and not self._connecting

    def get_send_progress(self):
        """goes down to 0 to show progress"""
        return len(self._out_pkg_queue)
    def get_rec_progress(self):
        """goes down to 0 to show progress"""
        return self._current_data_collection_last_id

    def disconnect(self):
        self._out_pkg_queue.append(self._create_pkg(self._PackageType.END))
        self._stop_all_threads = True
        if self._connecting and not self._connected_to_other_client:
            raise TimeoutError()
        self._connecting = False
        self._connected_to_other_client = False
        # IDEA maybe wait for ack if end and only stop after


import os
TARGET_FOLDER = "/tmp/receive/"
CONNECTION_CODE = "BADUM_TZZZ"
cc = None
# def main():
#     global cc
#     cc = PunchlineClient()
    
#     if CONNECTION_CODE:
#         code = CONNECTION_CODE
#     else:
#         code = input("Enter connection code: ")

#     print("connecting...")
#     cc.connect_async(code.encode(encoding='utf-8'))
#     while not cc.is_connected():
#         time.sleep(0.01)
#     print(f"Connected to: {cc._destination_address_port}")
        
#     def rec_printer():
#         while True:
#             r = cc.recieve()
#             if r:
#                 if "#send:" in r:
#                     # print(time.time())
#                     parts = r.split(":")
#                     print(f"Would you like to save file: {parts[1]}? (if no y/n shows up press enter)")
#                     save_file = (input("(y/n): ") == "y")
#                     if save_file:
#                         print("Recieving file:")
#                     else:
#                         print("Ignoring file being sent:")
#                     filename = parts[1]
#                     while True:
#                         data = cc.recieve()
#                         print(f"{cc.get_rec_progress()}          ", end="\r", flush=True)
#                         if data:
#                             break
#                         time.sleep(0.1)
#                     if save_file:
#                         path = TARGET_FOLDER + filename
#                         with open(path, "wb") as file:
#                             file.write(data)
#                         print(f"Recieved file: {parts[1]}")
#                     else:
#                         print(f"Ignored file: {parts[1]}")
#                     # print(time.time())
#                 else:
#                     print("RECIEVED MESSAGE: ", r)
#             time.sleep(0.1)

#     rec_printer_thread = threading.Thread(target=rec_printer, daemon=True)
#     rec_printer_thread.start()

#     while True:
#         i = input()  # remember this can only take 4kb of input in shell
#         if i != "":
#             if "#send:" in i:
#                 parts = i.split(":")
#                 print(parts)
#                 path = parts[1]
#                 if os.path.isfile(path):
#                     with open(path, "rb") as file:
#                         data = file.read()
#                         filename = os.path.basename(path)
#                         cc.send("#send:"+filename)
#                         cc.send(data)
#                     print("waiting for transmit to be over...")
#                     while cc.get_send_progress() > 0:
#                         time.sleep(0.1)
#                         print(f"{cc.get_send_progress()}          ", end="\r", flush=True)
#                     print("Done")
#             else:
#                 # print(f"<DBG>: {i}")
#                 cc.send(i)
#                 print(f"ping: {cc.stat_ping}")

import sys
def send_rec_main():
    global cc
    cc = PunchlineClient(dedicated_server=('localhost', 12345))
    if CONNECTION_CODE:
        code = CONNECTION_CODE
    else:
        code = input("Enter connection code: ")
    print("connecting...")
    
    cc.connect_async(code.encode(encoding='utf-8'))
    
    while not cc.is_connected():
        time.sleep(0.01)
        
    print(f"Connected to: {cc._destination_address_port}")
    
    if len(sys.argv) == 2:
        # send
        path = sys.argv[1]
        if os.path.isfile(path):
            with open(path, "rb") as file:
                data = file.read()
                filename = os.path.basename(path)
                cc.send("#send:"+filename)
                cc.send(data)
            print("waiting for transmit to be over...")
            while cc.get_send_progress() > 0:
                time.sleep(0.1)
                print(f"{cc.get_send_progress()}          ", end="\r", flush=True)
            print("Done")
        else:
            print(f"{path} is not a file")
    else:
        if not os.path.isdir(TARGET_FOLDER):
            os.mkdir(TARGET_FOLDER)
        while cc.is_connected():
            r = cc.recieve()
            if r:
                if "#send:" in r:
                    # print(time.time())
                    parts = r.split(":")
                    print(f"Would you like to save file: {parts[1]}?")
                    save_file = (input("(y/n): ") == "y")
                    if save_file:
                        print("Recieving file:")
                    else:
                        print("Ignoring file being sent:")
                    filename = parts[1]
                    while True:
                        data = cc.recieve()
                        print(f"{cc.get_rec_progress()}          ", end="\r", flush=True)
                        if data:
                            break
                        time.sleep(0.1)
                    if save_file:
                        path = TARGET_FOLDER + filename
                        with open(path, "wb") as file:
                            file.write(data)
                        print(f"Recieved file: {parts[1]}")
                    else:
                        print(f"Ignored file: {parts[1]}")
                    break
                else:
                    print("RECIEVED MESSAGE: ", r)
                    break
            else:
                time.sleep(0.1)

if __name__ == "__main__":
    try:
        send_rec_main()
    except (Exception, KeyboardInterrupt) as e:
        print(f"\n {cc.stat_ping=}, {cc.stat_resends=}, {cc.stat_failed_sends=}, {len(cc._in_data_queue)=}, {len(cc._out_pkg_queue)=}", end="")
        if cc._current_data_collection:
            print(f"{len(cc._current_data_collection)=}")
        else:
            print()
        raise e
