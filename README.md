# Punchline P2P
### Connect with only a single string
```
cd punchline-p2p
pip install -e .
```
Then use:
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

data = pc.recieve()
```

To start a local server you currently need to use:
```
from punchline_p2p import PunchlineServer
s = PunchlineServer()
```
This wont be needed in the future when at least one server is public.

## Disclamer:
This project is under active development and will change regularly for the time being.
