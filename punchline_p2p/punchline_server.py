#!/usr/bin/env python3
import threading
import time
import pandas as pd
from punchline_p2p.punchline import Punchline

class PunchlineServer(Punchline):
    _LOCAL_IP = ""
    _LOCAL_PORT = 12345
    _CLIENT_TIMEOUT_DELAY_S = 15
    # client should be dataframe with punchline, ip, port, timeout where timeout is a timestamp that just triggers an end package to be sent to client
    _clients = None
    _clients_lock = threading.Lock()


    def __init__(self, port=12345, debug=False):
        super().__init__(debug)
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

    def _handle_recieve_pkg(self, pkg, sender):
        package_parts = super()._handle_received_pkg(pkg, sender)
        if package_parts:
            pkg_version, pkg_type, pkg_sequence_id, data = package_parts
        else:
            print("TEST WTH WAS THIS?!")
            return  # same package was sent twice by mistake

        if pkg_type == self._PackageType.CON:
            with self._clients_lock:
                if data in self._clients['punchline'].values:
                    candidates = self._clients[self._clients['punchline'] == data]
                    if not candidates.empty:
                        client = candidates.iloc[0]
                    else:
                        print("TEST NOONE HERE")
                        return
                    if (client['ip'], client['port']) == sender:  # INFO this shouldnt happen (only if client restarts with same code and gets same port so maybe reset entry?)
                        print("TEST SAAAAAAME")
                        return
                    self._link(sender, (client['ip'], client['port']), data)
                    self._clients.drop(index=client.name, inplace=True)
                else:
                    self._clients.loc[len(self._clients)] = [data, sender[0], sender[1], pd.Timestamp.now()]
                    if self._DEBUG:
                        print(f"<DBG> NEW_CLIENT: {sender} WITH CODE: {data}")
                if self._DEBUG:
                    print(self._clients)


        elif pkg_type == self._PackageType.KAL:
            pass # kal could but currently doesnt reset timeout

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
            self._send_pkg(self._create_pkg(self._PackageType.END), (row['ip'], row['port']))
        time.sleep(1)
    
        
    def _link(self, a, b, punchline):
        if self._DEBUG:
            print(f"<DBG> Linking: {a} <--{punchline}--> {b}")

        a_bin = self._address_port_to_binary(a)
        b_bin = self._address_port_to_binary(b)
        send_b_to_a = threading.Thread(target=self._send_pkg, args=(self._create_pkg(self._PackageType.CON, b_bin), a), daemon=True)
        send_a_to_b = threading.Thread(target=self._send_pkg, args=(self._create_pkg(self._PackageType.CON, a_bin), b), daemon=True)
        send_b_to_a.start()
        send_a_to_b.start()

    def _recieve_thread(self):
        while True:
            pkg, pkg_origin_address_port = self._UDP_socket.recvfrom(self._BUFFER_SIZE)
            if self._DEBUG:
                print(f"<DBG> From: {pkg_origin_address_port}, Recieved: {pkg}")
            self._handle_recieve_pkg(pkg, pkg_origin_address_port)
    
    def run_server(self):
        self._recieve_thread = threading.Thread(target=self._recieve_thread, daemon=True)
        self._recieve_thread.start()
        while True:
            self._check_timeout()

if __name__ == "__main__":
    s = PunchlineServer(debug=True)
    print("Listening")
    s.run_server()
    print("Server ended")
