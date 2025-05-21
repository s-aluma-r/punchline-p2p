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
from punchline_p2p.punchline import Punchline, VersionError
from typing import Optional, List, Dict, Tuple, Any

# IDEA: encrypt data before deviding into packages (this way resends etc dont matter) dont encrypt faf packages or acks

class PunchlineClient(Punchline):
    """This Client is used to setup a connection to another Client and send data back and forth"""
    # IDEA maybe turn the json parts inneral serial parts and make it possible to change serialisation methods

    # IDEA: maybe have second out queue for faf packages that alternates with normal send queue to allow sending data while large ammount of data is transmitted?

    # constant
    _DEDICATED_SERVER: Optional[Tuple[str, int]] = None
    
    # data
    _stop_all_threads: bool = False
    _destination_address_port: Optional[Tuple[str, int]] = None
    _out_pkg_queue: Queue = Queue()  # only for packages
    _connected_to_other_client: bool = False
    _connecting: bool = False
    _in_data_queue: Queue = Queue()  # for user data
    _current_data_collection: Optional[list] = None
    _current_data_collection_last_id: Optional[int] = None  # has to be none because none triggers fresh start
    _receive_thread: Optional[threading.Thread] = None
    _send_thread: Optional[threading.Thread] = None
    _code: bytes = None  # TODO rename code -> punchline

    _client_number: int = -1  # this is eather 1 or 0 after connecting
    

        # IDEA add optional method to replace functions for python -> binary and back (default json but pickle or custom should work too) using function as parameter at init with dults being json ones
    
    def __init__(self, PSK: Optional[bytes] = None, dedicated_server: Optional[Tuple[str, int]] = None, logging_level: int = logging.CRITICAL):
        super().__init__(logging_level)
        self._DEDICATED_SERVER = dedicated_server

    def _send_pkg(self, pkg: bytes, destination_address_port: Tuple[str, int]) -> None:
        try:
            super()._send_pkg(pkg, destination_address_port)
        except TimeoutError as e:
            self._end_connection()
            raise e

    def connect_async(self, code: bytes) -> bool:  # TODO rename code -> punchline
        """ connects you to another client with the same punchline(code). returns false if you are already connected or still connecting, returns True if connection process started"""
        # check code size (needs to fit in single data pkg)
        if len(code) > self._MAX_PKG_DATA_SIZE:
            raise ValueError(f"code can max be len: {self._MAX_PKG_DATA_SIZE}")

        if (self._connected_to_other_client or self._connecting):
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

    def connect(self, punchline: bytes) -> bool:
        
        if not self.connect_async(punchline):
            return False

        while self.is_connecting():
            time.sleep(0.01)

        if not self.is_connected():
            if not self.in_thread_error_queue.empty():
                raise self.in_thread_error_queue.get()

            raise Exception("Unexpected Error")
                

        return True
        
    def _get_semi_random_server(self, code: bytes) -> Tuple[str, int]:  # TODO rename code -> punchline
        """ returns a random server from the server list that fits the version of this client based on the punchline(code) as a seed (or the dedicated server given while creating PunchlineClient Obj)"""
        if self._DEDICATED_SERVER:
            resolved = (socket.gethostbyname(self._DEDICATED_SERVER[0]), self._DEDICATED_SERVER[1])
            return resolved
        response = requests.get('https://raw.githubusercontent.com/s-aluma-r/punchline-p2p/refs/heads/main/active_servers.json', timeout=10)
        # timeout error is fine (maybe wrap in own error when porting to micropython)
        if response.status_code == 200:
            versions = json.loads(response.content.decode())
            servers = versions[f"V{self._VERSION}"]
            rand = r.Random()
            rand.seed(code)
            server_pos = r.randint(0, len(servers)-1)
            server = servers[server_pos]
            ip = socket.gethostbyname(server["ip"])
            port = server["port"]
            return (ip, port)
        else:
            raise ConnectionError("Couldn't reach raw.githubusercontent.com to retrieve active servers")
        
    def _append_data_packages(self, data: bytes, is_json: bool = False) -> None:
        # TODO maybe this should get any data then determine what it is and handle it acordingly because its only used in send
        """takes binary data, slices it into packages and appends them to the sending queue"""
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

    def _send_pkg_thread_func(self) -> None:
        """this function runs in the sending thread and sends packages if available or periodic keepalive packages"""
        last_keepalive_time = 0
        while not self._stop_all_threads:
            try:
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
            except Exception as e:
                self._LOGGER.error(f"<ERROR> {e}")
                self.in_thread_error_queue.put(e)
                self.disconnect()

    def send(self, data, fire_and_forget: bool = False) -> bool:
        """this function gets data and prepares it to be sent to the other client. Data can be anyting thats serialisable or binary"""
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
            if len(bin_data) > self._MAX_PKG_DATA_SIZE:
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

    def _collect_multi_package_data(self, data: bytes, pkg_sequence_id: int) -> Optional[bytes]:
        """This function collects and assembles data from consecutive data packages that belong together"""
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

    def _handle_received_pkg(self, pkg: bytes, sender: Tuple[str, int]) -> None:
        """This function handles receiving of packages and turns them back into the intendet data"""
        try:
            package_parts = super()._handle_received_pkg(pkg, sender)
        except VersionError:
            self._send_pkg(self._create_pkg(self._PackageType.END), sender)
            if self._connecting and not self._connected_to_other_client:
                raise VersionError(f"the server you tried to reach [{self.destination_address_port}] had the wrong version")
            elif self._connecting and self._connected_to_other_client:
                raise VersionError(f"the client you tried to reach [{self.destination_address_port}] had the wrong version")
            elif not self._connecting and self._connected_to_other_client:
                raise VersionError(f"the client you tried to reach [{self.destination_address_port}] had the wrong version (but switched version mid conversation?)")
            else:
                raise Exception(" something with the client logic is off (not connected or connecting and received package shouldnt happen)")

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
                self._client_number = pkg_sequence_id

                self._LOGGER.info("<UPDATING ADDRESS> %s --> %s", self._destination_address_port, address_port)
                
                self._destination_address_port = address_port
                self._connected_to_other_client = True

                # initial keepalive to kickstart connection (no need to wait for keepalive interval)
                self._send_pkg(self._create_pkg(self._PackageType.KAL), self._destination_address_port)

        elif pkg_type == self._PackageType.END:
            if self._connecting and not self._connected_to_other_client:
                self._end_connection()
                raise TimeoutError()
            else:
                self._end_connection()
                # TODO somehow let user know connection endet

    def _receive_pkg_thread_func(self) -> None:
        """This function runs in the receive thread and listens for packages to pass along to the handling function"""
        # IDEA implement timeout with recvform itself somehow (if no pkg longer than a few times keepalive delay = timeout?)
        while not self._stop_all_threads:

            try:
                pkg, pkg_origin_address_port = self._UDP_socket.recvfrom(self._BUFFER_SIZE)

                if pkg_origin_address_port != self._destination_address_port:
                    self._LOGGER.warning("<Unexpected sender address/port> %s (IGNORING)", pkg_origin_address_port)
                else:
                    if self._connected_to_other_client and self._connecting:
                        self._connecting = False
                    self._handle_received_pkg(pkg, pkg_origin_address_port)
            except Exception as e:
                self._LOGGER.error(f"<ERROR> {e}")
                self.in_thread_error_queue.put(e)
                self.disconnect()
                

    def _end_connection(self) -> None:
        """This function ends the connection"""
        self._stop_all_threads = True
        self._connecting = False
        self._connected_to_other_client = False
        self._cancel_send_pkg = True  # (because punchline cant see _connecting or _connected_to_other_client it just has this cancel variable)
        self._out_pkg_queue
        while not self._out_pkg_queue.empty():  # clear queue
            self._out_pkg_queue.get()
        self._LOGGER.info("<CONNECTION_ENDED>")

    def receive(self) -> Any:
        """takes one obj from received data queue and returns it to you (returns None uf queue empty)"""
        if self._in_data_queue.empty():
            return None
        else:
            return self._in_data_queue.get()

    def get_receive_queue_length(self) -> int:
        """returns the length of the receive queue (ammount of data you received)"""
        return self._in_data_queue.qsize()

    def is_connected(self) -> bool:
        """returns if you are connected to another client"""
        return self._connected_to_other_client and not self._connecting

    def is_connecting(self) -> bool:
        """returns if you are still connecting to other client"""
        return self._connecting

    def get_send_progress(self) -> int:
        """counts down to 0 to show progress of current package being sent"""
        return self._out_pkg_queue.qsize()
    def get_rec_progress(self) -> int:
        """counts down to 0 to show progress or current package being received"""
        return self._current_data_collection_last_id
    def get_client_number(self) -> Optional[int]:
        """returns positive int (0 or 1) representing of you are client 0 or 1 (returns None if not connected)"""
        if not self.is_connected():
            return None
        return self._client_number
    
    def disconnect(self) -> None:
        """Disconnects you from other client"""
        if not self._connected_to_other_client and not self._connecting:
            self._LOGGER.info("<ALREADY_DISCONNECTED>")
            self._end_connection()
            return
        
        self._LOGGER.info("<DISCONNECTING>")
        if self._connected_to_other_client:
            # for client wait for data to be sent then end connection
            self._out_pkg_queue.put(self._create_pkg(self._PackageType.END))
            while self.get_send_progress() > 0:
                time.sleep(0.01)
        elif self._connecting:
            # for server only send end package
            self._send_pkg(self._create_pkg(self._PackageType.END), self._destination_address_port)
        # finally or only this if other client already disconnected
        self._end_connection()
