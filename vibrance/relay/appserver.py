import socket
import subprocess
import atexit
import time
import json
import os
import selectors
import tempfile
from multiprocessing.dummy import Pool as ThreadPool


class AppServer:
    """Server allowing clients to connect and receive updates."""

    # Constants used to indicate socket type when used with selectors:
    SERVER = 0  # server socket
    WAITING = 1  # awaiting authentication
    CLIENT = 2  # connected client

    def __init__(self, cert=None, key=None):
        """Creates an AppServer. If cert and key are specified, uses SSL."""

        self.selector = selectors.DefaultSelector()

        tempdir = os.path.join(tempfile.gettempdir(), "vibrance_relay")

        if not os.path.exists(tempdir):
            os.mkdir(tempdir)

        sockpath = os.path.join(tempdir, "sock")

        if os.path.exists(sockpath):
            os.remove(sockpath)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(sockpath)
        sock.listen(16)
        self.selector.register(sock, selectors.EVENT_READ,
                               AppServer.SERVER)

        if cert is not None and key is not None:
            self.websockify_proc = subprocess.Popen(["websockify", "9000",
                                                     f"--unix-target={sockpath}",
                                                     f"--cert={cert}",
                                                     f"--key={key}",
                                                     "--ssl-only"],
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL)
        else:
            self.websockify_proc = subprocess.Popen(["websockify", "9000",
                                                     f"localhost:9001"],
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL)

        atexit.register(self.websockify_proc.terminate)

        self.clients = {}
        self.lastMessage = {}

        self.pool = ThreadPool(32)

        self.messages = {}

    def accept(self, server):
        """Accepts a new client on the given server socket."""
        new_client, addr = server.accept()
        self.selector.register(new_client, selectors.EVENT_READ,
                               AppServer.WAITING)
        self.lastMessage[new_client] = time.time()

    def addToZone(self, client):
        try:
            data = client.recv(1024)
        except OSError:
            self.remove(client)
            return
        if len(data) == 0:
            self.remove(client)
            return

        zone = data.decode("utf-8", "ignore")

        self.selector.modify(client, selectors.EVENT_READ, AppServer.CLIENT)
        self.clients[client] = zone
        self.lastMessage[client] = time.time()

    def remove(self, client):
        """Removes a client from all lists and closes it if possible."""
        try:
            self.selector.unregister(client)
        except KeyError:
            pass
        try:
            del self.clients[client]
        except ValueError:
            pass
        try:
            del self.lastMessage[client]
        except KeyError:
            pass
        try:
            client.close()
        except OSError:
            pass

    def handleMessage(self, client):
        """Handles an incoming message from a client."""
        try:
            data = client.recv(1024)
        except OSError:
            self.remove(client)
            return
        if len(data) == 0:  # Client disconnected
            self.remove(client)
            return

        msg = data.decode("utf-8", "ignore")

        if msg == "OK":
            self.lastMessage[client] = time.time()
        else:
            self.remove(client)
            return

    def run(self):
        """Monitors for new client connections or messages and handles them
        appropriately."""
        while True:
            events = self.selector.select()
            for key, mask in events:
                sock = key.fileobj
                type = key.data

                if type == AppServer.SERVER:
                    self.accept(sock)
                elif type == AppServer.WAITING:
                    self.addToZone(sock)
                elif type == AppServer.CLIENT:
                    self.handleMessage(sock)

    def handleCheckAlive(self):
        """Periodically checks each client to ensure they are still alive
        and sending messages."""
        while True:
            clients = list(self.clients.keys())
            for client in clients:
                try:
                    if time.time() - self.lastMessage[client] > 20:
                        self.remove(client)
                except KeyError:  # Client was already removed
                    pass
                time.sleep(10 / len(clients))

    def broadcastToClient(self, item):
        """Broadcasts the appropriate current message to a single client."""
        client, zone = item
        if zone not in self.messages:
            return
        msg = json.dumps(self.messages[zone])
        try:
            client.send(msg.encode("utf-8"))
        except OSError:
            self.remove(client)

    def broadcast(self, messages):
        """Broadcasts the given messages to all clients."""
        ts = time.time()
        self.messages = messages
        self.pool.map(self.broadcastToClient, self.clients.items())
        telemetry = {"clients": len(self.clients),
                     "latency": int((time.time() - ts)*1000)}
        return telemetry