from punchline_p2p.punchline import Punchline
import logging
import threading
import socket
import json

class VerifyActiveServers(Punchline):
    def __init__(self, logging_level=logging.ERROR):
        super().__init__(logging_level)
        self._UDP_socket.settimeout(2)

    def _send_pkg_no_timeout_error(self, pkg, address_port):
        try:
            self._send_pkg(pkg, address_port)
        except TimeoutError:
            pass
        
    def verify(self, servers):
        new_server_list = {}
        for version in servers:
            new_server_list[version] = []
            for i, server in enumerate(servers[version]):
                timeout=False
                send_thread = threading.Thread(target=self._send_pkg_no_timeout_error, args=(self._create_pkg(self._PackageType.DAT), (server['ip'], server['port'])), daemon=True)
                send_thread.start()
                # TODO catch send thread timeout exception for cleaner output
                try:
                    pkg, sender = self._UDP_socket.recvfrom(self._BUFFER_SIZE)
                    pkg_version, pkg_type, pkg_sequence_id, data = self._handle_received_pkg(pkg, sender)
                except socket.timeout:
                    timeout=True
                send_thread.join()  # not needed due to natural timeout in send function but cant join before _handle_received_pkg becaue this func recieves ack

                if timeout:
                    print(f"{server}, timed out --> deleting")
                    continue
                
                info = f"{server} marked as {version}, is V{pkg_version} --> "
                pkg_ver_str = "V"+str(pkg_version)
                if  pkg_ver_str == version:
                    print(info, "correct")
                    new_server_list[version].append(server)
                else:
                    print(info, "wrong --> placing server in right version group")
                    if server not in new_server_list[pkg_ver_str]:
                        new_server_list[pkg_ver_str].append(server)
            if len(new_server_list[version]) == 0:
                del new_server_list[version]
                
        return new_server_list

if __name__ == "__main__":
    with open('../active_servers.json', 'r') as f:
        servers = json.load(f)
        vas = VerifyActiveServers()
        new_servers = vas.verify(servers)
        new_servers_json = json.dumps(new_servers, indent=4)
        print("#################################### Copy the following txt to ../active_servers.json ############################################")
        print(new_servers_json)
        print("###############################################################################################################################")
