# Punchline P2P
##  Connect with only a single string
### To Install use:
```
git clone https://github.com/s-aluma-r/punchline-p2p
cd punchline-p2p
pip install -e .
```
or 
```
pip install git+https://github.com/s-aluma-r/punchline-p2p.git
```

### To test you can use:
```
punchline_file_transfer
```
this is unencrypted filetransfer for now but will be a proper encrypted feature in the future

### How to use:
```
from punchline_p2p import PunchlineClient

pc = PunchlineClient()

pl = input("Enter punch-line: ")
	
pc.connect_async(pl.encode(encoding='utf-8'))

print("connecting...")
while not pc.is_connected():
    time.sleep(0.01)
print(f"Connected to: {pl._destination_address_port}")  # this will be exposed via function in the future
```
Now you can use
```
pc.send("your serialisable or binary data")
```
to send data
and
```
data = None
while data is None:
	time.sleep(0.01)
	data = pc.recieve()
```
to receive data (other ways of handling no data being received are also possible to use)

### Local testing server:
use
```
pip install "git+https://github.com/s-aluma-r/punchline-p2p.git#egg=punchline_p2p[server]"
```
```
from punchline_p2p import PunchlineServer
s = PunchlineServer()
```
or
```
run_punchline_server
```

## Contribute a server
There is currently a server public thats hosted by me.
If you are interested in contributing a server to this project i will be creating a docker-compose file and a normal instruction soon and a form to submit your server address and port.
The load of all users is automatically spread to all version matching servers.

## Disclamer:
This project is under active development and will change regularly for the time being.
