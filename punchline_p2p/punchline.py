from abc import ABC
from enum import IntEnum
import struct
import time
import socket
import hashlib
import threading
import random as r

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


class Punchline(ABC):

    # constants
    _DEBUG = False
    _VERSION = 0  # 1 byte vanue for version
    _BUFFER_SIZE = 1024  # not more than 1500
    _HEADER = ">HBBI"  # header layout VERSION, TYPE, sequence_id
    _HEADER_IDX = {"VERSION": 0, "TYPE": 1, "ROLLING_ID": 2, "SEQUENCE_ID": 3}
    _HEADER_SIZE = 0
    _MAX_PKG_DATA_SIZE = 0
    _KEEPALIVE_DELAY_S = 1
    _PKG_CHECK_ACK_DELAY_S = 0.00001
    _PKG_CHECK_ACK_TIMEOUT_DELAY_S = _KEEPALIVE_DELAY_S/4  # wait a good while before resend (is probably due to bigger issue)
    _SEND_QUEUE_EMPTY_CHECK_DEALY_S = 0.00001  # bigger than _PKG_CHECK_ACK_DELAY_S ? 
    _CONNECTION_TIMEOUT_S = 5
    _MAX_RESEND_TRIES = 10

    # data
    _UDP_socket = None
    _ack_hash_list = []
    _ack_hash_list_lock = threading.Lock()

    # there to differentiate single packages with same content sent right after each other from a resend (only needed for dat or jdt with sequence_id=0)
    _rolling_id = 0  # rotating id (0-256) to make 2 packages with same data different
    _last_rec_ack_ret_pkg = b''  # last recieved ack return package type

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
        # KEY = 6  # KEY code for connect or encrypt|
        CON = 6  # CONnect to                       | this is used between server and client to send code to server and get ip/port of partner client back
        END = 7  # END connection                   |
        # TODO PSK --> switch to pre shared key (this needs to have some logic with back and forth to see if change in encryption has worked or not)

    # _NO_ACK_PKG_TYPES = [_PackageType.FAF, _PackageType.JFF, _PackageType.KAL, _PackageType.ACK]
    _ACK_RET_PKG_TYPES = [_PackageType.DAT, _PackageType.JDT, _PackageType.CON, _PackageType.END]  # ack return package types

    def __init__(self, debug):
        self._HEADER_SIZE = struct.calcsize(self._HEADER)
        self._MAX_PKG_DATA_SIZE = self._BUFFER_SIZE-self._HEADER_SIZE
        self._UDP_socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self._rolling_id = r.randint(0, 255)
        self._DEBUG = debug

    def _hash(self, pkg: bytes):
        hash_object = hashlib.sha256()
        hash_object.update(pkg)
        return hash_object.digest()

    def _address_port_to_binary(self, address_port):
        ip_binary = socket.inet_aton(address_port[0])  # 4-byte binary for IPv4
        port_binary = struct.pack('!H', address_port[1])  # 2-byte binary for port in network order
        bin_address = ip_binary+port_binary
        return bin_address

    def _binary_to_address_port(self, binary):
        ip_binary = binary[:4]  # First 4 bytes for IP
        port_binary = binary[4:]  # Last 2 bytes for port
        
        # Convert back to human-readable formats
        ip_address = socket.inet_ntoa(ip_binary)  # Binary to IPv4 string
        port = struct.unpack('!H', port_binary)[0]  # Unpack 2 bytes as big-endian integer

        # Resulting tuple
        address_port = (ip_address, port)
        return address_port
    
    def _create_pkg(self, pkg_type: _PackageType, data: bytes = b'\x00', sequence_id: int = 0):
        if not (0 <= self._VERSION < 256):
            raise ValueError("_VERSION must be an unsigned 1-byte integer (0 to 255).")
        if not (0 <= pkg_type < 256):
            raise ValueError("pkg_type must be an unsigned 1-byte integer (0 to 255).")
        if not (0 <= sequence_id <= 4_294_967_295):
            raise ValueError("sequence_id must be an unsigned 4-byte integer (0 to 4,294,967,295).")
        if not (0 <= len(data) <= self._MAX_PKG_DATA_SIZE):
            raise ValueError(f"data must be bytes with max len={self._MAX_PKG_DATA_SIZE}.")

        header = struct.pack(self._HEADER, self._VERSION, pkg_type, self._rolling_id, sequence_id)
        self._rolling_id = (self._rolling_id+1) % 256

        return header+data

    def _ack_hash_list_check_remove(self, ack_hash):
        with self._ack_hash_list_lock:
            if ack_hash in self._ack_hash_list:
                self._ack_hash_list.remove(ack_hash)
                return True
            return False

    def _ack_hash_list_append(self, ack_hash):
        with self._ack_hash_list_lock:
            self._ack_hash_list.append(ack_hash)

    def _send_pkg(self, pkg: bytes, destination_address_port):
        header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
        pkg_type = header[self._HEADER_IDX["TYPE"]]

        # print(f"<DBG> sending", pkg)
        
        self._UDP_socket.sendto(pkg, destination_address_port)

        if pkg_type in self._ACK_RET_PKG_TYPES:
            pkg_hash = self._hash(pkg)
            timeout = 0
            resends = 0
            while not self._ack_hash_list_check_remove(pkg_hash) and resends < self._MAX_RESEND_TRIES:  # TODO make this function of time instead of max resend tries and maybe end connection if it runs out
                if timeout >= self._PKG_CHECK_ACK_TIMEOUT_DELAY_S:
                    self._UDP_socket.sendto(pkg, destination_address_port)  # resend pkg
                    timeout = 0
                    resends += 1
                    self.stat_resends += 1
                else:
                    time.sleep(self._PKG_CHECK_ACK_DELAY_S)
                    timeout += self._PKG_CHECK_ACK_DELAY_S

            self.stat_ping = (timeout + self._PKG_CHECK_ACK_TIMEOUT_DELAY_S * resends)*1000

            if resends >= self._MAX_RESEND_TRIES:
                self.stat_failed_sends += 1
        if self._DEBUG:
            print(f"<DBG> To: {destination_address_port}, Sent: {pkg}")

    def _handle_received_pkg(self, pkg, sender):
        header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
        data = pkg[self._HEADER_SIZE:]
        pkg_version = header[self._HEADER_IDX["VERSION"]]
        if pkg_version != self._VERSION:
            raise RuntimeError(f"Recieved package version:{pkg_version} != Client version")
        pkg_type = header[self._HEADER_IDX["TYPE"]]
        pkg_sequence_id = header[self._HEADER_IDX["SEQUENCE_ID"]]

        if pkg_type == self._PackageType.ACK:
            self._ack_hash_list_append(data)
        if pkg_type in self._ACK_RET_PKG_TYPES:
            self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)), sender)
            
            if self._last_rec_ack_ret_pkg == pkg:  # resend but already recieved last package
                return None  # package was sent twice by mistake (because ack didnt reach destination)
            self._last_rec_ack_ret_pkg = pkg

        return (pkg_version, pkg_type, pkg_sequence_id, data)
