import threading
import socket
import json
import time
from enum import IntEnum
import struct
import random as r
import hashlib

class PunchlineServer:
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
    _PKG_CHECK_ACK_TIMEOUT_DELAY_S = _KEEPALIVE_DELAY_S/4
    _SEND_QUEUE_EMPTY_CHECK_DEALY_S = 0.00001
    _CONNECTION_TIMEOUT_S = 5
    _MAX_RESEND_TRIES = 1  # DEBUG FIXME set from 10 to 0 just for data transfer test (should be 10 at server rewrite)

    _UDP_server_socket = None
    _last_recieved_ack_hash = 0

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

        #TODO add end package and also timeout feature (if not even keepalive comes)

    def __init__(self):
        self._HEADER_SIZE = struct.calcsize(self._HEADER)
        self._MAX_PKG_DATA_SIZE = self._BUFFER_SIZE-self._HEADER_SIZE
        self.run_server()

    def _hash(self, pkg:bytes):
        hash_object = hashlib.sha256()
        hash_object.update(pkg)
        return hash_object.digest()
        

    def _create_pkg(self, pkg_type: _PackageType, data: bytes = 0, sequence_id: int=0):
        if not (0 <= self._VERSION < 256):
            raise ValueError("_VERSION must be an unsigned 1-byte integer (0 to 255).")
        if not (0 <= pkg_type < 256):
            raise ValueError("pkg_type must be an unsigned 1-byte integer (0 to 255).")
        if not (0 <= sequence_id <= 4_294_967_295):
            raise ValueError("sequence_id must be an unsigned 4-byte integer (0 to 4,294,967,295).")
        if not (0 <= len(data) < self._MAX_PKG_DATA_SIZE):
            raise ValueError(f"data must be bytes with max len={self._MAX_PKG_DATA_SIZE}.")

        header = struct.pack(self._HEADER, self._VERSION, pkg_type, sequence_id)

        return header+data

    def _send_pkg(self, pkg: bytes, destination_address_port):
        header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
        pkg_type = header[self._HEADER_IDX["TYPE"]]
        
        self._UDP_server_socket.sendto(pkg, destination_address_port)

        if pkg_type not in [self._PackageType.FAF, self._PackageType.JFF, self._PackageType.KAL, self._PackageType.ACK]:
            pkg_hash = self._hash(pkg)
            timeout = 0
            resends = 0
            while pkg_hash != self._last_recieved_ack_hash and resends < self._MAX_RESEND_TRIES:
                if timeout >= self._PKG_CHECK_ACK_TIMEOUT_DELAY_S:
                    self._UDP_server_socket.sendto(pkg, destination_address_port)  # resend pkg
                    print("resend")
                    timeout = 0
                    resends +=1
                else:
                    time.sleep(self._PKG_CHECK_ACK_DELAY_S)
                    timeout += self._PKG_CHECK_ACK_DELAY_S

    def run_server(self):
        localIP     = ""

        localPort   = 12345

        bufferSize  = 1024

        # msgFromServer       = "keepalive"
        # keepalive         = str.encode(msgFromServer)
        # Create a datagram socket

        self._UDP_server_socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)

        # Bind to address and ip

        self._UDP_server_socket.bind((localIP, localPort))

        clients = {}

        print("UDP server up and listening")

        while(True):

            print("listening", end="", flush=True)
            bytesAddressPair = self._UDP_server_socket.recvfrom(bufferSize)
            print("recieved:", bytesAddressPair)

            pkg = bytesAddressPair[0]
            address = bytesAddressPair[1]

            header = struct.unpack(self._HEADER, pkg[:self._HEADER_SIZE])
            data = pkg[self._HEADER_SIZE:]
            pkg_version = header[self._HEADER_IDX["VERSION"]]
            if pkg_version != self._VERSION:
                raise RuntimeError(f"Recieved package version:{pkg_version} != Client version")
            pkg_type = header[self._HEADER_IDX["TYPE"]]
            pkg_sequence_id = header[self._HEADER_IDX["SEQUENCE_ID"]]

            print(f"{pkg_version=}\t{pkg_type=}\t{pkg_sequence_id}\t{data=}")
            
            if pkg_type == self._PackageType.KAL:
                pass
            elif pkg_type == self._PackageType.CON:
                self._send_pkg(self._create_pkg(self._PackageType.ACK, self._hash(pkg)), address)
                if data in clients.keys():
                    print(f"{address} <-{data}-> {clients[data]}")
                    ip_binary = socket.inet_aton(address[0])  # 4-byte binary for IPv4
                    port_binary = struct.pack('!H', address[1])  # 2-byte binary for port in network order
                    bin_address = ip_binary+port_binary
                    other_address = clients[data]
                    ip_binary = socket.inet_aton(other_address[0])  # 4-byte binary for IPv4
                    port_binary = struct.pack('!H', other_address[1])  # 2-byte binary for port in network order
                    bin_other_address = ip_binary+port_binary
                    self._send_pkg(self._create_pkg(self._PackageType.CON, bin_address), other_address)
                    print(f"{other_address} done")
                    self._send_pkg(self._create_pkg(self._PackageType.CON, bin_other_address), address)
                    print(f"{other_address} done")
                    del clients[data]
                else:
                    print(f"new client {address}")
                    clients[data] = address
                print(f"{clients=}")
            elif pkg_type == self._PackageType.ACK:
                # take data and place it into last recieved ack hash
                self._last_recieved_ack_hash = data

            # TODO add recieve and send thread otherwise ack thing cant work!!!
            # while sending nothing is recieved so ack pkg doesnt arrive so send never finishes
            # or even better reciever thread and sender that works off recieved packages?
            # 

                    
        

if __name__ == "__main__":
    s = PunchlineServer()
    
# TODO maybe start new send thread for each pkg that expects ack and save it in dict with key being hash and val being the thread
# the main thread recieves, every time it recieves an ack it gets the key and ends the resend try thread? (so turn only resend part of send function into thread)
