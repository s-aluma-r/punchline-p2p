from abc import ABC
from enum import IntEnum
import struct
import time
import socket
import hashlib
import threading
import random as r
import logging
from typing import Optional, List, Dict, Tuple

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
    """Base class for punchline p2p functionality"""

    # ABSOLUTES (these can never change because otherwise version checks wont work anymore)
    _VERSION_HEADER: str = ">H"
    _VERSION_HEADER_SIZE: int = 2
    # constants
    _VERSION: int = 0  # 1 byte vanue for version
    _BUFFER_SIZE: int = 1024  # not more than 1500
    _HEADER: str = ">HBBI"  # header layout VERSION, TYPE, sequence_id
    _HEADER_IDX: Dict[str, int] = {"VERSION": 0, "TYPE": 1, "ROLLING_ID": 2, "SEQUENCE_ID": 3}
    _HEADER_SIZE: int = 0
    _MAX_PKG_DATA_SIZE: int = 0
    _KEEPALIVE_DELAY_S: float = 1
    _PKG_CHECK_ACK_DELAY_S: float = 0.00001
    _PKG_CHECK_ACK_TIMEOUT_DELAY_S: float = 0.1  # wait a good while before resend (is probably due to bigger issue)
    _SEND_QUEUE_EMPTY_CHECK_DEALY_S: float = 0.00001  # bigger than _PKG_CHECK_ACK_DELAY_S ?  # TODO spelling
    _CONNECTION_TIMEOUT_S: float = 5  # unused but for recvfrom timeout in future
    _MAX_RESEND_TRIES: int = 5

    # data
    _UDP_socket: Optional[socket.socket] = None
    _ack_hash_list: List[bytes] = []
    _ack_hash_list_lock: threading.Lock = threading.Lock()
    _cancel_send_pkg: bool = False

    # there to differentiate single packages with same content sent right after each other from a resend (only needed for dat or jdt with sequence_id=0)
    _rolling_id: int = 0  # rotating id (0-256) to make 2 packages with same data different
    _last_rec_ack_ret_pkg: bytes = b''  # last received ack return package (just to check that last received package isnt the same as current one)

    # statistics
    stat_resends: int = 0
    stat_ping: float = 0

    class _PackageType(IntEnum):
        """standard package definitions"""
        KAL = 0  # KeepALive                        | 
        DAT = 1  # DATa                             |
        JDT = 2  # Json Data                        | same as data only json
        FAF = 3  # Fire And Forget                  |
        JFF = 4  # Json Fire and Forget             |
        ACK = 5  # ACKnowladge                      |

        CON = 6  # CONnect to                       | this is used between server and client to send code to server and get ip/port of partner client back
        END = 7  # END connection                   |
        # TODO PSK --> switch to pre shared key (this needs to have some logic with back and forth to see if change in encryption has worked or not)

    # _NO_ACK_PKG_TYPES = [_PackageType.FAF, _PackageType.JFF, _PackageType.KAL, _PackageType.ACK]
    _ACK_RET_PKG_TYPES: List[_PackageType] = [_PackageType.DAT, _PackageType.JDT, _PackageType.CON, _PackageType.END]  # there packages require an ack to be returned

    def __init__(self, logging_level: int = logging.NOTSET):
        """Constructor"""
        self._HEADER_SIZE = struct.calcsize(self._HEADER)
        self._MAX_PKG_DATA_SIZE = self._BUFFER_SIZE-self._HEADER_SIZE
        self._UDP_socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self._rolling_id = r.randint(0, 255)
        self._LOGGER = logging.getLogger(self.__class__.__name__)
        self._LOGGER.setLevel(logging_level)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        self._LOGGER.addHandler(console_handler)

    def _hash(self, pkg: bytes) -> bytes:
        """Generates consistant hash"""
        hash_object = hashlib.sha256()
        hash_object.update(pkg)
        return hash_object.digest()

    def _address_port_to_binary(self, address_port: Tuple[str, int]) -> bytes:
        """Converts address and port to binary to send"""
        ip_binary = socket.inet_aton(address_port[0])  # 4-byte binary for IPv4
        port_binary = struct.pack('!H', address_port[1])  # 2-byte binary for port in network order
        bin_address = ip_binary+port_binary
        return bin_address

    def _binary_to_address_port(self, binary: bytes) -> Tuple[str, int]:
        """Converts binary address and port back to python objects"""
        ip_binary = binary[:4]  # First 4 bytes for IP
        port_binary = binary[4:]  # Last 2 bytes for port
        # Convert back to human-readable formats
        ip_address = socket.inet_ntoa(ip_binary)  # Binary to IPv4 string
        port = struct.unpack('!H', port_binary)[0]  # Unpack 2 bytes as big-endian integer

        # Resulting tuple
        address_port = (ip_address, port)
        return address_port

    def _create_pkg(self, pkg_type: _PackageType, data: bytes = b'\x00', sequence_id: int = 0) -> bytes:
        """Creates packsage with header and data (and optional sequence_id in case of multiple packages)"""
        if not (0 <= len(data) <= self._MAX_PKG_DATA_SIZE):
            raise ValueError(f"data must be bytes with max len={self._MAX_PKG_DATA_SIZE}.")

        header = struct.pack(self._HEADER, self._VERSION, pkg_type, self._rolling_id, sequence_id)
        self._rolling_id = (self._rolling_id+1) % 256

        return header+data

    def _ack_hash_list_check_remove(self, ack_hash) -> bool:
        """Checks if ack has arrived and removes it from memory"""
        with self._ack_hash_list_lock:
            if ack_hash in self._ack_hash_list:
                self._ack_hash_list.remove(ack_hash)
                return True
            return False

    def _ack_hash_list_append(self, ack_hash) -> None:
        """Add hash to hash list"""
        with self._ack_hash_list_lock:
            self._ack_hash_list.append(ack_hash)

    def _unpack_pkg(self, pkg: bytes, get_only_type: bool = False) -> Tuple[Optional[int], int, Optional[int], Optional[int], Optional[bytes]]:
        """Unpacks package into its parts with built in version check"""
        v = struct.unpack(self._VERSION_HEADER, pkg[:self._VERSION_HEADER_SIZE])[0]
        if v != self._VERSION:
            raise VersionError()
        header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
        pkg_type = header[self._HEADER_IDX["TYPE"]]
        if get_only_type:
            return None, pkg_type, None, None, None

        pkg_version = header[self._HEADER_IDX["VERSION"]]
        if pkg_version != self._VERSION:
            raise RuntimeError(f"Received package version:{pkg_version} != Client version")
        pkg_rolling_id = header[self._HEADER_IDX["ROLLING_ID"]]
        pkg_sequence_id = header[self._HEADER_IDX["SEQUENCE_ID"]]
        data = pkg[self._HEADER_SIZE:]
        return pkg_version, pkg_type, pkg_rolling_id, pkg_sequence_id, data

    def _send_pkg(self, pkg: bytes, destination_address_port: Tuple[str, int]) -> None:
        """Sends package to defined address annd port"""
        split_pkg = self._unpack_pkg(pkg, get_only_type=not self._LOGGER.isEnabledFor(logging.DEBUG))
        pkg_version, pkg_type, pkg_rolling_id, pkg_sequence_id, data = split_pkg

        self._LOGGER.debug("<SENDING > %s --> %d, %d, %d, %d, %.100s", destination_address_port, pkg_version, pkg_type, pkg_rolling_id, pkg_sequence_id, data)
        self._UDP_socket.sendto(pkg, destination_address_port)

        resends = 0
        self._cancel_send_pkg = False
        if pkg_type in self._ACK_RET_PKG_TYPES:
            pkg_hash = self._hash(pkg)
            timeout = 0
            while not self._cancel_send_pkg and not self._ack_hash_list_check_remove(pkg_hash) and resends < self._MAX_RESEND_TRIES:
                #  TODO make this function of time instead of max resend tries and maybe end connection if it runs out
                if timeout >= self._PKG_CHECK_ACK_TIMEOUT_DELAY_S:
                    self._UDP_socket.sendto(pkg, destination_address_port)  # resend pkg
                    timeout = 0
                    resends += 1
                    self.stat_resends += 1
                else:
                    time.sleep(self._PKG_CHECK_ACK_DELAY_S)
                    timeout += self._PKG_CHECK_ACK_DELAY_S

            if not self._cancel_send_pkg:
                self.stat_ping = (timeout + self._PKG_CHECK_ACK_TIMEOUT_DELAY_S * resends)*1000

            if resends >= self._MAX_RESEND_TRIES:
                raise TimeoutError(f"{self._MAX_RESEND_TRIES=} reached")
        self._LOGGER.debug("<SENT    > %s -%d-> %d, %d, %d, %d, %.100s", destination_address_port, resends+1, pkg_version, pkg_type, pkg_rolling_id, pkg_sequence_id, data)

    def _handle_received_pkg(self, pkg: bytes, sender: Tuple[str, int]) -> Optional[Tuple[int, int, int, bytes]]:
        """Handles reception of package and sends back ack (extend this to handle data)"""
        pkg_version, pkg_type, pkg_rolling_id, pkg_sequence_id, data = self._unpack_pkg(pkg)

        self._LOGGER.debug("<RECIEVED> %s --> %d, %d, %d, %d, %.100s", sender, pkg_version, pkg_type, pkg_rolling_id, pkg_sequence_id, data)

        if pkg_type == self._PackageType.ACK:
            self._ack_hash_list_append(data)  # no need for mutex/lock because only one thread uses this
        if pkg_type in self._ACK_RET_PKG_TYPES:
            self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)), sender)

            if self._last_rec_ack_ret_pkg == pkg:  # resend but already received last package
                return None  # package was sent twice by mistake (because ack didnt reach destination)
            self._last_rec_ack_ret_pkg = pkg

        return (pkg_version, pkg_type, pkg_sequence_id, data)

    def get_max_pkg_data_size(self) -> int:
        """Returns the maximum binary size for a single package"""
        return self._MAX_PKG_DATA_SIZE

class VersionError(Exception):
    """A generic Exception to signal a version conflict between server and client or 2 clients"""
    def __init__(self, message="The server/client you connected to is using a different version from you."):
        super().__init__(message)
