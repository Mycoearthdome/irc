import asyncio
import ssl
import base64
import logging
import certifi
import random
import time
import traceback
import socket
import os
import struct
import shlex
import requests

logging.basicConfig(
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s',
    datefmt='%H:%M:%S',
    level=logging.DEBUG
)

DCC_STATE = {
    "resumes": {},  # filename -> {"resume_pos": int, "nick": str}
    "accepts": {},  # filename -> {"port": int, "pos": int, "nick": str}
}
DCC_STATE_LOCK = asyncio.Lock()

class IRCClient:
    def __init__(self, net_name, server_info, auth_services, nickname, password, email, channel_name, dcc_send_filename):
        self.net_name = net_name
        self.server = server_info[0]
        self.port = server_info[1]
        # Fix: ensure auth_services is always a list
        if isinstance(auth_services, str):
            self.auth_services = [auth_services]
        elif isinstance(auth_services, (list, tuple)):
            self.auth_services = list(auth_services)
        else:
            self.auth_services = []

        self.nickname = nickname
        self.original_nickname = nickname
        self.password = password
        self.email = email
        self.channel_name = channel_name
        self.reader = None
        self.writer = None
        self.logger = logging.getLogger(f"IRCClient-{self.net_name}")
        self.authenticated = False
        self.connected = False
        self.joined_channel = False
        self.reconnect_delay = 10
        self.sasl_supported = False
        self.pending_dcc = {}
        self.dcc_send_filename = dcc_send_filename

    async def send(self, command, log_level=logging.DEBUG):
        if not self.writer:
            self.logger.warning(f"Attempted to send while disconnected: {command}")
            return
        try:
            self.writer.write(command.encode("utf-8") + b"\r\n")
            await self.writer.drain()
            self.logger.log(log_level, f">> {command}")
        except (ConnectionError, asyncio.CancelledError, AttributeError) as e:
            self.logger.error(f"Send failed (connection lost): {e}")
            self.connected = False
            self.writer = None
            self.reader = None
        

    async def read_line_with_timeout(self, timeout=15):
        if not self.reader:
            return None
        try:
            line_bytes = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
            if not line_bytes:
                return None
            return line_bytes.decode("utf-8", errors="ignore").strip()
        except asyncio.TimeoutError:
            return None
        except (ConnectionError, asyncio.IncompleteReadError) as e:
            self.logger.error(f"❌ Connection error while reading: {e}")
            return None

    async def connect(self):
        self.logger.info(f"Connecting to {self.server}:{self.port}...")
        try:
            if self.port == 6697:
                context = ssl.create_default_context(cafile=certifi.where())
                self.reader, self.writer = await asyncio.open_connection(self.server, self.port, ssl=context)
            else:
                self.reader, self.writer = await asyncio.open_connection(self.server, self.port)
            if self.reader and self.writer:
                self.connected = True
                self.logger.info("Connection successful.")
                self.logger.debug(f"Reader: {self.reader}, Writer: {self.writer}")
                return True
            else:
                self.logger.error("❌ Failed to establish reader/writer streams.")
                self.connected = False
                return False
        except ssl.SSLCertVerificationError as e:
            self.logger.error(f"❌ SSL Certificate error: {e}")
        except ConnectionError as e:
            self.logger.error(f"❌ Connection failed: {e}")
        except socket.gaierror as e:
            self.logger.error(f"❌ DNS lookup failed: {self.server}. Error: {e}")
        except OSError as e:
            self.logger.error(f"❌ OS error: {self.server}. Error: {e}")
        self.connected = False
        return False

    async def disconnect(self):
        if self.writer:
            try:
                await self.send("QUIT :Leaving", logging.INFO)
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.connected = False
        self.authenticated = False
        self.joined_channel = False
        self.logger.info("Disconnected.")

    async def handle_message_loop(self):
        """Main loop to handle incoming IRC messages after authentication."""
        try:
            while self.connected:
                line = await self.read_line_with_timeout(timeout=300)
                if not line:
                    continue

                self.logger.debug(f"<< {line}")

                # Respond to server PINGs
                if line.startswith("PING"):
                    pong_response = line.replace("PING", "PONG", 1)
                    await self.send(pong_response, logging.DEBUG)
                    continue

                # Extract sender (nick)
                sender = None
                if line.startswith(":") and "!" in line:
                    sender = line.split("!", 1)[0][1:]

                # Only proceed with PRIVMSG
                if "PRIVMSG" in line:
                    try:
                        prefix, trailing = line.split(" :", 1)
                        parts = prefix.split()
                        if len(parts) >= 3:
                            sender = parts[0][1:].split('!')[0]
                            target = parts[2]
                            message = trailing

                            self.logger.info(f"[{target}] <{sender}> {message}")

                            # Simple echo bot
                            if target.startswith("#"):
                                reply = f"PRIVMSG {target} :You said: {message}"
                                await self.send(reply, logging.DEBUG)
                            elif target == self.nickname:
                                reply = f"PRIVMSG {sender} :You PM'd me: {message}"
                                await self.send(reply, logging.DEBUG)

                                if not os.path.exists(self.dcc_send_filename):
                                        self.logger.warning(f"DCC file '{self.dcc_send_filename}' does not exist.")
                                else:
                                    await dcc_send_with_resume(sender, self.dcc_send_filename, self.send, port=random.randint(5000, 6000))

                            # Handle CTCP messages
                            if message.startswith('\x01DCC SEND'):
                                try:
                                    ctcp_data = message.strip('\x01')
                                    parts = shlex.split(ctcp_data)
                                    _, _, file_name, ip_str, port_str, size_str = parts

                                    ip_int = int(ip_str)
                                    port = int(port_str)
                                    file_size = int(size_str)

                                    self.logger.info(f"[DCC] Incoming file '{file_name}' from {sender}. Accepting...")

                                    self.pending_dcc[file_name] = {
                                        "ip_int": ip_int,
                                        "port": port,
                                        "file_size": file_size,
                                        "nick": sender
                                    }

                                    await dcc_receive_with_resume(
                                        sender,
                                        file_name,
                                        ip_int,
                                        port,
                                        file_size,
                                        self.send,
                                        save_path=file_name
                                    )

                                except Exception as e:
                                    self.logger.error(f"[DCC] Failed to parse DCC SEND: {message} | Error: {e}")

                            elif message.startswith('\x01DCC RESUME'):
                                try:
                                    ctcp_data = message.strip('\x01')
                                    parts = shlex.split(ctcp_data)
                                    _, _, file_name, port_str, pos_str = parts
                                    async with DCC_STATE_LOCK:
                                        DCC_STATE.setdefault("resumes", {})[file_name] = {
                                            "resume_pos": int(pos_str),
                                            "nick": sender
                                        }

                                    # Send ACCEPT
                                    accept_msg = f'\x01DCC ACCEPT "{file_name}" {port_str} {pos_str}\x01'
                                    await self.send(f"PRIVMSG {sender} :{accept_msg}")
                                    self.logger.info(f"[DCC] RESUME from {sender} for {file_name} at {pos_str}, sent ACCEPT.")
                                except Exception as e:
                                    self.logger.error(f"[DCC] Error parsing RESUME: {message} | {e}")

                            elif message.startswith('\x01DCC ACCEPT'):
                                try:
                                    ctcp_data = message.strip('\x01')
                                    parts = shlex.split(ctcp_data)
                                    _, _, file_name, port_str, pos_str = parts
                                    async with DCC_STATE_LOCK:
                                        DCC_STATE.setdefault("accepts", {})[file_name] = {
                                            "port": int(port_str),
                                            "pos": int(pos_str),
                                            "nick": sender
                                        }
                                    self.logger.info(f"[DCC] ACCEPT received from {sender} for {file_name}")
                                except Exception as e:
                                    self.logger.error(f"[DCC] Error parsing ACCEPT: {message} | {e}")
                    except Exception as e:
                        self.logger.error(f"[PRIVMSG Handling Error] {e}")

        except Exception as e:
            self.logger.error(f"💥 Exception in message loop: {e}")
            self.connected = False


    async def is_nickname_registered(self):
        # Send INFO command and parse replies to detect if nick is registered and verified
        await self.send(f"PRIVMSG NickServ :INFO {self.nickname}")

        verified = False
        registered = False
        timeout = 15
        start = time.time()

        while time.time() - start < timeout:
            line = await self.read_line_with_timeout(timeout=timeout - (time.time() - start))
            if not line:
                if registered and verified:
                    return True  # registered and verified means SASL can be attempted
                elif registered and not verified:
                    # Nick is registered but not verified — no SASL
                    return False
                continue

            if "VERIFIED: YES" in line.upper():
                verified = True
            if "is already registered" in line.lower():
                registered = True

        # Default fallback if no info
        return False

    async def handle_authentication(self):
        if not self.connected or not self.reader or not self.writer:
            self.logger.error("❌ Cannot authenticate: not connected to server.")
            return False
        
        try:
            await self.send(f"NICK {self.nickname}")
            await self.send(f"USER {self.nickname} 0 * :{self.nickname}")

            # Check if nickname is registered
            is_registered = False
            if self.auth_services and "nickserv" in [s.lower() for s in self.auth_services]:
                is_registered = await self.is_nickname_registered()
                if not is_registered:
                    self.logger.warning(f"📛 Nickname '{self.nickname}' is not registered. Attempting to REGISTER...")
                    service = self.auth_services[-1].lower()
                    if service == "nickserv":
                        register_cmd = f"PRIVMSG NickServ :REGISTER {self.password} {self.email}"
                    elif service == "x":
                        register_cmd = f"PRIVMSG X@channels.undernet.org :REGISTER {self.password} {self.email}"
                    elif service == "q":
                        register_cmd = f"PRIVMSG Q@CServe.quakenet.org :REGISTER {self.email} {self.password}"
                    elif service == "dalnet":
                        register_cmd = f"PRIVMSG NickServ@services.dal.net :REGISTER {self.password} {self.email}"
                    else:
                        register_cmd = None

                    if register_cmd:
                        await self.send(register_cmd, logging.INFO)
                        await asyncio.sleep(10)
                else:
                    self.logger.info(f"✅ Nickname '{self.nickname}' is already registered.")

            # ---- CAP LS ----
            await self.send("CAP LS 302")
            cap_lines = []
            cap_timeout = 15
            cap_start = time.time()

            while time.time() - cap_start < cap_timeout:
                line = await self.read_line_with_timeout(timeout=cap_timeout - (time.time() - cap_start))
                if not line:
                    continue
                self.logger.debug(f"<< {line}")
                if "CAP" in line and " LS " in line:
                    cap_lines.append(line)
                    if "CAP * LS" not in line:
                        break  # last line

                # Respond to server PINGs to stay connected
                if line.startswith("PING"):
                    pong_response = line.replace("PING", "PONG", 1)
                    await self.send(pong_response, logging.DEBUG)
                    continue

                if " 001 " in line:
                    self.logger.info("✅ Logged in (001 welcome).")
                    self.authenticated = True
                    break

            cap_str = " ".join(cap_lines).lower()
            self.sasl_supported = "sasl" in cap_str

            if self.sasl_supported and is_registered:
                self.logger.info("✅ Server supports SASL. Requesting...")
                await self.send("CAP REQ :sasl")

                # Wait for ACK or NAK
                ack_timeout = 10
                ack_start = time.time()
                while time.time() - ack_start < ack_timeout:
                    line = await self.read_line_with_timeout(timeout=ack_timeout - (time.time() - ack_start))
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if "CAP" in line and "ACK" in line and "sasl" in line.lower():
                        await self.send("AUTHENTICATE PLAIN")
                        break
                    elif "CAP" in line and "NAK" in line and "sasl" in line.lower():
                        self.logger.warning("❌ Server NAK'd SASL request.")
                        self.sasl_supported = False
                        break

            # ---- SASL Flow ----
            if self.sasl_supported:
                sasl_start = time.time()
                while time.time() - sasl_start < 30:
                    line = await self.read_line_with_timeout(timeout=5)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")

                    if line.startswith("PING"):
                        await self.send(f"PONG :{line.split(':', 1)[1]}")
                        continue

                    if "AUTHENTICATE +" in line:
                        auth_str = base64.b64encode(f"\0{self.nickname}\0{self.password}".encode()).decode()
                        await self.send(f"AUTHENTICATE {auth_str}")
                        continue

                    if any(code in line for code in ["903", "900"]):  # success
                        self.logger.info("✅ SASL authentication successful.")
                        self.authenticated = True
                        break

                    if any(code in line for code in ["904", "905", "906", "907"]):
                        self.logger.warning(f"❌ SASL authentication failed. Line: {line}")
                        break

                    if " 001 " in line:
                        self.logger.info("✅ Logged in (001 welcome).")
                        self.authenticated = True
                        break

            # ---- IDENTIFY Fallback ----
            if not self.authenticated and "nickserv" in [s.lower() for s in self.auth_services]:
                identify_cmd = f"PRIVMSG NickServ :IDENTIFY {self.password}"
                await self.send(identify_cmd, logging.INFO)
                await asyncio.sleep(5)
                for _ in range(5):
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    lowered = line.lower()
                    if any(k in lowered for k in ["identified", "already logged in", "successfully", "accepted"]):
                        self.logger.info("✅ IDENTIFY succeeded.")
                        self.authenticated = True
                        break
                    elif any(k in lowered for k in ["invalid password", "wrong password", "password incorrect"]):
                        self.logger.warning("❌ IDENTIFY failed.")
                        break

            if not self.authenticated and "dalnet" in [s.lower() for s in self.auth_services]:
                identify_cmd = f"PRIVMSG NickServ@services.dal.net :IDENTIFY {self.password}"
                await self.send(identify_cmd, logging.INFO)
                await asyncio.sleep(5)
                for _ in range(5):
                    line = await self.read_line_with_timeout(timeout=10)
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    lowered = line.lower()
                    if any(k in lowered for k in ["identified", "already logged in", "successfully", "accepted"]):
                        self.logger.info("✅ IDENTIFY succeeded.")
                        self.authenticated = True
                        break
                    elif any(k in lowered for k in ["invalid password", "wrong password", "password incorrect"]):
                        self.logger.warning("❌ IDENTIFY failed.")
                        break

            # ---- Final Wait for 001 if Needed ----
            if not self.authenticated:
                login_timeout = 30
                login_start = time.time()
                while time.time() - login_start < login_timeout:
                    line = await self.read_line_with_timeout(timeout=login_timeout - (time.time() - login_start))
                    if not line:
                        continue
                    self.logger.debug(f"<< {line}")
                    if " 001 " in line:
                        self.logger.info("✅ Logged in (001 welcome).")
                        self.authenticated = True
                        break

            # Always CAP END last
            if self.connected and not self.writer.is_closing():
                await self.send("CAP END")

            return self.authenticated

        except Exception as e:
            self.logger.exception(f"Exception during authentication: {e}")
            return False


    async def run(self):
        """The main execution method for the IRC client."""
        try:
            while True:
                self.connected = False
                self.authenticated = False
                self.joined_channel = False

                if not await self.connect():
                    self.logger.error(f"Connection failed. Retrying in {self.reconnect_delay}s.")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 2, 300)
                    continue

                self.reconnect_delay = 10

                if not await self.handle_authentication():
                    self.logger.warning("Failed to authenticate. Disconnecting.")
                    await self.disconnect()
                    await asyncio.sleep(self.reconnect_delay)
                    continue

                if self.authenticated:
                    await self.send(f"JOIN {self.channel_name}", logging.INFO)
                    self.joined_channel = True
                    await self.handle_message_loop()

                await self.disconnect()
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 300)
        except Exception as e:
            self.logger.exception(f"❌ Unhandled exception in run(): {e}")

async def status_reporter(clients, interval):
    """Periodically reports the status of all IRC clients."""
    while True:
        await asyncio.sleep(interval)
        print("\n" + "="*65)
        print("📈 IRC Server Status Report")
        print("="*65)
        for client in clients:
            status = "✅ Connected" if client.connected else "❌ Disconnected"
            auth_status = "✅ Authenticated" if client.authenticated else "❌ Not Authenticated"
            print(f"| {client.net_name:<15} | {status:<18} | {auth_status:<20} |")
        print("="*65 + "\n")

async def run_client_with_error_handling(client):
    try:
        await client.run()
    except Exception as e:
        client.logger.exception(f"❌ Client crashed: {e}")

async def shutdown(clients, tasks):
    for client in clients:
        await client.disconnect()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def ip_to_int(ip):
    return struct.unpack("!I", socket.inet_aton(ip))[0]


def int_to_ip(ip_int):
    return socket.inet_ntoa(struct.pack("!I", ip_int))


async def dcc_receive_with_resume(nick, file_name, ip_int, port, file_size, send_func, save_path=None):
    ip = int_to_ip(ip_int)
    save_path = save_path or file_name
    resume_position = 0

    if os.path.exists(save_path):
        resume_position = os.path.getsize(save_path)
        if resume_position >= file_size:
            print(f"[DCC] {file_name} already downloaded.")
            return

        # Send RESUME
        resume_msg = f'\x01DCC RESUME "{file_name}" {port} {resume_position}\x01'
        await send_func(f"PRIVMSG {nick} :{resume_msg}")
        print(f"[DCC] Sent RESUME {resume_position} to {nick}")

        # Wait for ACCEPT
        for _ in range(10):
            await asyncio.sleep(1)
            async with DCC_STATE_LOCK:
                if file_name in DCC_STATE["accepts"]:
                    DCC_STATE["accepts"].pop(file_name, None)
                    break
        else:
            resume_position = 0  # No ACCEPT, start from zero

    reader, writer = await asyncio.open_connection(ip, port)
    mode = 'ab' if resume_position else 'wb'
    total = resume_position

    with open(save_path, mode) as f:
        while total < file_size:
            chunk = await reader.read(1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)

    writer.close()
    await writer.wait_closed()
    print(f"[DCC] Received {file_name} to {save_path}")



async def dcc_send_with_resume(nick, file_path, send_func, bind_ip='0.0.0.0', port=5001):
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    public_ip = requests.get("https://api.ipify.org").text
    ip_int = ip_to_int(public_ip)

    resume_position = 0
    resume_event = asyncio.Event()

    async def wait_for_resume_accept():
        nonlocal resume_position
        for _ in range(10):
            await asyncio.sleep(1)
            async with DCC_STATE_LOCK:
                resume_info = DCC_STATE['resumes'].get(file_name)
                if resume_info and resume_info["nick"] == nick:
                    resume_position = resume_info["resume_pos"]
                    DCC_STATE['resumes'].pop(file_name, None)
                    resume_event.set()
                    break

    async def file_server(reader, writer):
        with open(file_path, 'rb') as f:
            if resume_position:
                f.seek(resume_position)
            while chunk := f.read(1024):
                writer.write(chunk)
                await writer.drain()
        writer.close()
        await writer.wait_closed()

    # Send DCC SEND
    dcc_message = f'\x01DCC SEND "{file_name}" {ip_int} {port} {file_size}\x01'
    await send_func(f"PRIVMSG {nick} :{dcc_message}", logging.DEBUG)

    await wait_for_resume_accept()

    try:
        server = await asyncio.start_server(file_server, bind_ip, port)
        async with server:
            await asyncio.wait_for(server.serve_forever(), timeout=60)
    except asyncio.TimeoutError:
        print(f"[DCC] Timeout: {nick} did not connect to receive {file_name}.")












async def main():
    nickname = "user_"
    password = "pass"
    email = "user@example.com"
    channel_name = "#echochannel"
    dcc_send_filename = "irc.py"

    irc_networks = {
        # Common major networks
        "Libera.Chat":      [("irc.libera.chat", 6697), "SASL", "NickServ"],
        "OFTC":             [("irc.oftc.net", 6697), "NickServ"],
        "Undernet":         [("irc.undernet.org", 6667), "X"],
        #"EFnet":            [("irc.efnet.org", 6697), "NickServ"],
        "DALnet":           [("irc.dal.net", 6697), "DALnet"],
        #"Rizon":            [("irc.rizon.net", 6697), "NickServ"],
        "QuakeNet":         [("irc.quakenet.org", 6667), "Q"],
        #"Snoonet":          [("irc.snoonet.org", 6697), "NickServ"],

        # Other well-known networks
        #"FreenodeClassic":  [("irc.freenode.net", 6697), "NickServ"],
        #"IRCNet":           [("irc.ircnet.net", 6667), None],
        "GameSurge":        [("irc.gamesurge.net", 6667), "AuthServ"],
        #"EsperNet":         [("irc.esper.net", 6697), "SASL", "NickServ"],
        #"Hackint":          [("irc.hackint.org", 6697), "NickServ"],
        #"GeekShed":         [("irc.geekshed.net", 6697), "NickServ"],
        #"SpigotMC":         [("irc.spi.gt", 6697), "NickServ"],
        #"PIRC":             [("irc.pirc.pl", 6697), "NickServ"],
        #"DarkMyst":         [("irc.darkmyst.org", 6697), "SASL", "NickServ"],
        #"SlashNET":         [("irc.slashnet.org", 6667), "NickServ"],
        #"DALnet-EU":        [("irc.eu.dal.net", 6697), "NickServ"],
        #"EFnet-US":         [("irc.efnet.org", 6697), "NickServ"],
    }
    
    clients = []
    tasks = []

    # Add the status reporter task
    for net_name, (server_info, *auth_services) in irc_networks.items():
        client = IRCClient(net_name, server_info, auth_services, nickname, password, email, channel_name, dcc_send_filename)
        clients.append(client)
        tasks.append(asyncio.create_task(run_client_with_error_handling(client)))

    tasks.append(asyncio.create_task(status_reporter(clients, 90)))
    
    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n💥 KeyboardInterrupt or Cancelled. Shutting down...")
        await shutdown(clients, tasks)
    except Exception:
        print("Unexpected exception:")
        traceback.print_exc()
        await shutdown(clients, tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program shut down.")
