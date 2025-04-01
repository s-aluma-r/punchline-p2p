#!/usr/bin/env python3

"""
limitations:
Here’s a breakdown:

    max Ethernet MTU: 1,500 bytes

    Subtract IPv6 header: -40 bytes
    (IPv4 would only be around 20 but its good to be safe)

    Subtract UDP header: -8 bytes

    Remaining for data: 1,452 bytes

    im using a 6 byte header: 1byte package type, 4 byte count, 1byte salt

    and 1018b data

    so i can use a 1024b buffer for socket (IPv6/4 and udp headers dont apply to this)
"""
"""
PSK:
send PSK package
get ack back
start using psk
"""

import threading
import socket
import json
import time
from enum import IntEnum
import struct
import random as r
import hashlib
# import base64

class PunchlineClient:
    # TODO maybe turn the json parts inneral serial parts and make it possible to change serialisation methods

    # TODO INFO IDEA: maybe have second out queue for faf packages that alternates with normal send queue to allow sending data while large ammount of data is transmitted?

    # constants
    _VERSION = 0  # 1 byte vanue for version
    _BUFFER_SIZE = 1024  # not more than 1500
    _HEADER = "B B I"  # header layout VERSION, TYPE, sequence_id
    _HEADER_IDX = {"VERSION": 0, "TYPE": 1, "SEQUENCE_ID": 2}
    _HEADER_SIZE = 0
    _MAX_PKG_DATA_SIZE = 0
    _KEEPALIVE_DELAY_S = 1
    _PKG_CHECK_ACK_DELAY_S = 0.00001
    _PKG_CHECK_ACK_TIMEOUT_DELAY_S = _KEEPALIVE_DELAY_S/4  # wait a good while before resend (is probably due to bigger issue)
    _SEND_QUEUE_EMPTY_CHECK_DEALY_S = 0.00001  # bigger than _PKG_CHECK_ACK_DELAY_S ? 
    _CONNECTION_TIMEOUT_S = 5
    _MAX_RESEND_TRIES = 10

    # data
    _stop_all_threads = False
    _UDP_client_socket = None
    _destination_address_port = None
    _last_recieved_ack_hash = 0
    _out_pkg_queue = []  # only for packages
    _connected_to_other_client = False
    _connecting = False
    _in_data_queue = []  # for user data
    _current_data_collection = None
    _current_data_collection_last_id = None  # has to be none because none triggers fresh start
    _recieve_thread = None
    _send_thread = None
    _code = None

    # statistics
    stat_resends = 0
    stat_failed_sends = 0
    stat_ping = 0

    class _PackageType(IntEnum):
        KAL = 0  # KeepALive                        | 
        DAT = 1  # DATa                             |
        JDT = 2  # Json Data                        | same as data only json
        FAF = 3  # Fire And Forget                  |
        JFF = 4  # Json Fire and Forget             | 
        ACK = 5  # ACKnowladge                      | 
        # KEY = 6  # KEY code for connect or encrypt  | 
        CON = 6  # CONnect to                       | this is used between server and client to send code to server and get ip/port of partner client back
        END = 7  # END connection                   |
        # TODO PSK --> switch to pre shared key (this needs to have some logic with back and forth to see if change in encryption has worked or not)

        # TODO add optional method to replace functions for python -> binary and back (default json but pickle or custom should work too) using function as parameter at init with dults being json ones

        #TODO add end package and also timeout feature (if not even keepalive comes)
    
    def __init__(self, PSK:str = None, dedicated_server=None):
        self._HEADER_SIZE = struct.calcsize(self._HEADER)
        self._MAX_PKG_DATA_SIZE = self._BUFFER_SIZE-self._HEADER_SIZE
        self._UDP_client_socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        # start send thread
        # start recieve thread

    def connect_async(self, code:bytes):
        # check code size (needs to fit in single data pkg)
        if len(code) > self._MAX_PKG_DATA_SIZE:
            raise ValueError(f"code can max be len: {self._MAX_PKG_DATA_SIZE}")

        if self._connected_to_other_client or self._connecting:
            return False

        self._connecting = True

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
        """TODO"""
        return ("127.0.0.1", 12345)
        # a = ("domain", 12345)
        # b = (socket.gethostbyname(a[0]), a[1])
        # return b

    def _hash(self, pkg: bytes):
        hash_object = hashlib.sha256()
        hash_object.update(pkg)
        return hash_object.digest()
        

    def _create_pkg(self, pkg_type: _PackageType, data: bytes = b'\x00', sequence_id: int = 0):
        if not (0 <= self._VERSION < 256):
            raise ValueError("_VERSION must be an unsigned 1-byte integer (0 to 255).")
        if not (0 <= pkg_type < 256):
            raise ValueError("pkg_type must be an unsigned 1-byte integer (0 to 255).")
        if not (0 <= sequence_id <= 4_294_967_295):
            raise ValueError("sequence_id must be an unsigned 4-byte integer (0 to 4,294,967,295).")
        if not (0 <= len(data) <= self._MAX_PKG_DATA_SIZE):
            raise ValueError(f"data must be bytes with max len={self._MAX_PKG_DATA_SIZE}.")

        header = struct.pack(self._HEADER, self._VERSION, pkg_type, sequence_id)
        
        return header+data
        
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

    def _send_pkg(self, pkg: bytes):
        header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
        pkg_type = header[self._HEADER_IDX["TYPE"]]

        # print(f"<DBG> sending", pkg)
        
        self._UDP_client_socket.sendto(pkg, self._destination_address_port)

        if pkg_type not in [self._PackageType.FAF, self._PackageType.JFF, self._PackageType.KAL, self._PackageType.ACK]:
            pkg_hash = self._hash(pkg)
            timeout = 0
            resends = 0
            while pkg_hash != self._last_recieved_ack_hash and resends < self._MAX_RESEND_TRIES:  # TODO make this function of time instead of max resend tries and maybe end connection if it runs out
                if timeout >= self._PKG_CHECK_ACK_TIMEOUT_DELAY_S:
                    self._UDP_client_socket.sendto(pkg, self._destination_address_port)  # resend pkg
                    timeout = 0
                    resends += 1
                    self.stat_resends += 1
                else:
                    time.sleep(self._PKG_CHECK_ACK_DELAY_S)
                    timeout += self._PKG_CHECK_ACK_DELAY_S

            self.stat_ping = (timeout + self._PKG_CHECK_ACK_TIMEOUT_DELAY_S * resends)*1000

            if resends >= self._MAX_RESEND_TRIES:
                self.stat_failed_sends += 1

    def _send_pkg_thread_func(self):
        last_keepalive_time = 0
        while not self._stop_all_threads:
            if len(self._out_pkg_queue) > 0:
                pkg = self._out_pkg_queue.pop(0)
                self._send_pkg(pkg)
            else:
                # check if its time for a keepalive
                now = time.time()
                keepalive_delay = now - last_keepalive_time
                if keepalive_delay > self._KEEPALIVE_DELAY_S:
                    # send keepalive
                    self._send_pkg(self._create_pkg(self._PackageType.KAL))
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

    def _handle_recieve_pkg(self, pkg):
        header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
        data = pkg[self._HEADER_SIZE:]
        pkg_version = header[self._HEADER_IDX["VERSION"]]
        if pkg_version != self._VERSION:
            raise RuntimeError(f"Recieved package version:{pkg_version} != Client version")
        pkg_type = header[self._HEADER_IDX["TYPE"]]
        pkg_sequence_id = header[self._HEADER_IDX["SEQUENCE_ID"]]

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
        elif pkg_type == self._PackageType.ACK:
            # take data and place it into last recieved ack hash
            self._last_recieved_ack_hash = data
        # ________________________ everything below returns ack _________________________
        elif pkg_type == self._PackageType.DAT:   # TODO duplicated code
            # collect, acknowledge (not with out queue), and if id 0 add to in queue
            self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)))

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
            self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)))

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
            self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)))
            if not self._connected_to_other_client:

                ip_binary = data[:4]  # First 4 bytes for IP
                port_binary = data[4:]  # Last 2 bytes for port

                # Convert back to human-readable formats
                ip_address = socket.inet_ntoa(ip_binary)  # Binary to IPv4 string
                port = struct.unpack('!H', port_binary)[0]  # Unpack 2 bytes as big-endian integer

                # Resulting tuple
                address_port = (ip_address, port)

                print("<DBG> updating address to:", address_port)
                self._destination_address_port = address_port
                self._connected_to_other_client = True
                # directly send some kind of pkg (maybe empty CON package because its also expected to be acked and therefore repeated) to other client so there is no need to wait for a keepalive?
                # self._send_pkg(self._create_pkg(self._PackageType.CON))
                # FIXME this doesnt work for some reason (investigate)
                # only keepalive works but need to fix server first
                # self._send_pkg(self._create_pkg(self._PackageType.KAL))
                # (feature probably not needed)

        elif pkg_type == self._PackageType.END:
            self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)))
            self.disconnect()

    def _recieve_pkg_thread_func(self):
        connection_timeout = 0  # TODO implement this with recvform itself somehow
        while not self._stop_all_threads:
            try:
                bytes_address_pair = self._UDP_client_socket.recvfrom(self._BUFFER_SIZE)

                pkg = bytes_address_pair[0]
                pkg_origin_address_port = bytes_address_pair[1]

                if pkg_origin_address_port != self._destination_address_port:
                    raise RuntimeWarning(f"Unexpected sender address/port: {pkg_origin_address_port}")
                elif self._connected_to_other_client and self._connecting:
                    self._connecting = False

                self._handle_recieve_pkg(pkg)
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
        self._connected_to_other_client = False
        # TODO maybe wait for ack if end and only stop after


import os
TARGET_FOLDER = "/tmp/receive/"
CONNECTION_CODE = None
cc = None
def main():
    global cc
    cc = PunchlineClient()
    print("connecting...")
    if CONNECTION_CODE:
        code = CONNECTION_CODE
    else:
        code = input("Enter connection code: ")
    cc.connect_async(code.encode(encoding='utf-8'))
    while not cc.is_connected():
        time.sleep(0.01)
    print(f"Connected to: {cc._destination_address_port}")
        
    def rec_printer():
        while True:
            r = cc.recieve()
            if r:
                if "#send:" in r:
                    # print(time.time())
                    parts = r.split(":")
                    print(f"Would you like to save file: {parts[1]}? (if no y/n shows up press enter)")
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
                    # print(time.time())
                else:
                    print("RECIEVED MESSAGE: ", r)
            time.sleep(0.1)

    rec_printer_thread = threading.Thread(target=rec_printer, daemon=True)
    rec_printer_thread.start()

    while True:
        i = input()  # remember this can only take 4kb of input in shell
        if i != "":
            if "#send:" in i:
                parts = i.split(":")
                print(parts)
                path = parts[1]
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
                # print(f"<DBG>: {i}")
                cc.send(i)
                print(f"ping: {cc.stat_ping}")

import sys
def send_rec_main():
    global cc
    cc = PunchlineClient()
    print("connecting...")
    if CONNECTION_CODE:
        code = CONNECTION_CODE
    else:
        code = input("Enter connection code: ")
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
        while True:
            r = cc.recieve()
            if r:
                if "#send:" in r:
                    # print(time.time())
                    parts = r.split(":")
                    print(f"Would you like to save file: {parts[1]}? (if no y/n shows up press enter)")
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
# TODO maybe instead of _connected_to_other_client have updated_address and connected
#    updated_address is true after server sends new address
#    connected to other client is true if recieved first package from other client?
