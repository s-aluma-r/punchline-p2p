import threading
import socket
import json
import time
import struct
import random as r
import requests
from queue import Queue
import logging
# import base64
# from punchline_p2p import Punchline
from punchline_p2p.punchline import Punchline

# IDEA: encrypt data before deviding into packages (this way resends etc dont matter) dont encrypt faf packages or acks

class PunchlineClient(Punchline):
    # IDEA maybe turn the json parts inneral serial parts and make it possible to change serialisation methods

    # IDEA: maybe have second out queue for faf packages that alternates with normal send queue to allow sending data while large ammount of data is transmitted?

    # constant
    _DEDICATED_SERVER = None
    
    # data
    _stop_all_threads = False
    _destination_address_port = None
    _out_pkg_queue = Queue()  # only for packages
    _connected_to_other_client = False
    _connecting = False
    _in_data_queue = Queue()  # for user data
    _current_data_collection = None
    _current_data_collection_last_id = None  # has to be none because none triggers fresh start
    _receive_thread = None
    _send_thread = None
    _code = None
    

        # IDEA add optional method to replace functions for python -> binary and back (default json but pickle or custom should work too) using function as parameter at init with dults being json ones
    
    def __init__(self, PSK: str = None, dedicated_server=None, logging_level=logging.INFO):
        super().__init__(logging_level)
        self._DEDICATED_SERVER = dedicated_server

    def _send_pkg(self, pkg: bytes, destination_address_port):
        try:
            super()._send_pkg(pkg, destination_address_port)
        except TimeoutError as e:
            self._end_connection()
            raise e

    def connect_async(self, code: bytes):
        # check code size (needs to fit in single data pkg)
        if len(code) > self._MAX_PKG_DATA_SIZE:
            raise ValueError(f"code can max be len: {self._MAX_PKG_DATA_SIZE}")

        if self._connected_to_other_client or self._connecting:
            return False

        self._connecting = True
        self._timed_out = False

        self._destination_address_port = self._get_semi_random_server(code)

        self._code = code

        self._receive_thread = threading.Thread(target=self._receive_pkg_thread_func, daemon=True)
        self._receive_thread.start()

        # start sending thread
        self._send_thread = threading.Thread(target=self._send_pkg_thread_func, daemon=True)
        self._send_thread.start()

        self._out_pkg_queue.put(self._create_pkg(self._PackageType.CON, code))
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
            self._out_pkg_queue.put(self._create_pkg(pkg_type, data=chunk, sequence_id=s_id))

    def _send_pkg_thread_func(self):
        last_keepalive_time = 0
        while not self._stop_all_threads:
            if not self._out_pkg_queue.empty():
                pkg = self._out_pkg_queue.get()
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
                self._out_pkg_queue.put(self._create_pkg(faf_type, bin_data))
        else:
            self._append_data_packages(bin_data, is_json=is_json)
        return True

    def _collect_multi_package_data(self, data, pkg_sequence_id):
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
            pkg_sequence_id = None  # set end of transmission
            return full_data
        return None

    def _handle_received_pkg(self, pkg, sender):
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
            self._in_data_queue.put(data)
        elif pkg_type == self._PackageType.JFF:
            # json decode add to in queue
            j = data.decode()
            o = json.loads(j)
            self._in_data_queue.put(o)
        # ________________________ everything below returns ack _________________________
        elif pkg_type == self._PackageType.DAT:
           full_data = self._collect_multi_package_data(data, pkg_sequence_id)
           if full_data:
               self._in_data_queue.put(full_data)
            
        elif pkg_type == self._PackageType.JDT:
            full_data = self._collect_multi_package_data(data, pkg_sequence_id)
            if full_data:
                j = full_data.decode()
                # print("<DBG> RESULT JSON:", j)
                o = json.loads(j)
                # print("<DBG> RESULT:", o)
                self._in_data_queue.put(o)
                
        elif pkg_type == self._PackageType.CON:
            # check if not connected to client, if so connect to this new address
            if not self._connected_to_other_client:

                address_port = self._binary_to_address_port(data)

                self._LOGGER.info("<UPDATING ADDRESS> %s --> %s", self._destination_address_port, address_port)
                
                self._destination_address_port = address_port
                self._connected_to_other_client = True

                # initial keepalive to kickstart connection (no need to wait for keepalive interval)
                self._send_pkg(self._create_pkg(self._PackageType.KAL), self._destination_address_port)

        elif pkg_type == self._PackageType.END:
            self._end_connection()

    def _receive_pkg_thread_func(self):
        # IDEA implement timeout with recvform itself somehow (if no pkg longer than a few times keepalive delay = timeout?)
        while not self._stop_all_threads:

            pkg, pkg_origin_address_port = self._UDP_socket.recvfrom(self._BUFFER_SIZE)

            if pkg_origin_address_port != self._destination_address_port:
                self._LOGGER.warning("<Unexpected sender address/port> %s", pkg_origin_address_port)
                raise RuntimeWarning(f"WARN: Unexpected sender address/port: {pkg_origin_address_port}")  # might need to uncomment this
            else:
                if self._connected_to_other_client and self._connecting:
                    self._connecting = False

                self._handle_received_pkg(pkg, pkg_origin_address_port)

    def _end_connection(self):
        self._stop_all_threads = True
        if self._connecting and not self._connected_to_other_client:
            raise TimeoutError()
        self._connecting = False
        self._connected_to_other_client = False
        self._LOGGER.info("<ENDED_CONNECTION>")

    def receive(self):
        if self._in_data_queue.empty():
            return None
        else:
            return self._in_data_queue.get()

    def is_connected(self):
        return self._connected_to_other_client and not self._connecting

    def get_send_progress(self):
        """goes down to 0 to show progress"""
        return self._out_pkg_queue.qsize()
    def get_rec_progress(self):
        """goes down to 0 to show progress"""
        return self._current_data_collection_last_id
    
    def disconnect(self):
        if self._connected_to_other_client:
            self._out_pkg_queue.put(self._create_pkg(self._PackageType.END))
            while self.get_send_progress() > 0:
                time.sleep(0.01)
            self._end_connection()
            return True
        return False
