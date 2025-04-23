import threading
import time
import pandas as pd
import logging
from punchline_p2p.punchline import Punchline, VersionError

class PunchlineServer(Punchline):
    _LOCAL_IP = ""
    _LOCAL_PORT = 12345
    _CLIENT_TIMEOUT_DELAY_S = 15
    # client should be dataframe with punchline, ip, port, timeout where timeout is a timestamp that just triggers an end package to be sent to client
    _clients = None
    _clients_lock = threading.Lock()


    def __init__(self, port=12345, logging_level=logging.INFO):
        super().__init__(logging_level)
        self._UDP_socket.bind((self._LOCAL_IP, self._LOCAL_PORT))
        self._clients = pd.DataFrame(
            {
                "punchline": pd.Series(name="punchline", dtype=bytes),
                "ip": pd.Series(dtype=str),
                "port": pd.Series(dtype=int),
                "timeout": pd.Series(dtype="datetime64[ns]"),
            },
        )
        self._LOCAL_PORT = port

    def _handle_received_pkg(self, pkg, sender):
        try:
            package_parts = super()._handle_received_pkg(pkg, sender)
        except VersionError:
            self._send_pkg(self._create_pkg(self._PackageType.END), sender)  # this is sent back so the other person also sees that its the Wrong version
            return
        if package_parts:
            pkg_version, pkg_type, pkg_sequence_id, data = package_parts
        else:
            return  # same package was sent twice due to ack being lost

        if pkg_type == self._PackageType.CON:
            with self._clients_lock:
                if data in self._clients['punchline'].values:
                    candidates = self._clients[self._clients['punchline'] == data]
                    if not candidates.empty:
                        client = candidates.iloc[0]
                    else:
                        raise Exception("Somehow _clients changed while locked")
                    if (client['ip'], client['port']) == sender:
                        return  # same ip , port and punchline tried connecting twice (shouldnt happen)

                    self._link(sender, (client['ip'], client['port']), data)
                    self._clients.drop(index=client.name, inplace=True)
                else:
                    self._clients.loc[len(self._clients)] = [data, sender[0], sender[1], pd.Timestamp.now()]
                    self._LOGGER.info("<NEW_CLIENT> %s with Punchline: %s", sender, data)

        elif pkg_type == self._PackageType.END:
            with self._clients_lock:
                connection = self._clients[(self._clients['ip'] == sender[0]) & (self._clients['port'] == sender[1])]
                if not connection.empty:
                    self._LOGGER.info("<CANCEL    > %s", sender)
                    self._clients.drop(connection.index, inplace=True)
        elif pkg_type == self._PackageType.KAL:
            pass  # kal could but currently doesnt reset timeout

    def _check_timeout(self):
        # remove all timestamps older than now - n seconds and send them end
        with self._clients_lock:
            current_time = pd.Timestamp.now()
            # Define the threshold (30 seconds ago)
            threshold_time = current_time - pd.Timedelta(seconds=self._CLIENT_TIMEOUT_DELAY_S)
            # timed out clients (older than 30sec)
            timed_out_clients = self._clients[self._clients['timeout'] < threshold_time]
            self._clients.drop(timed_out_clients.index, inplace=True)

        for index, row in timed_out_clients.iterrows():
            address_port = (row['ip'], row['port'])
            self._send_pkg(self._create_pkg(self._PackageType.END), address_port)
            self._LOGGER.info("<TIMEOUT   > %s with Punchline: %s", address_port, row['punchline'])
        time.sleep(1)
    

    def _send_pkg(self, pkg: bytes, destination_address_port):
        try:
            super()._send_pkg(pkg, destination_address_port)
        except TimeoutError:
            pass  # ignore the timeout probably broke connection
    
    def _link(self, a, b, punchline):
        self._LOGGER.info("<LINKING   > %s <-- %s --> %s", a, punchline, b)

        a_bin = self._address_port_to_binary(a)
        b_bin = self._address_port_to_binary(b)
        # add sequence id 1 or 0 for user to differentiate if he connected first or second (this is a feature needed for future encryption)
        send_b_to_a = threading.Thread(target=self._send_pkg, args=(self._create_pkg(self._PackageType.CON, b_bin, 1), a), daemon=True)
        send_a_to_b = threading.Thread(target=self._send_pkg, args=(self._create_pkg(self._PackageType.CON, a_bin, 0), b), daemon=True)
        send_b_to_a.start()
        send_a_to_b.start()

    def _receive_thread(self):
        while True:
            pkg, pkg_origin_address_port = self._UDP_socket.recvfrom(self._BUFFER_SIZE)
            self._handle_received_pkg(pkg, pkg_origin_address_port)
    
    def run_server(self):
        self._receive_thread = threading.Thread(target=self._receive_thread, daemon=True)
        self._receive_thread.start()
        while True:
            self._check_timeout()
