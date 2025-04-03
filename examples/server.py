#!/usr/bin/env python3
import logging
from punchline_p2p import PunchlineServer
if __name__ == "__main__":
    s = PunchlineServer()
    print("Listening")
    s.run_server()
    print("Server ended")
